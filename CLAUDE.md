# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A browser-based SVG editor for **CNC-ready cut drawings**, plus an MCP server so Claude can draw, edit and preview the same document. 1 SVG unit = 1 mm everywhere.

It is being used to design a plywood sit/stand desk inspired by the Jaswig StandUp. The desk project lives in `data/desk/` (generator `generate.py`, spec `DESIGN.md`, reference photos, and a reference `For CNC.dxf` that is **only for a final comparison, never as a drawing source**).

## Running & testing

```bash
docker compose up -d --build        # editor at http://localhost:8765, MCP (SSE) on :8766
docker compose run --rm --no-deps -T --entrypoint python svg-mcp -m pytest -q tests   # server tests
```

- `web/` and `data/` are bind-mounted: edit the UI and reload the page, no rebuild. Python changes need `--build`.
- Rebuilding the container drops Claude Code's MCP connection; reconnect with `/mcp`.
- No build tools or JS dependencies: the UI is plain ES modules served as-is.

## Architecture

**The server is the single source of truth.** The browser never pushes whole documents; it sends operations and renders the state the server returns. This is deliberate: an earlier design where the browser and server both owned the document caused reloads to overwrite work.

```
mcp-server/
  document.py   Document model (Layer, Element), validation, SVG read/write (file / cnc modes)
  store.py      Store: open document, file name, dirty tracking, undo/redo, ops, files, session autosave
  server.py     MCP tools + HTTP API (aiohttp) — thin wrappers over Store
  tests/        pytest (store + document)
web/
  index.html, css/app.css
  js/state.js     shared client state + event bus (on/emit)
  js/api.js       HTTP client + long-poll sync (/api/state?since=<version>)
  js/geometry.js  render attrs from layer styles, doc→SVG/PNG, move/bbox helpers
  js/canvas.js    rendering, zoom/grid/rulers, tools (select/marquee/move, pan, draw)
  js/panels.js    Layers panel + Inspector (selected shape / document)
  js/actions.js   file/edit actions (new/open/save/import/export, duplicate, clear…)
  js/menu.js      menu bar, toolbar, tool palette, keyboard shortcuts
  js/main.js      wiring: server state → views, status bar, code panel, screenshots
data/             documents (*.svg), exports/, .session.json (autosave; gitignored)
```

### Document model
- `Document {width, height, layers[], elements[], background, next_id}`; elements are `{id: "el-N", tag, layer, attrs, text}`.
- **Stroke colour and line style belong to the layer** (CNC colour convention). `stroke`, `stroke-dasharray`, `data-layer`, `style`, `id` are never stored in element attrs; they are applied when rendering/serializing. Fill, geometry, transform, font-size etc. are per element.
- Layers: `name, color, line_style (solid|dashed|dotted|"a b"), visible, locked, export, description`. Defaults: CUT_OUTSIDE, CUT_INSIDE, ENGRAVE, NOTES (NOTES has `export=false`), each with a description of its CAM meaning.
- File format (`to_svg("file")`): `width="…mm" height="…mm" viewBox`, Inkscape layers (`<g inkscape:groupmode="layer" inkscape:label=…>` with `data-color/-line-style/-export/-locked/-description`), explicit stroke attrs so other software renders it. `from_svg` also reads plain SVGs (`data-layer` attrs, no viewBox → bare numbers) and flattens group transforms onto elements.
- CNC export (`to_svg("cnc")`): only layers that are `export` AND `visible`, no ids, no background.

### Store / operations
- All edits go through `Store.apply(ops)`: a batch is validated on a copy and committed atomically as **one undo step**. Ops: `add_element, update_element, remove_elements, reorder_element, add_layer, update_layer, set_layer_visibility, remove_layer, move_layer, set_size, set_background, set_background_opacity, clear, replace_svg, import_svg`.
- `set_layer_visibility` is view state: synced, but not an undo step and not "unsaved".
- Rapid single-field edits (typing, nudging) coalesce into one undo step (`coalesce_key`).
- `dirty` = content fingerprint differs from the last save/open (undoing back to the saved state is clean).
- Every change bumps `version`, notifies long-pollers and autosaves `data/.session.json`, so a server restart loses nothing (unsaved work included).
- File names are resolved inside `data/` only (subfolders ok, no `..`, no dotfiles, no absolute paths). New/Open refuse to drop unsaved changes unless `discard=true`.

### HTTP API (port 8765)
`GET /api/state[?since=V&instance=I]` (long-poll ≤25 s) · `POST /api/ops {ops,label}` · `POST /api/undo|redo` · `POST /api/file/new|open|save|delete|import` · `GET /api/files` · `GET /api/export/cnc|file` · `POST /api/screenshot`. Responses carry the full state (`version, file, name, dirty, can_undo, undo_label, …, doc`). `instance` changes on every server start.

### Frontend notes
- Rendering: `canvas.render()` rebuilds `#content` (one `<g>` per layer) from `app.doc`, plus `#hits`: invisible 12 px-wide clones (non-scaling stroke) for easy clicking; `pickAt()` chooses the closest outline among overlapping hits.
- Selection is a Set of ids. Marquee: left→right = fully inside, right→left = touching. Moves preview with a `translate()` on the nodes and commit one `update_element` per element on mouse-up (`geometry.moveAttrs`: coords for primitives, a leading `translate()` for paths/polys/transformed elements).
- Display strokes are 1 px (`vector-effect: non-scaling-stroke`, screen dash patterns); exported files use layer dash patterns in mm.
- Per-browser view prefs (zoom, grid, snap, ruler unit) are in localStorage under `svgcnc.*`; the page always fits the document on load.

## MCP tools (server `svg-editor`, SSE on :8766)

Document/files: `get_document_info, list_documents, new_document, open_document, save_document, set_canvas_size, export_cnc, undo, redo`
Elements: `list_elements, add_element, add_svg (many at once, one undo step), update_element, remove_element (comma ids), set_element_layer, get_svg`
Layers: `list_layers, add_layer, update_layer (rename/colour/line style/visible/locked/export/description), remove_layer, move_layer`
Preview: `take_screenshot` (returns an image; needs the editor open in a browser), `set_background_image, remove_background_image`

### Drawing with MCP
1. `get_document_info` / `new_document(width, height)` (mm). Don't discard the user's unsaved work without asking.
2. Put geometry on the right layer instead of passing colours: outlines → CUT_OUTSIDE, holes/slots/windows → CUT_INSIDE, marks/pockets → ENGRAVE, labels/dimensions → NOTES.
3. Prefer `add_svg` with `<path>` markup for parts (one undo step); use `fill="none"`.
4. `take_screenshot` to check, `save_document`, `export_cnc` for the shop file.

## SVG for CNC guidelines
- 1 unit = 1 mm; files carry `mm` width/height + viewBox. Don't scale in CAM.
- Draw nominal geometry; kerf/tool offsets are CAM's job (CUT_OUTSIDE = outside offset, CUT_INSIDE = inside offset). Compensate manually only for press fits.
- Inside corners get the tool radius; add dog-bones where a square part must fit.
- Closed shapes with no gaps; no duplicate overlapping lines (cut twice); text converted to paths before engraving; no gradients/clips/effects.
