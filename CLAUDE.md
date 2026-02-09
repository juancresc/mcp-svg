# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A browser-based SVG editor implemented as a single `index.html` file (~1250 lines). No build tools, no dependencies, no package manager — just open the file in a browser.

The editor is being used to create **CNC-ready cut drawings** for a standing desk inspired by the Jaswig StandUp/Nomad. See `PROJECT.md` for desk specs, piece inventory, and reference links.

## Development

Open `index.html` directly in a browser. There are no build, lint, or test commands.

## Architecture

Everything lives in one self-contained HTML file with three sections:

- **CSS** (`<style>` block, lines 7–412): CSS custom properties in `:root` for theming. Layout uses CSS Grid for the canvas area (rulers + SVG) and flexbox elsewhere.
- **HTML** (lines 414–466): Header toolbar, main area (canvas-area with rulers + SVG + properties panel), and a collapsible code panel at the bottom.
- **JavaScript** (`<script>` block, lines 468–1246): All application logic in plain vanilla JS, no frameworks.

### Key JS Concepts

- **State object** (`state`, line 489): Single mutable object tracking current tool, drawing/dragging state, selected element, unit preference, etc.
- **SVG namespace**: All SVG elements created via `document.createElementNS(NS, tagName)` where `NS = 'http://www.w3.org/2000/svg'`.
- **ATTRS map** (line 476): Defines which attributes are editable per SVG element type. The properties panel and serialization logic both use this.
- **Selection box** (`selBox`): A hidden SVG rect (`id="_sel"`) appended to the canvas to show selection outlines. Excluded from export/save via `cleanClone()`.
- **Persistence**: `localStorage` key `svg-editor` stores the canvas innerHTML (minus selection box).
- **Code panel**: A textarea showing serialized SVG. Has a dirty flag (`state.codeDirty`) to avoid overwriting user edits. `applyCode()` parses and replaces canvas content.
- **Snippet creator**: Right panel shows an "Add Element" UI when nothing is selected. Parses user-entered SVG markup via DOMParser and appends to canvas.

### Drawing Flow

1. `mousedown` on canvas → create shape via `makeShape()`, append to SVG
2. `mousemove` → `resizeShape()` updates the in-progress element
3. `mouseup` → if shape is too small (`tooSmall()`), remove it; otherwise `bindEl()` for selection/dragging, auto-switch to select tool

### Element Interaction

- `bindEl(el)` attaches mousedown handler for select+drag on any shape element
- Dragging works differently per element type: line moves all 4 coords, rect/text move x/y, circle/ellipse move cx/cy, path/polygon/polyline use `transform="translate(...)"`
- `SHAPE_SEL` constant (`line, rect, circle, ellipse, text, path, polygon, polyline`) is the CSS selector used throughout for querying drawable elements

### Ruler System

Canvas rulers are drawn on `<canvas>` elements (not SVG). Support cm/in units toggled via `state.unit`. `drawRulerAxis()` handles both horizontal and vertical via a `dir` parameter. Red cursor indicator line tracks mouse position.

### SVG Export & Units

`cleanClone()` prepares SVG for internal use (save, MCP sync, code panel) by:
- Removing the selection box (`#_sel`)
- Stripping inline `style` attributes

`cncClone()` extends `cleanClone()` for file export by additionally:
- Adding `viewBox="0 0 W H"` to preserve the coordinate system
- Setting `width` and `height` with `mm` suffix (e.g., `width="500mm"`)

This ensures **1 SVG unit = 1mm** when imported into CAM/CNC software. This is critical — wrong units are the #1 source of CNC scale errors. The mm units are only applied on file export, not during save/sync (which would break the canvas).

## MCP Server

A Python MCP server lets Claude Code create/edit/remove SVG elements programmatically. The browser syncs with it via HTTP polling.

### Running

```bash
docker compose up --build    # starts MCP server + HTTP bridge on port 8765
```

Claude Code auto-discovers the server via `.mcp.json`. After restarting Claude Code, the `svg-editor` MCP tools become available.

