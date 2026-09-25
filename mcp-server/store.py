"""The single source of truth: the open document, its file, undo history and persistence.

Both the MCP tools and the browser (HTTP) go through `Store.apply()` / file methods, so
every change is validated once, versioned, undoable and autosaved. The browser never
pushes whole documents; it sends operations and renders what the store returns.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from document import Document, DocError, SHAPE_TAGS

HISTORY_LIMIT = 200
COALESCE_SECONDS = 1.0


class Store:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.session_file = self.data_dir / ".session.json"
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.doc = Document()
        self.file: str | None = None          # path relative to data_dir, e.g. "desk/desk.svg"
        self.dirty = False
        self.saved_fingerprint = fingerprint(self.doc)   # content as last saved/opened
        self.version = 0
        self.undo_stack: list[tuple[Document, str]] = []
        self.redo_stack: list[tuple[Document, str]] = []
        self._last_coalesce: tuple[str, float] | None = None
        self._session_timer: threading.Timer | None = None
        # Screenshot handshake with the browser
        self.screenshot_requested = False
        self.screenshot_png: bytes | None = None
        self._restore_session()

    # ── state for clients ──────────────────────────────────
    def state(self, include_doc: bool = True, consume_screenshot: bool = False) -> dict:
        """Client state. The reference image is sent separately (GET /api/background) and only
        identified here by a hash, so large images don't travel with every change.
        consume_screenshot: this response delivers a pending screenshot request to a browser."""
        with self.lock:
            s = {
                "version": self.version,
                "file": self.file,
                "name": self.display_name(),
                "dirty": self.dirty,
                "can_undo": bool(self.undo_stack),
                "can_redo": bool(self.redo_stack),
                "undo_label": self.undo_stack[-1][1] if self.undo_stack else None,
                "redo_label": self.redo_stack[-1][1] if self.redo_stack else None,
                "screenshot_requested": self.screenshot_requested,
            }
            if consume_screenshot:
                self.screenshot_requested = False
            if include_doc:
                doc = self.doc.to_json()
                if doc["background"]:
                    bg = doc["background"]
                    doc["background"] = {"opacity": bg.get("opacity", 0.3),
                                         "id": hashlib.sha1(bg["href"].encode()).hexdigest()[:16]}
                s["doc"] = doc
            return s

    def display_name(self) -> str:
        return Path(self.file).stem if self.file else "Untitled"

    def wait_for_change(self, since: int, timeout: float) -> bool:
        """Block until version != since or a screenshot is requested. Returns True on change."""
        deadline = time.time() + timeout
        with self.changed:
            while self.version == since and not self.screenshot_requested:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self.changed.wait(remaining)
            return True

    def _bump(self, persist: bool = True):
        self.version += 1
        self.changed.notify_all()
        if persist:
            self._schedule_session_save()

    def _schedule_session_save(self, delay: float = 0.5):
        """Autosave at most every `delay` seconds (typing/nudging produce many ops)."""
        if self._session_timer is None:
            self._session_timer = threading.Timer(delay, self.flush_session)
            self._session_timer.daemon = True
            self._session_timer.start()

    def flush_session(self):
        with self.lock:
            self._session_timer = None
            self._save_session()

    # ── mutations ──────────────────────────────────────────
    def apply(self, ops: list[dict], label: str | None = None) -> list:
        """Apply a batch of operations atomically, as one undo step. Returns per-op results."""
        if not isinstance(ops, list) or not ops or not all(isinstance(op, dict) for op in ops):
            raise DocError("ops must be a non-empty list of objects")
        with self.lock:
            before = self.doc.clone()
            work = self.doc.clone()
            results = [apply_op(work, op) for op in ops]
            history = any(op.get("op") not in NO_HISTORY_OPS for op in ops)
            if history:
                label = label or describe(ops)
                key = coalesce_key(ops)
                now = time.time()
                if key and self._last_coalesce and self._last_coalesce[0] == key \
                        and now - self._last_coalesce[1] < COALESCE_SECONDS and self.undo_stack:
                    pass  # merge into the previous undo step (e.g. typing in a property field)
                else:
                    self.undo_stack.append((before, label))
                    del self.undo_stack[:-HISTORY_LIMIT]
                self._last_coalesce = (key, now) if key else None
                self.redo_stack.clear()
            self.doc = work
            self.dirty = fingerprint(work) != self.saved_fingerprint
            self._bump()
            return results

    def undo(self) -> str:
        with self.lock:
            if not self.undo_stack:
                raise DocError("Nothing to undo")
            doc, label = self.undo_stack.pop()
            self.redo_stack.append((self.doc, label))
            self.doc = keep_view_state(doc, self.doc)
            self.dirty = fingerprint(self.doc) != self.saved_fingerprint
            self._last_coalesce = None
            self._bump()
            return label

    def redo(self) -> str:
        with self.lock:
            if not self.redo_stack:
                raise DocError("Nothing to redo")
            doc, label = self.redo_stack.pop()
            self.undo_stack.append((self.doc, label))
            self.doc = keep_view_state(doc, self.doc)
            self.dirty = fingerprint(self.doc) != self.saved_fingerprint
            self._last_coalesce = None
            self._bump()
            return label

    def _reset(self, doc: Document, file: str | None, dirty: bool):
        self.doc, self.file, self.dirty = doc, file, dirty
        self.saved_fingerprint = None if dirty else fingerprint(doc)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._last_coalesce = None
        self._bump()

    # ── files ──────────────────────────────────────────────
    def resolve(self, name: str) -> Path:
        """Map a user-supplied name to a .svg path inside data_dir (subfolders allowed)."""
        name = (name or "").strip().replace("\\", "/")
        if not name:
            raise DocError("File name is empty")
        if name.startswith("/"):
            raise DocError("Use a path relative to the data folder, e.g. 'desk/v2'")
        if not name.lower().endswith(".svg"):
            name += ".svg"
        parts = name.split("/")
        if any(p in ("", ".", "..") or p.startswith(".") or not re.fullmatch(r"[\w \-.()]+", p) for p in parts):
            raise DocError(f"Invalid file name '{name}'")
        path = (self.data_dir / name).resolve()
        if self.data_dir.resolve() not in path.parents:
            raise DocError(f"Invalid file name '{name}'")
        return path

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.data_dir.resolve()).as_posix()

    def list_files(self) -> list[dict]:
        out = []
        for p in sorted(self.data_dir.rglob("*.svg")):
            if any(part.startswith(".") for part in p.relative_to(self.data_dir).parts):
                continue
            st = p.stat()
            out.append({"file": self.rel(p), "size": st.st_size, "modified": st.st_mtime})
        return out

    def _check_discard(self, discard: bool):
        if self.dirty and not discard:
            raise DocError(f"'{self.display_name()}' has unsaved changes. Save it first, or pass "
                           f"discard_changes=true to drop them.")

    def new(self, width: float = 800, height: float = 600, discard: bool = False):
        with self.lock:
            self._check_discard(discard)
            doc = Document()
            doc.set_size(width, height)
            self._reset(doc, None, False)

    def open(self, name: str, discard: bool = False):
        with self.lock:
            self._check_discard(discard)
            path = self.resolve(name)
            if not path.exists():
                raise DocError(f"File '{self.rel(path)}' not found")
            doc = Document.from_svg(path.read_text(encoding="utf-8"))
            self._reset(doc, self.rel(path), False)

    def save(self, name: str | None = None, overwrite: bool = True) -> str:
        with self.lock:
            target = name or self.file
            if not target:
                raise DocError("Document has no file name yet: pass a name (Save As)")
            path = self.resolve(target)
            if name and path.exists() and not overwrite and self.rel(path) != self.file:
                raise DocError(f"'{self.rel(path)}' already exists")
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".svg.tmp")
            tmp.write_text(self.doc.to_svg("file"), encoding="utf-8")
            os.replace(tmp, path)
            self.file, self.dirty = self.rel(path), False
            self.saved_fingerprint = fingerprint(self.doc)
            self._bump()
            return self.file

    def delete_file(self, name: str):
        with self.lock:
            path = self.resolve(name)
            if not path.exists():
                raise DocError(f"File '{self.rel(path)}' not found")
            if self.rel(path) == self.file:
                raise DocError("Cannot delete the open document")
            path.unlink()

    def import_svg(self, markup: str, layer: str | None = None, name: str | None = None,
                   replace: bool = False, discard: bool = False) -> int:
        """Add elements from SVG markup (one undo step), or replace the document with it."""
        with self.lock:
            if replace:
                self._check_discard(discard)
                doc = Document.from_svg(markup)
                self._reset(doc, None, True)
                if name:
                    self.file = self.rel(self.resolve(name))
                return len(doc.elements)
            before = len(self.doc.elements)
            self.apply([{"op": "import_svg", "svg": markup, "layer": layer}], label="Import SVG")
            return len(self.doc.elements) - before

    def export_cnc(self, name: str | None = None) -> str:
        with self.lock:
            svg = self.doc.to_svg("cnc")
            stem = name or f"{self.display_name()}-cnc"
            path = self.resolve(f"exports/{stem}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(svg, encoding="utf-8")
            return self.rel(path)

    # ── session persistence (survives server restarts) ─────
    def _save_session(self):
        data = {"file": self.file, "dirty": self.dirty, "saved_fingerprint": self.saved_fingerprint,
                "doc": self.doc.to_json()}
        tmp = self.session_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.session_file)

    def _restore_session(self):
        try:
            data = json.loads(self.session_file.read_text(encoding="utf-8"))
            self.doc = Document.from_json(data["doc"])
            self.file, self.dirty = data.get("file"), bool(data.get("dirty"))
            self.saved_fingerprint = data.get("saved_fingerprint")
        except FileNotFoundError:
            pass
        except Exception as e:  # corrupt session: start clean but keep the file for inspection
            os.replace(self.session_file, self.session_file.with_suffix(".corrupt"))
            print(f"session restore failed: {e}")


# ── operations ─────────────────────────────────────────────

# View-state changes: applied and synced, but not undo steps and don't mark the file dirty
NO_HISTORY_OPS = {"set_layer_visibility"}


def apply_op(doc: Document, op: dict):
    kind = op.get("op")
    if kind == "add_element":
        return doc.add_element(op["tag"], op.get("attrs") or {}, op.get("text", ""), op.get("layer")).id
    if kind == "update_element":
        return doc.update_element(op["id"], op.get("attrs"), op.get("text"), op.get("layer")).id
    if kind == "remove_elements":
        return doc.remove_elements(op["ids"])
    if kind == "reorder_element":
        return doc.reorder_element(op["id"], op["where"])
    if kind == "add_layer":
        return doc.add_layer(op["name"], op.get("color", "#000000"), op.get("line_style", "solid"),
                             op.get("export", True), op.get("visible", True), op.get("locked", False),
                             op.get("description", "")).name
    if kind == "update_layer":
        return doc.update_layer(op["name"], op.get("new_name"), op.get("color"), op.get("line_style"),
                                op.get("visible"), op.get("locked"), op.get("export"),
                                op.get("description")).name
    if kind == "set_layer_visibility":
        return doc.update_layer(op["name"], visible=op["visible"]).name
    if kind == "remove_layer":
        return doc.remove_layer(op["name"], op.get("move_to"))
    if kind == "move_layer":
        return doc.move_layer(op["name"], int(op["index"]))
    if kind == "set_size":
        return doc.set_size(op["width"], op["height"])
    if kind == "set_background":
        href = op.get("href")
        doc.background = {"href": href, "opacity": float(op.get("opacity", 0.3))} if href else None
        return None
    if kind == "set_background_opacity":
        if doc.background:
            doc.background["opacity"] = max(0.0, min(1.0, float(op["opacity"])))
        return None
    if kind == "clear":
        n = len(doc.elements)
        doc.elements = []
        return n
    if kind == "replace_svg":
        new = Document.from_svg(op["svg"])
        doc.width, doc.height, doc.layers, doc.elements = new.width, new.height, new.layers, new.elements
        doc.background = new.background
        doc.next_id = max(doc.next_id, new.next_id)
        return len(doc.elements)
    if kind == "import_svg":
        new = Document.from_svg(op["svg"], default_layer=op.get("layer"), base=doc)
        doc.layers, doc.elements, doc.next_id = new.layers, new.elements, new.next_id
        return None
    raise DocError(f"Unknown op '{kind}'")


def describe(ops: list[dict]) -> str:
    names = {"add_element": "Add", "update_element": "Edit", "remove_elements": "Delete",
             "reorder_element": "Reorder", "add_layer": "Add layer", "update_layer": "Edit layer",
             "remove_layer": "Delete layer", "move_layer": "Move layer", "set_size": "Resize document",
             "set_background": "Background", "set_background_opacity": "Background opacity",
             "clear": "Clear", "replace_svg": "Edit code", "import_svg": "Import SVG"}
    kinds = {op.get("op") for op in ops}
    if len(kinds) == 1:
        k = kinds.pop()
        if k == "update_element" and len(ops) > 1:
            return f"Move {len(ops)} elements"
        return names.get(k, k)
    return "Edit"


def coalesce_key(ops: list[dict]) -> str | None:
    """Rapid repeated edits of the same thing (typing in a field) merge into one undo step."""
    if len(ops) == 1 and ops[0].get("op") in ("update_element", "update_layer", "set_background_opacity"):
        op = ops[0]
        return f'{op["op"]}:{op.get("id") or op.get("name")}:{",".join(sorted((op.get("attrs") or {}).keys()))}' \
               f':{op.get("text") is not None}:{op.get("coalesce", "")}'
    return None


def fingerprint(doc: Document) -> str:
    """Content hash for unsaved-change tracking; layer visibility is view state, not content."""
    d = doc.to_json()
    d.pop("next_id", None)
    for l in d["layers"]:
        l.pop("visible", None)
    return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()


def keep_view_state(doc: Document, current: Document) -> Document:
    """Undo/redo restore content, not what the user is currently looking at (visibility)."""
    vis = {l.name: l.visible for l in current.layers}
    for l in doc.layers:
        if l.name in vis:
            l.visible = vis[l.name]
    return doc
