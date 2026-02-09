import asyncio
import json
import logging
import os
import sys
import threading
import time

from aiohttp import web
from mcp.server.fastmcp import FastMCP

from svg_state import SvgCanvas

# Log to stderr only — stdout is reserved for MCP stdio transport
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(message)s")
log = logging.getLogger("svg-mcp")

canvas = SvgCanvas()
mcp_port = int(os.environ.get("MCP_PORT", "8766"))
mcp = FastMCP("svg-editor", host="0.0.0.0", port=mcp_port)


# ── MCP Tools ──────────────────────────────────────────────

@mcp.tool()
def list_elements() -> str:
    """List all SVG elements on the canvas with their IDs, attributes, and layer."""
    elements = [
        {
            "id": el.id,
            "tag": el.tag,
            "attrs": el.attrs,
            "text_content": el.text_content,
            "layer": el.attrs.get("data-layer", "CUT_OUTSIDE"),
        }
        for el in canvas.list_elements()
    ]
    return json.dumps({
        "canvas": {"width": canvas.width, "height": canvas.height},
        "elements": elements,
    })


@mcp.tool()
def add_element(tag: str, attrs: str, text_content: str = "", layer: str = "CUT_OUTSIDE") -> str:
    """Add a new SVG element to the canvas.

    Args:
        tag: SVG element type — rect, circle, ellipse, line, text, path, polygon, polyline.
        attrs: JSON string of SVG attributes, e.g. '{"x":"100","y":"100","width":"200","height":"150","fill":"#4a90d9","stroke":"#333","stroke-width":"2"}'.
        text_content: Text content (only for <text> elements).
        layer: Layer to assign the element to (default: CUT_OUTSIDE). Options: CUT_OUTSIDE, CUT_INSIDE, ENGRAVE, NOTES.
    """
    try:
        parsed = json.loads(attrs)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON in attrs: {e}"})
    parsed["data-layer"] = layer
    el = canvas.add_element(tag, parsed, text_content)
    return json.dumps({"id": el.id, "tag": el.tag, "attrs": el.attrs, "layer": layer})


@mcp.tool()
def update_element(element_id: str, attrs: str) -> str:
    """Update attributes of an existing SVG element by ID.

    Args:
        element_id: The element ID (e.g. "el-1").
        attrs: JSON string of attributes to set/update.
    """
    try:
        parsed = json.loads(attrs)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON in attrs: {e}"})
    el = canvas.update_element(element_id, parsed)
    if not el:
        return json.dumps({"error": f"Element '{element_id}' not found"})
    return json.dumps({"id": el.id, "tag": el.tag, "attrs": el.attrs})


@mcp.tool()
def remove_element(element_id: str) -> str:
    """Remove an SVG element by ID.

    Args:
        element_id: The element ID to remove (e.g. "el-1").
    """
    ok = canvas.remove_element(element_id)
    if not ok:
        return json.dumps({"error": f"Element '{element_id}' not found"})
    return json.dumps({"removed": True, "id": element_id})


@mcp.tool()
def get_svg() -> str:
    """Get the full SVG markup of the canvas."""
    return canvas.to_svg_markup()


@mcp.tool()
def set_canvas_size(width: int, height: int) -> str:
    """Set the canvas dimensions in mm (1 unit = 1mm).

    Args:
        width: Canvas width in mm.
        height: Canvas height in mm.
    """
    canvas.width = width
    canvas.height = height
    canvas.version += 1
    return json.dumps({"width": width, "height": height})


@mcp.tool()
def take_screenshot() -> str:
    """Request a screenshot of the current SVG canvas from the browser.
    The browser must be connected (polling) for this to work.
    Returns base64 PNG image data."""

    # Set flag and clear old data
    canvas.screenshot_data = None
    canvas.screenshot_requested = True

    # Poll for the browser to respond (up to 10 seconds)
    deadline = time.time() + 10
    while time.time() < deadline:
        if canvas.screenshot_data is not None:
            data = canvas.screenshot_data
            canvas.screenshot_data = None
            return json.dumps({"screenshot": data})
        time.sleep(0.3)

    canvas.screenshot_requested = False
    return json.dumps({"error": "Timeout waiting for browser to capture screenshot. Is the browser connected?"})


