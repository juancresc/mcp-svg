"""Kerf server: MCP tools (SSE, port 8766) + HTTP API and web UI (port 8765).

Both front ends are thin wrappers over one `Store` (see store.py), which owns the document.
"""
import asyncio
import base64
import functools
import json
import logging
import mimetypes
import os
import sys
import threading
import time
import uuid
from pathlib import Path

from aiohttp import web
from mcp.server.fastmcp import FastMCP, Image

from document import DocError, LINE_STYLES
from export import cnc_dxf, dxf_to_svg, parts_zip, part_files, bbox, parse_transform
from store import Store

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(message)s")
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
log = logging.getLogger("kerf")

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = Path(os.environ.get("WEB_DIR", APP_DIR / "web"))
DATA_DIR = Path(os.environ.get("DATA_DIR", APP_DIR / "data"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8765"))
MCP_PORT = int(os.environ.get("MCP_PORT", "8766"))

store = Store(DATA_DIR)
# Changes on every start; lets the browser notice a restart and reload the state
INSTANCE_ID = uuid.uuid4().hex
INSTRUCTIONS = """Kerf — a CNC design editor shared with the user (they see every change live at http://localhost:8765).
- Units: 1 SVG unit = 1 mm. Stroke colour/line style come from the element's LAYER, never pass them.
- Layers: CUT_OUTSIDE = part outlines, CUT_INSIDE = holes/slots/windows, ENGRAVE = partial depth,
  NOTES = labels/dimensions (never cut), HARDWARE = bought parts for the 3D preview (never cut).
- Entities (groups) = parts: group each part's outline + holes (group_elements), set qty and a 3D
  placement (update_group) so export_parts and the 3D preview work.
- Several documents can be open (tabs); all tools act on the active tab (list_tabs / switch_tab).
- get_selection tells you what the user selected ("this part"); set_selection highlights for them.
- Check your work with take_screenshot (view "2d" or "3d"). Prefer add_svg for many shapes (one undo).
- Never discard the user's unsaved work or close their tabs without asking."""
try:
    from mcp.server.transport_security import TransportSecuritySettings
    _security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"localhost:{MCP_PORT}", f"127.0.0.1:{MCP_PORT}", "localhost", "127.0.0.1"],
        allowed_origins=[f"http://localhost:{MCP_PORT}", f"http://127.0.0.1:{MCP_PORT}"])
    mcp = FastMCP("kerf", host="0.0.0.0", port=MCP_PORT, instructions=INSTRUCTIONS, transport_security=_security)
except ImportError:  # older mcp without transport security settings
    mcp = FastMCP("kerf", host="0.0.0.0", port=MCP_PORT, instructions=INSTRUCTIONS)


