### MCP-first CNC design editor

A browser CAD-style editor for CNC cut drawings (1 unit = 1 mm) with an MCP server, so Claude can draw, edit and preview the same documents live with you.

[Demo](https://youtu.be/_wV0kU_m0hc)

- **Projects** (`.svgcnc`) keep everything: layers, parts (entities), 3D placements, parameters, material & stock. Several open at once in tabs.
- **Drawing:** click–click or drag, type exact sizes (length/angle, width/height, diameter), snapping, measuring tool, dimensions.
- **Layers:** rename, colour, solid/dashed/dotted, lock, hide, reorder, description, CNC-export flag, **pocket depth**.
- **Entities:** group a part's outline + holes. Double-click to edit inside, set quantity and 3D placement.
- **3D preview** of the assembled design, with sliders (e.g. desk height). Export **GLB** / **STL**.
- **Exports:** CNC **SVG** and **DXF** (mm, true arcs, circles), **parts for nesting** (SVG + DXF per part, zipped), SVG, PNG. **Imports:** SVG, DXF.
- Undo/redo, autosave session, Open/Save As in the data folder or anywhere on your computer.

Start
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
Draw a 400 × 300 mm box lid in 6 mm MDF with four 8 mm holes 20 mm from the corners, group it and show it in 3D
```

Tests
```
docker compose run --rm --no-deps -T --entrypoint python svg-mcp -m pytest -q tests
```
