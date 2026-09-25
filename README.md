### MCP-first SVG CNC editor

A web SVG editor for CNC cut drawings (1 unit = 1 mm) and an MCP server so Claude can draw, edit, and preview the same document.

[Demo](https://youtu.be/_wV0kU_m0hc)

- File menu: New / Open / Save / Save As / Import / Export for CNC (SVG in mm) / PNG, with undo/redo
- Layers you can rename, recolour, set solid/dashed/dotted, hide, lock, reorder, describe and exclude from CNC export
- Select by click or box (left→right inside, right→left touching), move, nudge, duplicate; the inspector shows the selected shape with editable geometry
- Documents are plain SVGs in `data/` (Inkscape layers, mm units); the session autosaves so nothing is lost on restart

Start the project
```
docker compose up -d --build
```

Open the editor
```
http://localhost:8765/
```

Open Claude Code in this folder (the MCP server is configured in `.mcp.json`)
```
claude
```

Ask it to build something
```
Draw a 400 × 300 mm box lid with four 8 mm holes 20 mm from the corners
```

Run the server tests
```
docker compose run --rm --no-deps -T --entrypoint python svg-mcp -m pytest -q tests
```