def tool(fn):
    """Register an MCP tool; DocErrors become {"error": ...} results instead of exceptions."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DocError as e:
            return json.dumps({"error": str(e)})
        except KeyError as e:
            return json.dumps({"error": f"Missing field {e}"})
    return mcp.tool()(wrapper)


def parse_json(value, what: str):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError as e:
        raise DocError(f"Invalid JSON in {what}: {e}")


def ids_arg(value: str) -> list[str]:
    return [i.strip() for i in value.split(",") if i.strip()]


def summary() -> dict:
    s = store.state(include_doc=False)
    d = store.doc
    return {"tab": s["active"], "file": s["file"], "name": s["name"], "title": d.title, "dirty": s["dirty"],
            "width_mm": d.width, "height_mm": d.height, "elements": len(d.elements),
            "entities": len(d.groups), "layers": [l.name for l in d.layers], "material": d.material,
            "can_undo": s["can_undo"], "can_redo": s["can_redo"], "open_tabs": store.list_tabs()}


# ── MCP: document & files ──────────────────────────────────

@tool
def get_document_info() -> str:
    """Current document: project name (title) and file name, unsaved changes, size in mm (1 unit = 1 mm), layer names,
    element count, undo/redo availability."""
    return json.dumps(summary())


@tool
def list_documents() -> str:
    """Projects (.kerf) and importable drawings (.svg, .dxf) in the data folder."""
    return json.dumps({"files": store.list_files(), "open": store.file})


@tool
def new_document(width: float = 800, height: float = 600) -> str:
    """Open a new, empty document (default CNC layers) in a new tab and make it active.

    Args:
        width: Width in mm.
        height: Height in mm.
    """
    store.new(width, height)
    return json.dumps(summary())


@tool
def open_document(file: str) -> str:
    """Open a project from the data folder in a new tab (or switch to its tab if already open).
    Projects are .kerf files ("desk/desk" finds desk/desk.kerf). An .svg or .dxf opens as a
    new unsaved tab (saving it creates a .kerf).

    Args:
        file: Path relative to the data folder; the extension is optional for projects.
    """
    store.open(file)
    return json.dumps(summary())


@tool
def list_tabs() -> str:
    """Open documents (tabs): id, name, file, unsaved changes, which one is active.
    All other tools work on the active tab."""
    return json.dumps({"tabs": store.list_tabs()})


@tool
def switch_tab(tab: str) -> str:
    """Make another open document (tab id from list_tabs) the active one."""
    store.activate(tab)
    return json.dumps(summary())


@tool
def close_document(tab: str = "", discard_changes: bool = False) -> str:
    """Close a tab (default: the active one). Refuses if it has unsaved changes unless
    discard_changes=true — ask the user before discarding their work."""
    store.close(tab or None, discard_changes)
    return json.dumps(summary())


@tool
def save_document(file: str = "") -> str:
    """Save the project (.kerf: layers, entities, 3D placements, parameters, material,
    reference image). Without `file` it saves to its current file; with `file` it saves under
    that name (Save As; overwrites). Use export_svg / export_cnc for SVG and DXF.

    Args:
        file: Optional path relative to the data folder, e.g. "desk/v2" → desk/v2.kerf.
    """
    return json.dumps({"saved": store.save(file or None)})


@tool
def set_canvas_size(width: float, height: float) -> str:
    """Set the document size in mm (1 unit = 1 mm)."""
    store.apply([{"op": "set_size", "width": width, "height": height}])
    return json.dumps({"width_mm": store.doc.width, "height_mm": store.doc.height})


@tool
def export_cnc(file: str = "", format: str = "svg") -> str:
    """Write a CNC-ready file to data/exports/ (mm units; only layers that are visible AND
    marked for export, so NOTES is left out). Returns the path.

    Args:
        file: Optional file name; defaults to "<document>-cnc".
        format: "svg" or "dxf" (DXF R2010: closed polylines with true arcs, circles, one DXF
                layer per document layer).
    """
    if format not in ("svg", "dxf"):
        raise DocError("format must be 'svg' or 'dxf'")
    return json.dumps({"exported": store.export_cnc(file or None, format)})


@tool
def export_svg(file: str = "") -> str:
    """Write the whole document as SVG (all layers, mm units, entities kept as metadata) to
    data/exports/ — for Inkscape or other software. Returns the path."""
    return json.dumps({"exported": store.export_svg(file or None)})


@tool
def set_project_name(name: str) -> str:
    """Name the project (shown on its tab, saved in the file). Independent of the file name:
    renaming does not move or rename the file. Empty = show the file name again.

    Args:
        name: The project name, e.g. "Standing desk v4".
    """
    store.apply([{"op": "set_title", "title": name}])
    return json.dumps(summary())


@tool
def set_material(name: str = "", type: str = "", thickness: float = 0, color: str = "",
                 sheet_width: float = 0, sheet_height: float = 0, tool_diameter: float = 0,
                 notes: str | None = None) -> str:
    """Material & stock for the project. Only given fields change.

    Args:
        name: e.g. "Birch plywood", "EVA foam", "Acrylic", "Aluminium 5754".
        type: plywood | wood | board | plastic | metal | foam | other.
        thickness: mm — default part thickness (3D preview, exports).
        color: #rrggbb used by the 3D preview.
        sheet_width / sheet_height: stock sheet size, mm.
        tool_diameter: end mill diameter, mm.
    """
    m = {k: v for k, v in (("name", name), ("type", type), ("thickness", thickness), ("color", color),
                           ("sheet_width", sheet_width), ("sheet_height", sheet_height),
                           ("tool_diameter", tool_diameter)) if v}
    if notes is not None:
        m["notes"] = notes
    store.apply([{"op": "set_material", "material": m}])
    return json.dumps({"material": store.doc.material})


@tool
def export_parts() -> str:
    """One file per entity (top-level group) for nesting software: '<name>_x<qty>.svg' and
    '.dxf', cut layers only, each moved to the origin. Written to data/exports/<doc>-parts/
    plus a zip. Group each part first (group_elements) and set qty with update_group."""
    with store.lock:
        files = part_files(store.doc)
        folder = store.resolve(f"exports/{store.display_name()}-parts", ext="")
        folder.mkdir(parents=True, exist_ok=True)
        for name, data in files:
            (folder / name).write_bytes(data)
        zpath = store.resolve(f"exports/{store.display_name()}-parts", ext=".zip")
        zpath.write_bytes(parts_zip(store.doc))
    return json.dumps({"folder": store.rel(folder), "zip": store.rel(zpath), "files": [n for n, _ in files]})


@tool
def import_dxf(file: str, new_tab: bool = True) -> str:
    """Import a DXF from the data folder (converted to mm, layers and colours kept, Y flipped).
    By default it opens as a new unsaved tab; new_tab=false adds it to the active document.

    Args:
        file: Path relative to the data folder, e.g. "desk/For CNC.dxf".
    """
    path = store.resolve(file, ext=".dxf" if not file.lower().endswith(".dxf") else "")
    if not path.is_file():
        raise DocError(f"'{file}' not found in the data folder")
    svg = dxf_to_svg(path.read_bytes())
    n = store.import_svg(svg, new_tab=new_tab, name=path.stem)
    return json.dumps({"imported": n, **summary()})


@tool
def undo() -> str:
    """Undo the last change (made by the user or by Claude)."""
    return json.dumps({"undone": store.undo(), **summary()})


@tool
def redo() -> str:
    """Redo the last undone change."""
    return json.dumps({"redone": store.redo(), **summary()})


# ── MCP: elements ──────────────────────────────────────────

@tool
def list_elements(layer: str = "") -> str:
    """List elements with id, tag, layer and attributes. Stroke colour and line style are not
    element attributes: they come from the element's layer.

    Args:
        layer: Optional layer name to filter by.
    """
    d = store.doc
    els = [e for e in d.elements if not layer or e.layer == layer]
    return json.dumps({"width_mm": d.width, "height_mm": d.height,
                       "elements": [{"id": e.id, "tag": e.tag, "layer": e.layer, "group": e.group, "attrs": e.attrs,
                                     **({"text": e.text} if e.tag == "text" else {})} for e in els]})


@tool
def add_element(tag: str, attrs: str, text_content: str = "", layer: str = "") -> str:
    """Add one element; coordinates in mm. Stroke colour/line style come from the layer.
    For many elements use add_svg (one call, one undo step).

    Args:
        tag: line, rect, circle, ellipse, text, path, polygon or polyline.
        attrs: JSON object of SVG attributes, e.g. '{"x":10,"y":10,"width":200,"height":100}'.
        text_content: Text for <text> elements.
        layer: Layer name (default: first layer). See list_layers.
    """
    [eid] = store.apply([{"op": "add_element", "tag": tag, "attrs": parse_json(attrs, "attrs"),
                          "text": text_content, "layer": layer or None}])
    return json.dumps({"id": eid})


@tool
def add_svg(markup: str, layer: str = "") -> str:
    """Add many elements at once from SVG markup, as ONE undo step. Accepts a fragment
    ('<path d="..."/><circle .../>') or a whole <svg>. Elements may carry data-layer="NAME";
    Inkscape layers are recognized; everything else goes to `layer`. Coordinates in mm.

    Args:
        markup: SVG markup.
        layer: Layer for elements without data-layer (default: first layer).
    """
    if layer:
        store.doc.layer(layer)
    before = {e.id for e in store.doc.elements}
    added = store.import_svg(markup, layer or None)
    new_ids = [e.id for e in store.doc.elements if e.id not in before]
    return json.dumps({"added": added, "ids": new_ids, "version": store.version})


@tool
def update_element(element_id: str, attrs: str = "{}", text_content: str | None = None,
                   layer: str = "") -> str:
    """Change an element's attributes (null or "" removes one), its text or its layer.

    Args:
        element_id: e.g. "el-12".
        attrs: JSON object of attributes to set, e.g. '{"x": 120}'.
        text_content: New text for <text> elements.
        layer: Move the element to this layer.
    """
    store.apply([{"op": "update_element", "id": element_id, "attrs": parse_json(attrs, "attrs"),
                  "text": text_content, "layer": layer or None}])
    e = store.doc.element(element_id)
    return json.dumps({"id": e.id, "tag": e.tag, "layer": e.layer, "attrs": e.attrs})


@tool
def remove_element(element_id: str) -> str:
    """Remove elements by ID; several IDs comma-separated ("el-1,el-2") in one undo step."""
    ids = ids_arg(element_id)
    store.apply([{"op": "remove_elements", "ids": ids}])
    return json.dumps({"removed": ids})


@tool
def set_element_layer(element_id: str, layer_name: str) -> str:
    """Move elements to another layer (comma-separated IDs allowed)."""
    ids = ids_arg(element_id)
    store.apply([{"op": "update_element", "id": i, "layer": layer_name} for i in ids])
    return json.dumps({"moved": ids, "layer": layer_name})


@tool
def get_svg() -> str:
    """The document as layered SVG (mm units). The reference image, if any, is left out."""
    d = store.doc.clone()
    d.background = None
    return d.to_svg("file")


# ── MCP: entities (groups) ─────────────────────────────────

@tool
def list_groups() -> str:
    """Entities (groups): id, name, parent group, quantity, 3D assembly placement, and the
    element ids inside (at any depth). Plus the document's 3D parameters (sliders)."""
    d = store.doc
    return json.dumps({"groups": [{**g.__dict__, "elements": d.descendants(g.id)} for g in d.groups],
                       "params": d.params})