@mcp.tool()
def list_layers() -> str:
    """List all layers with their properties (name, color, visibility)."""
    return json.dumps({
        "layers": [
            {"name": l.name, "color": l.color, "stroke_dash": l.stroke_dash, "visible": l.visible}
            for l in canvas.layers
        ]
    })


@mcp.tool()
def set_layer_visibility(layer_name: str, visible: bool) -> str:
    """Show or hide a layer. Hidden layers' elements are not displayed.

    Args:
        layer_name: Layer name (e.g. "CUT_OUTSIDE", "ENGRAVE", "NOTES").
        visible: True to show, False to hide.
    """
    for l in canvas.layers:
        if l.name == layer_name:
            l.visible = visible
            canvas.version += 1
            return json.dumps({"layer": layer_name, "visible": visible})
    return json.dumps({"error": f"Layer '{layer_name}' not found"})


@mcp.tool()
def set_element_layer(element_id: str, layer_name: str) -> str:
    """Move an element to a different layer.

    Args:
        element_id: The element ID (e.g. "el-1").
        layer_name: Target layer name (e.g. "CUT_OUTSIDE", "CUT_INSIDE", "ENGRAVE", "NOTES").
    """
    el = canvas.elements.get(element_id)
    if not el:
        return json.dumps({"error": f"Element '{element_id}' not found"})
    valid_names = {l.name for l in canvas.layers}
    if layer_name not in valid_names:
        return json.dumps({"error": f"Layer '{layer_name}' not found"})
    el.attrs["data-layer"] = layer_name
    canvas.version += 1
    return json.dumps({"id": el.id, "layer": layer_name})


# ── HTTP Bridge ────────────────────────────────────────────

@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        return web.Response(headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        })
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


async def handle_get_svg(request):
    """Browser polls this to get current SVG state."""
    elements = [
        {"id": el.id, "tag": el.tag, "attrs": el.attrs, "text_content": el.text_content}
        for el in canvas.list_elements()
    ]
    layers = [
        {"name": l.name, "color": l.color, "stroke_dash": l.stroke_dash, "visible": l.visible}
        for l in canvas.layers
    ]
    return web.json_response({
        "version": canvas.version,
        "width": canvas.width,
        "height": canvas.height,
        "elements": elements,
        "layers": layers,
        "screenshot_requested": canvas.screenshot_requested,
    })


async def handle_post_svg(request):
    """Browser pushes its current SVG state."""
    data = await request.json()
    svg_markup = data.get("svg", "")
    if svg_markup:
        canvas.from_svg_markup(svg_markup)
    return web.json_response({"version": canvas.version, "status": "ok"})


async def handle_post_screenshot(request):
    """Browser posts captured screenshot data."""
    data = await request.json()
    png_data = data.get("image", "")
    if png_data:
        canvas.screenshot_data = png_data
        canvas.screenshot_requested = False
    return web.json_response({"status": "ok"})


async def handle_root(request):
    """Serve index.html."""
    html_path = os.path.join(os.path.dirname(__file__) or ".", "index.html")
    if not os.path.exists(html_path):
        return web.Response(text="index.html not found", status=404)
    with open(html_path, "r") as f:
        html = f.read()
    return web.Response(text=html, content_type="text/html")


def run_http_server(port: int):
    """Run the HTTP bridge in its own event loop on a background thread."""
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get("/", handle_root)
    app.router.add_get("/api/svg", handle_get_svg)
    app.router.add_post("/api/svg", handle_post_svg)
    app.router.add_post("/api/screenshot", handle_post_screenshot)
    # OPTIONS preflight for all routes
    app.router.add_route("OPTIONS", "/api/svg", lambda r: web.Response())
    app.router.add_route("OPTIONS", "/api/screenshot", lambda r: web.Response())

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "0.0.0.0", port)
    loop.run_until_complete(site.start())
    log.info(f"HTTP bridge running on port {port}")
    loop.run_forever()


# ── Main ───────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("HTTP_PORT", "8765"))
    mcp_port = int(os.environ.get("MCP_PORT", "8766"))

    http_thread = threading.Thread(target=run_http_server, args=(port,), daemon=True)
    http_thread.start()

    log.info(f"Starting MCP server on SSE port {mcp_port}")
    mcp.run(transport="sse")
