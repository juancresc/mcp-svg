"""SVG CNC editor server: MCP tools (SSE, port 8766) + HTTP API and web UI (port 8765).

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
from store import Store

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(message)s")
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
log = logging.getLogger("svg-mcp")

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = Path(os.environ.get("WEB_DIR", APP_DIR / "web"))
DATA_DIR = Path(os.environ.get("DATA_DIR", APP_DIR / "data"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8765"))
MCP_PORT = int(os.environ.get("MCP_PORT", "8766"))

store = Store(DATA_DIR)
# Changes on every start; lets the browser notice a restart and reload the state
INSTANCE_ID = uuid.uuid4().hex
mcp = FastMCP("svg-editor", host="0.0.0.0", port=MCP_PORT)


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
    return {"file": s["file"], "name": s["name"], "dirty": s["dirty"],
            "width_mm": d.width, "height_mm": d.height, "elements": len(d.elements),
            "layers": [l.name for l in d.layers], "can_undo": s["can_undo"], "can_redo": s["can_redo"]}


# ── MCP: document & files ──────────────────────────────────

@tool
def get_document_info() -> str:
    """Current document: file name, unsaved changes, size in mm (1 unit = 1 mm), layer names,
    element count, undo/redo availability."""
    return json.dumps(summary())


@tool
def list_documents() -> str:
    """List saved SVG documents in the data folder (paths relative to it; subfolders allowed)."""
    return json.dumps({"files": store.list_files(), "open": store.file})


@tool
def new_document(width: float = 800, height: float = 600, discard_changes: bool = False) -> str:
    """Start a new, empty, unsaved document with the default CNC layers.

    Args:
        width: Width in mm.
        height: Height in mm.
        discard_changes: Must be true if the open document has unsaved changes (they are lost).
    """
    store.new(width, height, discard_changes)
    return json.dumps(summary())


@tool
def open_document(file: str, discard_changes: bool = False) -> str:
    """Open a saved document from the data folder (e.g. "desk/desk.svg").

    Args:
        file: Path relative to the data folder; ".svg" is optional.
        discard_changes: Must be true if the open document has unsaved changes (they are lost).
    """
    store.open(file, discard_changes)
    return json.dumps(summary())


@tool
def save_document(file: str = "") -> str:
    """Save the document. Without `file` it saves to its current file; with `file` it saves
    under that name (Save As; overwrites). Files are SVG with mm units and Inkscape layers.

    Args:
        file: Optional path relative to the data folder, e.g. "desk/v2".
    """
    return json.dumps({"saved": store.save(file or None)})


@tool
def set_canvas_size(width: float, height: float) -> str:
    """Set the document size in mm (1 unit = 1 mm)."""
    store.apply([{"op": "set_size", "width": width, "height": height}])
    return json.dumps({"width_mm": store.doc.width, "height_mm": store.doc.height})


@tool
def export_cnc(file: str = "") -> str:
    """Write a CNC-ready SVG (mm units; only layers that are visible AND marked for export,
    so NOTES is left out) to data/exports/. Returns the path.

    Args:
        file: Optional file name; defaults to "<document>-cnc".
    """
    return json.dumps({"exported": store.export_cnc(file or None)})


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
                       "elements": [{"id": e.id, "tag": e.tag, "layer": e.layer, "attrs": e.attrs,
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
    added = store.import_svg(markup, layer or None)
    return json.dumps({"added": added, "elements": len(store.doc.elements)})


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
    """The document as SVG, in the same format as saved files (mm units, Inkscape layers)."""
    return store.doc.to_svg("file")


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
              description: str = "") -> str:
    """Create a layer.

    Args:
        name: 1-40 chars: letters, digits, space, _ - .
        color: Stroke colour #rrggbb (CAM software often maps colours to operations).
        line_style: solid, dashed, dotted, or a dasharray like "4 2".
        export: Include in CNC export (false for reference/notes layers).
        description: What the layer is for, e.g. "Pocket 6 mm deep".
    """
    store.apply([{"op": "add_layer", "name": name, "color": color, "line_style": line_style,
                  "export": export, "description": description}])
    return list_layers()


@tool
def update_layer(name: str, new_name: str = "", color: str = "", line_style: str = "",
                 visible: bool | None = None, locked: bool | None = None,
                 export: bool | None = None, description: str | None = None) -> str:
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
    """
    only_visibility = visible is not None and not (new_name or color or line_style) \
        and locked is None and export is None and description is None
    if only_visibility:
        store.apply([{"op": "set_layer_visibility", "name": name, "visible": visible}])
    else:
        store.apply([{"op": "update_layer", "name": name, "new_name": new_name or None,
                      "color": color or None, "line_style": line_style or None, "visible": visible,
                      "locked": locked, "export": export, "description": description}])
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

@tool
def take_screenshot():
    """Image of the whole document as rendered by the editor (the editor page must be open)."""
    with store.changed:
        store.screenshot_png = None
        store.screenshot_requested = True
        store.changed.notify_all()
    deadline = time.time() + 15
    while time.time() < deadline:
        if store.screenshot_png is not None:
            png, store.screenshot_png = store.screenshot_png, None
            return Image(data=png, format="png")
        time.sleep(0.2)
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
    body = await request.json()
    results = store.apply(body["ops"], body.get("label"))
    return state_json(results=results)


def action(fn):
    @api
    async def handler(request):
        body = await request.json() if request.can_read_body else {}
        return state_json(result=fn(body))
    return handler


post_undo = action(lambda b: store.undo())
post_redo = action(lambda b: store.redo())
post_new = action(lambda b: store.new(b.get("width", 800), b.get("height", 600), b.get("discard", False)))
post_open = action(lambda b: store.open(b["file"], b.get("discard", False)))
post_save = action(lambda b: store.save(b.get("file") or None))
post_delete = action(lambda b: store.delete_file(b["file"]))
post_import = action(lambda b: store.import_svg(b["svg"], b.get("layer"), b.get("file"),
                                                b.get("replace", False), b.get("discard", False)))


@api
async def get_background(request):
    bg = store.doc.background
    return web.json_response({"href": bg["href"] if bg else None, "opacity": bg["opacity"] if bg else None},
                             headers={"Cache-Control": "no-cache"})


@api
async def get_files(request):
    return web.json_response({"files": store.list_files(), "open": store.file})


@api
async def get_export(request):
    kind = request.match_info["kind"]
    name = store.display_name()
    body, fname = (store.doc.to_svg("cnc"), f"{name}-cnc.svg") if kind == "cnc" \
        else (store.doc.to_svg("file"), f"{name}.svg")
    return web.Response(text=body, content_type="image/svg+xml",
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


@web.middleware
async def no_cache(request, handler):
    resp = await handler(request)
    if not request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def run_http_server():
    app = web.Application(middlewares=[no_cache], client_max_size=64 * 1024 ** 2)
    r = app.router
    r.add_get("/", index)
    r.add_get("/api/state", get_state)
    r.add_post("/api/ops", post_ops)
    r.add_post("/api/undo", post_undo)
    r.add_post("/api/redo", post_redo)
    r.add_post("/api/file/new", post_new)
    r.add_post("/api/file/open", post_open)
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