@tool
def group_elements(items: str, name: str = "") -> str:
    """Group elements and/or groups into a named entity (e.g. one part = outline + holes on
    different layers). Items must share the same parent group. Returns the new group id.

    Args:
        items: Comma-separated element/group ids, e.g. "el-1,el-2,g-3".
        name: Entity name, e.g. "Lower frame A".
    """
    [gid] = store.apply([{"op": "group", "items": ids_arg(items), "name": name or None}])
    return json.dumps({"group": gid})


@tool
def ungroup(group_id: str) -> str:
    """Dissolve a group; its children move up one level."""
    store.apply([{"op": "ungroup", "id": group_id}])
    return json.dumps({"ungrouped": group_id})


@tool
def update_group(group_id: str, name: str = "", qty: int = 0, assembly: str = "") -> str:
    """Rename an entity, set how many to cut (qty), or set its 3D placement for the preview.

    Args:
        group_id: e.g. "g-2".
        name: New name.
        qty: Quantity to cut (≥ 1), used in part exports.
        assembly: JSON placing the part in 3D, or "null" to clear:
            {"matrix": [a,b,c,d,e,f],   # maps document 2D (mm) → part-local 2D profile coords
             "thickness": 18, "position": [x,y,z], "rotation": [rx,ry,rz],   # degrees, applied X,Y,Z
             "color": "#e3c592", "move": {"param": "lift", "axis": [0,1,0]}}
            World: X = width, Y = up, Z toward the viewer. The profile is extruded along +Z.
    """
    op = {"op": "update_group", "id": group_id, "name": name or None, "qty": qty or None}
    if assembly:
        op["assembly"] = parse_json(assembly, "assembly")
    store.apply([op])
    g = store.doc.group_by_id(group_id)
    return json.dumps({**g.__dict__, "version": store.version})


@tool
def set_params(params: str) -> str:
    """Set the document's 3D preview parameters (sliders), e.g.
    '[{"name":"lift","label":"Desk height","min":0,"max":450,"step":50,"display_offset":720,"unit":"mm"}]'.
    Groups whose assembly has "move": {"param": "lift", "axis": [0,1,0]} slide with it."""
    store.apply([{"op": "set_params", "params": parse_json(params, "params")}])
    return json.dumps({"params": store.doc.params})


# ── MCP: selection, editing helpers, measuring ─────────────

@tool
def get_selection() -> str:
    """What the user currently has selected in the editor (element ids, the entities they
    belong to, and the bounding box in mm). Use it for requests like "make this part wider"."""
    d, sel = store.doc, list(store.tab.selection)
    els = [d.element(i) for i in sel if any(e.id == i for e in d.elements)]
    groups = sorted({g for e in els for g in d.ancestors(e.group)} if els else set())
    b = bbox(els) if els else None
    return json.dumps({"elements": [e.id for e in els], "entities": [{"id": g, "name": d.group_by_id(g).name} for g in groups],
                       "bounds_mm": {"x": b[0], "y": b[1], "width": b[2] - b[0], "height": b[3] - b[1]} if b else None})


@tool
def set_selection(items: str) -> str:
    """Select (highlight) elements and/or entities in the user's editor, e.g. to show what you
    changed. Comma-separated ids ("el-3,g-2"); empty string clears."""
    sel = store.set_selection(ids_arg(items))
    return json.dumps({"selected": len(sel)})