### Architecture

- **`mcp-server/svg_state.py`**: Core state — `SvgElement`, `SvgCanvas` dataclasses. Handles SVG parsing (`xml.etree.ElementTree`) and serialization. Thread-safe via `threading.Lock`.
- **`mcp-server/server.py`**: Single process running MCP server (SSE via FastMCP) + HTTP bridge (aiohttp on port 8765) in a background thread. Single global `SvgCanvas` instance shared between MCP tools and HTTP API.
- **Browser sync in `index.html`**: Auto-connects to MCP server. Polls `GET /api/svg` every 1s, pushes on `save()` via `POST /api/svg`. Screenshot capture renders SVG→Canvas→PNG and POSTs to `/api/screenshot`.

### MCP Tools

No session management needed — there is a single global canvas shared between the browser and MCP tools.

| Tool | Purpose |
|------|---------|
| `list_elements` | All elements with IDs, tags, attributes |
| `add_element` | Create element (tag + JSON attrs) |
| `update_element` | Modify element attributes by ID |
| `remove_element` | Delete element by ID |
| `get_svg` | Full SVG markup |
| `set_canvas_size` | Change width/height |
| `take_screenshot` | Capture PNG from browser (requires browser connected) |

### How to Use MCP for CNC Drawings

1. **Open the editor**: User opens `index.html` in browser. It auto-connects to the MCP server at `localhost:8765`.
2. **Set canvas size**: Call `set_canvas_size` with width/height in mm (1 unit = 1mm). Size the canvas to fit the piece with some margin for dimension labels.
3. **Draw the piece geometry**: Use `add_element` to create shapes. All coordinates are in mm. Use `fill="none"` and `stroke="#000000"` for cut lines.
4. **Add bolt holes**: Use `circle` elements with the hole radius. `fill="none"`, `stroke="#000000"`.
5. **Add dimension labels**: Use `text` elements and `line` elements for annotations. These are for reference only — they need to be on a separate layer or removed before CNC cutting (TODO: layer system).
6. **Verify with screenshot**: Call `take_screenshot` to visually check the drawing.
7. **Clean up**: Use `list_elements` to review, `remove_element` to delete stray elements.

### Element IDs

All SVG shape elements get auto-assigned IDs (`el-1`, `el-2`, ...) via `ensureIds()` and in `bindEl()`. The server and browser keep the ID counter in sync. IDs are used to address elements in MCP tool calls.

### Sync Protocol

Version counter on `SvgCanvas` increments on every mutation. Browser only applies server state when server version is higher than local. `pushToMcp()` sends serialized SVG; server parses it and returns new version. Last-write-wins.

## SVG for CNC Guidelines

### Units & Scale
- Export uses mm units: `width="400mm"` + `viewBox="0 0 400 200"`
- 1 SVG unit = 1mm — do not scale inside CAM
- The `cncClone()` function handles this automatically on file export (not `cleanClone()` which is for internal save/sync)

### Paths vs Shapes
- CNC/CAM software prefers `<path>` elements over `<rect>`, `<circle>`, etc.
- TODO: Add a "CNC Export" that converts all shapes to paths
- Text must be converted to paths before cutting

### Layers (TODO)
- Currently everything is flat — dimension labels would get cut by CNC
- Need to separate cut geometry from notes/dimensions
- Target layer convention: `CUT_OUTSIDE`, `CUT_INSIDE`, `ENGRAVE`, `NOTES`
- Alternative: color convention (red=cut, blue=engrave, green=notes)

### Kerf & Tool Diameter
- SVG is geometry only — CNC cutter size is handled in CAM
- Draw nominal size, let CAM do inside/outside offsets
- Compensate manually only for press-fit joints

### Common Mistakes to Avoid
- Document in px with no units (FIXED)
- Stroke thickness interpreted as geometry
- Double overlapping lines (machine cuts twice)
- Tiny gaps in closed shapes (joints fall apart)
- Fonts not converted to paths
- Gradients, clipping masks, effects (CAM can't handle them)
