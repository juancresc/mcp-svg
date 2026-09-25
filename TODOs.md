# TODOs

## Done
- [x] Easier selection: box-select by dragging (left→right = fully inside, right→left = touching), click-through entities, Shift adds.
- [x] Selected element highlighted in the code panel (and scrolled into view).
- [x] Zoom: ⌘-scroll, zoom controls in the toolbar (−, %, +, fit), pan tool and Space-drag.
- [x] Code panel fits the window (fixed height, resizable, no page scrollbar).
- [x] New file: canvas size in mm, cm or inches.
- [x] Import files: SVG and DXF.
- [x] File browser for the data/ folder (mounted in Docker): Open, Save, Save As, new folders; also Save to computer.
- [x] Layers from the UI and MCP: add, rename, colour, line style, show/hide, lock, reorder, move elements between layers, description, pocket depth, export flag.
- [x] Rollbacks: per-tab undo/redo (one step per action, MCP batches included), Revert to saved, autosaved session.
- [x] Export to CAD: DXF (true arcs and circles), CNC SVG in mm, parts per entity (SVG + DXF zip), GLB/STL 3D.
- [x] Project name separate from the file name (tab, Inspector, File → Rename project, MCP `set_project_name`).

## Open
- [ ] Persistent history across server restarts (undo stacks are in memory; the autosaved session keeps only the documents).
- [ ] CNC export that converts shapes and text to paths.
- [ ] Generators as a first-class feature (see README → Ideas).
- [ ] Better app name than "Kerf" (too generic).
- [ ] Saving depends on who you are (for the deployed version):
  - not logged in: work only on the computer (open from / download to your machine; nothing is stored on the server);
  - logged in: projects are saved on the server, in the user's own space (Open/Save As browse their files).