@tool
def measure(items: str) -> str:
    """Bounding boxes (mm) of elements/entities: x, y, width, height, centre — and of all of
    them together. Comma-separated ids ("g-1,el-7")."""
    d = store.doc
    out, all_els = [], []
    for i in ids_arg(items):
        els = [d.element(e) for e in (d.descendants(i) if i.startswith("g-") else [i])]
        all_els += els
        b = bbox(els)
        out.append({"id": i, **_box(b)})
    return json.dumps({"items": out, "total": _box(bbox(all_els)) if all_els else None})


def _box(b):
    if not b:
        return {"empty": True}
    x0, y0, x1, y1 = b
    return {"x": round(x0, 3), "y": round(y0, 3), "width": round(x1 - x0, 3), "height": round(y1 - y0, 3),
            "cx": round((x0 + x1) / 2, 3), "cy": round((y0 + y1) / 2, 3)}


@tool
def move_elements(items: str, dx: float, dy: float) -> str:
    """Move elements/entities by (dx, dy) mm, as one undo step. Comma-separated ids.
    Coordinates are updated directly (no transforms pile up)."""
    d = store.doc
    ids = []
    for i in ids_arg(items):
        ids += d.descendants(i) if i.startswith("g-") else [d.element(i).id]
    store.apply([{"op": "update_element", "id": i, "attrs": move_attrs(d.element(i), dx, dy)} for i in ids], label="Move")
    return json.dumps({"moved": len(ids), "version": store.version})


def move_attrs(e, dx, dy) -> dict:
    """Attribute patch moving an element by (dx, dy) — same rules as the editor (geometry.js)."""
    a = e.attrs
    n = lambda k: float(a.get(k, 0) or 0)
    r = lambda v: round(v, 4)
    if a.get("transform") or e.tag in ("path", "polygon", "polyline"):
        import re as _re
        t = a.get("transform", "")
        m = _re.match(r"\s*translate\(\s*([-\d.e]+)(?:[\s,]+([-\d.e]+))?\s*\)\s*", t)
        if m:
            x, y = r(float(m.group(1)) + dx), r(float(m.group(2) or 0) + dy)
            rest = t[m.end():].strip()
            return {"transform": " ".join(p for p in (f"translate({x}, {y})" if (x or y) else "", rest) if p)}
        return {"transform": " ".join(p for p in (f"translate({r(dx)}, {r(dy)})", t.strip()) if p)}
    if e.tag == "line":
        return {"x1": r(n("x1") + dx), "y1": r(n("y1") + dy), "x2": r(n("x2") + dx), "y2": r(n("y2") + dy)}
    if e.tag in ("rect", "text"):
        return {"x": r(n("x") + dx), "y": r(n("y") + dy)}
    return {"cx": r(n("cx") + dx), "cy": r(n("cy") + dy)}


@tool
def transform_elements(items: str, transform: str) -> str:
    """Apply an SVG transform to elements/entities (prepended to their own transform), one undo
    step. Examples: "rotate(90 500 300)" (degrees about a point), "scale(-1 1) translate(-1000 0)"
    (mirror), "translate(10 0)". Comma-separated ids."""
    parse_transform(transform)   # validates
    return _transform(items, transform, "Transform")


def _transform(items, t, label):
    d = store.doc
    ids = []
    for i in ids_arg(items):
        ids += d.descendants(i) if i.startswith("g-") else [d.element(i).id]
    ops = [{"op": "update_element", "id": i,
            "attrs": {"transform": " ".join(x for x in (t, d.element(i).attrs.get("transform")) if x)}} for i in ids]
    store.apply(ops, label=label)
    return json.dumps({"changed": len(ids)})


@tool
def duplicate(items: str, dx: float = 10, dy: float = 10, name: str = "") -> str:
    """Copy elements/entities, offset by (dx, dy) mm. Each copied entity becomes a new entity
    (one level). Returns the new ids."""
    d = store.doc
    plan, ops = [], []
    for i in ids_arg(items):
        src = d.descendants(i) if i.startswith("g-") else [d.element(i).id]
        idx = []
        for eid in src:
            e = d.element(eid)
            t = " ".join(x for x in (f"translate({dx}, {dy})", e.attrs.get("transform")) if x)
            idx.append(len(ops))
            ops.append({"op": "add_element", "tag": e.tag, "layer": e.layer, "text": e.text,
                        "attrs": {**e.attrs, **move_attrs(e, dx, dy)}})
        plan.append((i, idx))
    n_el = len(ops)
    ops += [{"op": "group", "items": [f"${k}" for k in idx], "name": name or f"{d.group_by_id(i).name} copy"}
            for i, idx in plan if i.startswith("g-") and idx]
    res = store.apply(ops, label="Duplicate")            # one undo step
    return json.dumps({"elements": res[:n_el], "entities": res[n_el:]})


@tool
def reorder(items: str, where: str = "front") -> str:
    """Bring elements/entities to the front or send them to the back of their layer
    (where = "front" | "back")."""
    d = store.doc
    ids = [x for i in ids_arg(items) for x in (d.descendants(i) if i.startswith("g-") else [i])]
    store.apply([{"op": "reorder_element", "id": i, "where": where} for i in (ids if where == "front" else ids[::-1])])
    return json.dumps({"reordered": len(ids)})


@tool
def add_dimension(x1: float, y1: float, x2: float, y2: float, offset: float = 0, label: str = "") -> str:
    """Draw a dimension (line, end ticks, length text) on the NOTES layer, grouped as one
    entity. Coordinates in mm; offset shifts the line sideways (e.g. 15 to sit beside an edge).
    label overrides the text (default: the length in mm)."""
    import math
    L = math.hypot(x2 - x1, y2 - y1)
    if L == 0:
        raise DocError("The two points are the same")
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    nx, ny = -uy, ux
    ax, ay, bx, by = x1 + nx * offset, y1 + ny * offset, x2 + nx * offset, y2 + ny * offset
    size = max(4, min(20, L / 12))
    ang = math.degrees(math.atan2(uy, ux))
    ang = ang - 180 if ang > 90 else ang + 180 if ang < -90 else ang
    mx, my = (ax + bx) / 2, (ay + by) / 2
    r = lambda v: round(v, 3)
    layer = "NOTES" if store.doc.has_layer("NOTES") else store.doc.layers[0].name
    ops = [{"op": "add_element", "tag": "line", "layer": layer, "attrs": {"x1": r(ax), "y1": r(ay), "x2": r(bx), "y2": r(by)}}]
    for px, py in ((ax, ay), (bx, by)):
        ops.append({"op": "add_element", "tag": "line", "layer": layer,
                    "attrs": {"x1": r(px - nx * 4), "y1": r(py - ny * 4), "x2": r(px + nx * 4), "y2": r(py + ny * 4)}})
    if offset:
        for (px, py), (qx, qy) in (((x1, y1), (ax, ay)), ((x2, y2), (bx, by))):
            ops.append({"op": "add_element", "tag": "line", "layer": layer, "attrs": {"x1": r(px), "y1": r(py), "x2": r(qx), "y2": r(qy)}})
    ops.append({"op": "add_element", "tag": "text", "layer": layer, "text": label or f"{round(L, 2):g}",
                "attrs": {"x": r(mx - nx * size * 0.6), "y": r(my - ny * size * 0.6), "font-size": r(size),
                          "font-family": "sans-serif", "text-anchor": "middle",
                          **({"transform": f"rotate({r(ang)} {r(mx)} {r(my)})"} if abs(ang) > 1e-6 else {})}})
    ops.append({"op": "group", "items": [f"${k}" for k in range(len(ops))], "name": f"Dimension {round(L, 2):g}"})
    res = store.apply(ops, label="Add dimension")        # one undo step
    return json.dumps({"entity": res[-1], "length": round(L, 3)})


@tool
def clear_document() -> str:
    """Remove every element and entity from the active document (one undo step — undo restores).
    Ask the user first."""
    n = store.apply([{"op": "clear"}], label="Clear all")[0]
    return json.dumps({"removed": n})


@tool
def revert_document() -> str:
    """Reload the active document from its saved file, dropping unsaved changes (undoable).
    Ask the user first."""
    store.revert()
    return json.dumps(summary())


@tool
def list_files(folder: str = "") -> str:
    """All files in a data-folder subfolder (SVG, DXF, images, …): use it to find a DXF to
    import or an image for set_background_image. folder is relative, e.g. "desk"."""
    base = store.data_dir if not folder else store.resolve(folder, ext="")
    if not base.is_dir():
        raise DocError(f"Folder '{folder}' not found")
    out = []
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(store.data_dir)
        if p.is_file() and not any(x.startswith(".") for x in rel.parts):
            out.append({"file": rel.as_posix(), "size": p.stat().st_size})
    return json.dumps({"files": out[:500], "truncated": len(out) > 500})


# ── MCP: power tools ───────────────────────────────────────

@tool
def apply_ops(ops: str, label: str = "") -> str:
    """Run several editor operations atomically as ONE undo step (all or nothing). "$n" in
    items/id/ids/group/parent refers to the result of operation n of this batch.
    Ops: add_element{tag,attrs,text,layer,group} · update_element{id,attrs,text,layer} ·
    remove_elements{ids} · reorder_element{id,where} · group{items,name,parent} · ungroup{id} ·
    update_group{id,name,qty,assembly} · add_layer{name,color,line_style,export,description,depth} ·
    update_layer{name,...} · remove_layer{name,move_to} · set_size{width,height} ·
    set_material{material} · set_title{title} · set_params{params} · import_svg{svg,layer} · clear.
    Example — a plate with a hole, grouped, in one step:
    [{"op":"add_element","tag":"rect","layer":"CUT_OUTSIDE","attrs":{"x":0,"y":0,"width":100,"height":50}},
     {"op":"add_element","tag":"circle","layer":"CUT_INSIDE","attrs":{"cx":20,"cy":25,"r":4}},
     {"op":"group","items":["$0","$1"],"name":"Plate"}]"""
    results = store.apply(parse_json(ops, "ops"), label or None)
    return json.dumps({"results": results, "version": store.version})


@tool
def find_elements(layer: str = "", tag: str = "", entity: str = "", x: float | None = None, y: float | None = None,
                  width: float | None = None, height: float | None = None, limit: int = 200) -> str:
    """Find elements by layer, tag, entity (id or name, any depth) and/or an area (x, y, width,
    height in mm: elements whose bounds touch it). Returns ids with bounds — much smaller than
    list_elements."""
    d = store.doc
    ids = None
    if entity:
        g = next((g for g in d.groups if g.id == entity or g.name == entity), None)
        if not g:
            raise DocError(f"Entity '{entity}' not found")
        ids = set(d.descendants(g.id))
    area = (x, y, x + width, y + height) if None not in (x, y, width, height) else None
    out = []
    for e in d.elements:
        if (layer and e.layer != layer) or (tag and e.tag != tag) or (ids is not None and e.id not in ids):
            continue
        b = bbox([e])
        if area and (not b or b[2] < area[0] or b[0] > area[2] or b[3] < area[1] or b[1] > area[3]):
            continue
        out.append({"id": e.id, "tag": e.tag, "layer": e.layer, "entity": e.group, **(_box(b) if b else {})})
        if len(out) >= limit:
            break
    return json.dumps({"count": len(out), "elements": out})


@tool
def describe_entity(entity: str) -> str:
    """One part in brief: bounds, layers, outline/hole/pocket counts, hole diameters, quantity,
    3D placement, sub-entities. entity = id ("g-3") or name."""
    d = store.doc
    g = next((g for g in d.groups if g.id == entity or g.name == entity), None)
    if not g:
        raise DocError(f"Entity '{entity}' not found")
    els = [d.element(i) for i in d.descendants(g.id)]
    by_layer = {}
    for e in els:
        by_layer[e.layer] = by_layer.get(e.layer, 0) + 1
    from export import element_shapes
    holes = sorted({round(2 * s[3], 2) for e in els if d.layer(e.layer).export
                    for s in element_shapes(e, None) if s[0] == "circle"})
    return json.dumps({"id": g.id, "name": g.name, "qty": g.qty, "parent": g.parent,
                       "children": [c.id for c in d.groups if c.parent == g.id],
                       "bounds_mm": _box(bbox(els)) if els else None, "shapes_per_layer": by_layer,
                       "hole_diameters_mm": holes, "assembly": g.assembly})


@tool
def check_cnc() -> str:
    """Pre-cut checks on the active document. Reports issues with element ids (pass them to
    set_selection to show the user): open contours on cut layers, holes smaller than the tool,
    duplicate shapes (cut twice), text on cut layers, shapes outside the document, cut shapes not
    in any entity, entities without an outline, and parts that don't fit the material sheet."""
    from export import element_shapes
    d = store.doc
    tool_d = float(d.material.get("tool_diameter") or 6)
    issues = []
    add = lambda kind, ids, msg: issues.append({"kind": kind, "ids": ids, "message": msg})
    cut = {l.name for l in d.layers if l.export}
    seen = {}
    for e in d.elements:
        if e.layer not in cut:
            continue
        if e.tag == "text":
            add("text_on_cut_layer", [e.id], f"Text on {e.layer}: convert it to paths or move it to NOTES")
            continue
        try:
            shapes = element_shapes(e, None)
        except DocError as err:
            add("invalid_geometry", [e.id], str(err)); continue
        for s_ in shapes:
            if s_[0] == "contour" and not s_[1].closed and d.layer(e.layer).depth is None:
                add("open_contour", [e.id], f"Open shape on {e.layer}: through-cuts need closed outlines")
            if s_[0] == "circle" and 2 * s_[3] < tool_d:
                add("hole_smaller_than_tool", [e.id], f"Ø{2 * s_[3]:g} hole is smaller than the {tool_d:g} mm tool")
        key = (e.tag, e.layer, json.dumps(e.attrs, sort_keys=True))
        if key in seen:
            add("duplicate", [seen[key], e.id], "Identical shapes on the same layer (would be cut twice)")
        else:
            seen[key] = e.id
        b = bbox([e])
        if b and (b[0] < -0.01 or b[1] < -0.01 or b[2] > d.width + 0.01 or b[3] > d.height + 0.01):
            add("outside_document", [e.id], "Shape extends outside the document")
        if d.groups and not e.group:
            add("not_in_entity", [e.id], "Cut shape not in any entity (it won't be in part exports)")
    sw, sh = float(d.material.get("sheet_width") or 0), float(d.material.get("sheet_height") or 0)
    for g in (g for g in d.groups if not g.parent):
        els = [d.element(i) for i in d.descendants(g.id)]
        cut_els = [e for e in els if e.layer in cut]
        if els and cut_els and not any(e.layer == "CUT_OUTSIDE" for e in cut_els) and d.has_layer("CUT_OUTSIDE"):
            add("no_outline", [g.id], f"Entity '{g.name}' has no CUT_OUTSIDE outline")
        b = bbox(cut_els) if cut_els else None
        if b and sw and sh:
            w, h = b[2] - b[0], b[3] - b[1]
            if not ((w <= sw and h <= sh) or (w <= sh and h <= sw)):
                add("bigger_than_sheet", [g.id], f"Entity '{g.name}' ({w:.0f} × {h:.0f}) doesn't fit a {sw:g} × {sh:g} sheet")
    return json.dumps({"ok": not issues, "issues": issues[:200], "count": len(issues),
                       "tool_diameter": tool_d})


@mcp.prompt()
def design_part(description: str) -> str:
    """Design a CNC part in Kerf from a description."""
    return (f"Design this part in the Kerf editor: {description}\n"
            "1. get_document_info; set_material if the material/thickness is known.\n"
            "2. Draw with apply_ops (outline on CUT_OUTSIDE, holes/slots on CUT_INSIDE, pockets on a layer with depth), "
            "grouping the part in the same batch (\"$n\" references).\n"
            "3. Add key dimensions on NOTES (add_dimension). 4. check_cnc and fix issues. "
            "5. take_screenshot to verify, then save_document.")


@mcp.prompt()
def prepare_for_cutting() -> str:
    """Checklist to get the active document ready for the CNC shop."""
    return ("Prepare the active Kerf document for cutting:\n"
            "1. check_cnc and fix every issue (set_selection to show the user what you change).\n"
            "2. Every part is an entity with the right qty (describe_entity / update_group).\n"
            "3. Material, thickness, sheet size and tool diameter are set (set_material).\n"
            "4. save_document, then export_cnc(format='dxf') and export_parts; report the files.")


# ── MCP: layers ────────────────────────────────────────────

@tool
def list_layers() -> str:
    """Layers in drawing order (first = bottom) with colour, line style, visibility, lock,
    export flag, description and element count."""
    d = store.doc
    return json.dumps({"layers": [{**l.__dict__, "elements": sum(e.layer == l.name for e in d.elements)}
                                  for l in d.layers],
                       "line_styles": list(LINE_STYLES)})


@tool
def add_layer(name: str, color: str = "#000000", line_style: str = "solid", export: bool = True,
              description: str = "", depth: float = 0) -> str:
    """Create a layer.

    Args:
        name: 1-40 chars: letters, digits, space, _ - .
        color: Stroke colour #rrggbb (CAM software often maps colours to operations).
        line_style: solid, dashed, dotted, or a dasharray like "4 2".
        export: Include in CNC export (false for reference/notes layers).
        description: What the layer is for, e.g. "Pocket 6 mm deep".
        depth: Partial-depth cut from the top face in mm (pockets); 0 = through-cut.
    """
    store.apply([{"op": "add_layer", "name": name, "color": color, "line_style": line_style,
                  "export": export, "description": description, "depth": depth or None}])
    return list_layers()


@tool
def update_layer(name: str, new_name: str = "", color: str = "", line_style: str = "",
                 visible: bool | None = None, locked: bool | None = None,
                 export: bool | None = None, description: str | None = None,
                 depth: float | None = None) -> str:
    """Rename a layer or change its colour, line style, visibility, lock, export flag or
    description. Only the fields you pass change.

    Args:
        name: Current layer name.
        new_name: Rename to this.
        color: #rrggbb.
        line_style: solid, dashed, dotted, or a dasharray like "4 2".
        visible: Show/hide (not an undo step).
        locked: Locked layers can't be selected in the editor.
        export: Include in CNC export.
        description: What the layer is for.
        depth: Partial-depth cut in mm from the top face (pocket); 0 makes it a through-cut.
    """
    only_visibility = visible is not None and not (new_name or color or line_style) \
        and locked is None and export is None and description is None and depth is None
    if only_visibility:
        store.apply([{"op": "set_layer_visibility", "name": name, "visible": visible}])
    else:
        store.apply([{"op": "update_layer", "name": name, "new_name": new_name or None,
                      "color": color or None, "line_style": line_style or None, "visible": visible,
                      "locked": locked, "export": export, "description": description,
                      **({"depth": depth or None} if depth is not None else {})}])
    return list_layers()


@tool
def remove_layer(name: str, move_elements_to: str = "", delete_elements: bool = False) -> str:
    """Delete a layer. If it has elements, move them (move_elements_to=<layer>) or delete them
    (delete_elements=true)."""
    move_to = "__delete__" if delete_elements else (move_elements_to or None)
    store.apply([{"op": "remove_layer", "name": name, "move_to": move_to}])
    return list_layers()


@tool
def move_layer(name: str, index: int) -> str:
    """Change drawing order: index 0 is drawn first (bottom)."""
    store.apply([{"op": "move_layer", "name": name, "index": index}])
    return list_layers()


# ── MCP: preview & reference image ─────────────────────────

@mcp.tool()
async def take_screenshot(view: str = "2d"):
    """Image of the active document as rendered by the editor (the editor page must be open).

    Args:
        view: "2d" = the whole drawing; "3d" = the assembled 3D preview (entities with a 3D
              placement); "3d-exploded" = the assembly view with parts pulled apart. 3D views
              render in the background without changing what the user sees.
    """
    if view not in ("2d", "3d", "3d-exploded"):
        return json.dumps({"error": "view must be '2d', '3d' or '3d-exploded'"})
    with store.changed:
        store.screenshot_png = None
        store.screenshot_view = view
        store.screenshot_requested = True
        store.changed.notify_all()
    for _ in range(75):                         # up to 15 s, without blocking other tools
        await asyncio.sleep(0.2)
        with store.lock:
            png, store.screenshot_png = store.screenshot_png, None
        if png is not None:
            return Image(data=png, format="png")
    with store.lock:
        store.screenshot_requested = False
    return json.dumps({"error": "No editor answered. Open http://localhost:8765/ in a browser."})


@tool
def set_background_image(file_path: str = "", image_data: str = "", opacity: float = 0.3) -> str:
    """Show a reference image behind the drawing (saved with the document, never exported).

    Args:
        file_path: Image path relative to the data folder (e.g. "desk/1.png").
        image_data: Alternatively a data URI or raw base64 PNG.
        opacity: 0..1.
    """
    if file_path:
        path = (DATA_DIR / file_path).resolve()
        if not path.is_file() or DATA_DIR.resolve() not in path.parents:
            raise DocError(f"Image '{file_path}' not found in the data folder")
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        image_data = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"
    if not image_data:
        raise DocError("Provide file_path or image_data")
    if not image_data.startswith("data:"):
        image_data = f"data:image/png;base64,{image_data}"
    store.apply([{"op": "set_background", "href": image_data, "opacity": opacity}])
    return json.dumps({"status": "ok", "size_kb": len(image_data) // 1024})


@tool
def remove_background_image() -> str:
    """Remove the reference image."""
    store.apply([{"op": "set_background", "href": None}])
    return json.dumps({"removed": True})


# ── HTTP API ───────────────────────────────────────────────

def api(handler):
    @functools.wraps(handler)
    async def wrapper(request):
        try:
            return await handler(request)
        except DocError as e:
            return web.json_response({"error": str(e)}, status=400)
        except (KeyError, ValueError, TypeError, AttributeError) as e:
            return web.json_response({"error": f"Bad request: {e}"}, status=400)
    return wrapper


def state_json(include_doc=True, **extra):
    # Responses to the browser deliver (and so clear) a pending screenshot request
    return web.json_response({**store.state(include_doc, consume_screenshot=True), "instance": INSTANCE_ID, **extra})


@api
async def get_state(request):
    """Full state, or with ?since=<version>&instance=<id> a long-poll (up to 25 s) that returns
    as soon as the document changes or a screenshot is requested."""
    since = request.query.get("since")
    if since is not None and request.query.get("instance") == INSTANCE_ID:
        since = int(since)
        await asyncio.get_running_loop().run_in_executor(None, store.wait_for_change, since, 25.0)
        return state_json(include_doc=store.version != since)
    return state_json()


@api
async def post_ops(request):
    """Ops for a specific tab (the one the user was looking at), not whatever is active now."""
    body = await request.json()
    results = store.apply(body["ops"], body.get("label"), body.get("tab"))
    return state_json(results=results)


def action(fn):
    @api
    async def handler(request):
        body = await request.json() if request.can_read_body else {}
        return state_json(result=fn(body))
    return handler


post_undo = action(lambda b: store.undo(b.get("tab")))
post_redo = action(lambda b: store.redo(b.get("tab")))
post_new = action(lambda b: store.new(b.get("width", 800), b.get("height", 600)))
post_open = action(lambda b: store.open(b["file"]))
post_close = action(lambda b: store.close(b.get("tab"), b.get("discard", False)))
post_activate = action(lambda b: store.activate(b["tab"]))
post_revert = action(lambda b: store.revert(b.get("tab")))
post_save = action(lambda b: store.save(b.get("file") or None, b.get("tab")))
post_saved_local = action(lambda b: store.mark_saved_elsewhere(b["name"], b.get("tab")))
post_delete = action(lambda b: store.delete_file(b["file"]))
post_mkdir = action(lambda b: store.make_folder(b["folder"]))


def _import(b):
    if b.get("project"):     # a .kerf opened from the user's computer
        from document import from_native
        doc = from_native(b["project"])
        with store.lock:
            tab = store._add_tab(doc)
            tab.suggested_name = b.get("name")
            store._bump()
        return len(doc.elements)
    svg = b.get("svg")
    if b.get("dxf"):
        svg = dxf_to_svg(base64.b64decode(b["dxf"]))
    if not svg:
        raise DocError("Nothing to import")
    return store.import_svg(svg, b.get("layer"), b.get("new_tab", False), b.get("name"))


post_import = action(_import)


@api
async def get_background(request):
    bg = store.doc.background
    return web.json_response({"href": bg["href"] if bg else None, "opacity": bg["opacity"] if bg else None},
                             headers={"Cache-Control": "no-cache"})


@api
async def post_selection(request):
    body = await request.json()
    store.report_selection(body.get("ids") or [], body.get("tab"))
    return web.json_response({"ok": True})


@api
async def get_files(request):
    return web.json_response({"files": store.list_files(), "open": store.file})


@api
async def get_browse(request):
    return web.json_response(store.browse(request.query.get("folder", "")))


@api
async def get_export(request):
    kind = request.match_info["kind"]
    name = store.display_name()
    with store.lock:
        if kind == "cnc":
            body, fname, ctype = store.doc.to_svg("cnc").encode(), f"{name}-cnc.svg", "image/svg+xml"
        elif kind == "cnc-dxf":
            body, fname, ctype = cnc_dxf(store.doc), f"{name}-cnc.dxf", "application/dxf"
        elif kind == "parts":
            body, fname, ctype = parts_zip(store.doc), f"{name}-parts.zip", "application/zip"
        elif kind == "file":
            body, fname, ctype = store.doc.to_svg("file").encode(), f"{name}.svg", "image/svg+xml"
        elif kind == "project":
            from document import to_native
            body, fname, ctype = to_native(store.doc).encode(), f"{name}.kerf", "application/json"
        else:
            raise DocError(f"Unknown export '{kind}'")
    return web.Response(body=body, content_type=ctype,
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@api
async def post_screenshot(request):
    data = await request.json()
    with store.changed:
        store.screenshot_png = base64.b64decode(data["image"])
        store.screenshot_requested = False
    return web.json_response({"status": "ok"})


async def index(request):
    return web.FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


@web.middleware
async def local_only(request, handler):
    """The API edits and deletes files: only serve this machine's browser. Blocks DNS rebinding
    (Host check) and cross-site form posts (Origin + JSON content type)."""
    host = (request.host or "").rsplit(":", 1)[0]
    if host not in LOCAL_HOSTS:
        return web.json_response({"error": "Forbidden host"}, status=403)
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if origin and origin.split("://", 1)[-1].rsplit(":", 1)[0] not in LOCAL_HOSTS:
            return web.json_response({"error": "Forbidden origin"}, status=403)
        if request.content_type != "application/json":
            return web.json_response({"error": "Content-Type must be application/json"}, status=415)
    return await handler(request)


@web.middleware
async def no_cache(request, handler):
    resp = await handler(request)
    if not request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def run_http_server():
    app = web.Application(middlewares=[local_only, no_cache], client_max_size=64 * 1024 ** 2)
    r = app.router
    r.add_get("/", index)
    r.add_get("/api/state", get_state)
    r.add_post("/api/ops", post_ops)
    r.add_post("/api/undo", post_undo)
    r.add_post("/api/redo", post_redo)
    r.add_post("/api/file/new", post_new)
    r.add_post("/api/file/open", post_open)
    r.add_post("/api/file/close", post_close)
    r.add_post("/api/file/activate", post_activate)
    r.add_post("/api/file/revert", post_revert)
    r.add_post("/api/file/saved-local", post_saved_local)
    r.add_post("/api/file/mkdir", post_mkdir)
    r.add_get("/api/browse", get_browse)
    r.add_post("/api/selection", post_selection)
    r.add_post("/api/file/save", post_save)
    r.add_post("/api/file/delete", post_delete)
    r.add_post("/api/file/import", post_import)
    r.add_get("/api/files", get_files)
    r.add_get("/api/background", get_background)
    r.add_get("/api/export/{kind}", get_export)
    r.add_post("/api/screenshot", post_screenshot)
    r.add_static("/", WEB_DIR)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    loop.run_until_complete(web.TCPSite(runner, "0.0.0.0", HTTP_PORT).start())
    log.info(f"Editor on http://localhost:{HTTP_PORT}/  (data: {DATA_DIR})")
    loop.run_forever()


if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    log.info(f"MCP server (SSE) on port {MCP_PORT}")
    mcp.run(transport="sse")
