### Kerf — MCP-first CNC design editor

A browser CAD-style editor for CNC cut drawings (1 unit = 1 mm) with an MCP server, so Claude can draw, edit and preview the same documents live with you.

[Demo](https://youtu.be/_wV0kU_m0hc)

- **Projects** (`.kerf`) keep everything: project name, layers, parts (entities), 3D placements, parameters, material & stock. Several open at once in tabs (each with a 2D and a 3D view).
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

## Connect Claude

In the editor, click **Connect Claude** (top right, or Help → Connect Claude). It shows copy-paste setups built from the editor's own address:
- **Claude Code:** a one-line `claude mcp add …` command.
- **`.mcp.json`:** a project config file.
- **Claude Desktop:** a config using `mcp-remote`.
- **Prompt (curl, no MCP):** a text you paste into Claude that explains the HTTP API with curl examples.

Each has a Copy button.

## MCP tools

The `kerf` MCP server (SSE on port 8766) gives Claude the same editor you see. Tools act on the active tab.

- **Start here:** `get_guide` returns the design guide: workflow, layers, 3D placement recipes, CNC rules and parametric scripts. Ask for one section with `get_guide("placement")`, `"scripts"`, and so on.
- **Projects and tabs:** `get_document_info`, `list_tabs`, `switch_tab`, `new_document`, `open_document`, `save_document`, `close_document`, `revert_document`, `set_project_name`, `set_canvas_size`, `set_material`, `undo`, `redo`, `list_files`.
- **Shapes:** `add_svg` (many shapes, one undo step), `add_element`, `update_element`, `remove_element`, `move_elements`, `transform_elements`, `duplicate`, `set_element_layer`, `list_elements`, `find_elements`, `get_svg`.
- **Parts (entities) and 3D:** `group_elements`, `ungroup`, `move_to_entity`, `update_group` (name, qty, 3D placement: `rotation` + `position`; the drawing→part matrix is automatic and follows the part when it's moved), `describe_entity`, `list_groups`, `set_params` (sliders).
- **Sheets:** `arrange_parts` packs every part onto stock sheets (turning parts when that fits better), draws the sheets and a notes block, and keeps the 3D placement.
- **Layers:** `add_layer` (with `depth` for pockets), `update_layer`, `remove_layer`, `move_layer`, `list_layers`.
- **Batch edits:** `apply_ops` runs any list of ops as one undo step. Name an op with `"as": "side"` and refer to its result as `"$side"` (`"$n"` = op n also works).
- **Check and look:** `check_cnc` (open outlines, holes smaller than the tool, duplicates, parts bigger than the sheet, parts overlapping or too close, parts off the sheets…), `describe_assembly` (every part's 3D world box, parts clashing), `measure`, `add_dimension`, `take_screenshot("2d" | "3d" | "3d-exploded")`, `get_selection` / `set_selection` (what you selected, or highlight parts for you).
- **Files for the shop:** `export_cnc` (SVG or DXF), `export_parts` (one file per part, for nesting), `export_svg`, `import_dxf`.
- **Prompts:** `design_part`, `design_furniture`, `prepare_for_cutting`.

## Share links

`http://<editor>/?open=<file>` opens a project from the data folder, or switches to its tab if it's already open. Add `&view=3d` to open it in 3D. Example: `/?open=desk/desk.kerf&view=3d`.

**File → Copy link to this project** copies the link for the current tab. The project must be saved in the data folder, and the current 2D/3D view is included.

A missing file just shows "not found". When deployed, a link like `/?token=…&open=…` signs in first and then opens the file.

## Deploying (reachable from other machines)

By default Kerf only answers on this computer. To put it on a server, set these in `docker-compose.yml` → `environment`, and publish the ports (or put a reverse proxy in front):

```yaml
    ports:
      - "8765:8765"   # editor + HTTP API
      - "8766:8766"   # MCP (SSE)
    environment:
      - KERF_PUBLIC_URL=https://kerf.example.com          # where people open the editor
      - KERF_MCP_URL=https://kerf.example.com:8766/sse    # optional; default: same host, port 8766
      - KERF_TOKEN=change-me-to-a-long-random-string      # required once KERF_PUBLIC_URL is set
```

- **Browser:** open `https://kerf.example.com/?token=…` once. That sets a login cookie; without it the editor shows a sign-in page.
- **API and MCP clients:** send `Authorization: Bearer <KERF_TOKEN>`. The Connect Claude snippets already include it.
- **Refuses to start without a token:** the server exits if `KERF_PUBLIC_URL` is set but `KERF_TOKEN` isn't, because anyone could otherwise edit and delete your files.
- **Use HTTPS** (e.g. a Caddy or nginx proxy). Otherwise the token travels in plain text.

## Workflow: how drawings get made

There are three ways to build a design. They share one server-side document, so every change shows up live in the browser.

1. **By hand in the editor.** Draw, type exact sizes, group parts, place them in 3D.
2. **Claude through MCP (the main way).** Claude calls the `kerf` MCP tools: `add_svg`, `update_element`, `group_elements`, `update_group` (3D placement), `set_params`, `take_screenshot`, and others. Each call is an undoable edit on the open tab. Use this for one-off parts, edits, checks (`check_cnc`, `measure`) and for opening or saving projects.
3. **A Python generator (optional).** Some designs are really a formula, like the desk in `data/desk/`: 17° tracks, a sawtooth pitch, fillets, collision checks at every height. For those, a plain script (`data/desk/generate.py`) computes the geometry, runs its own checks, and writes a `.kerf` project. `--push` then opens or reloads it in the editor through the HTTP API. The editor and MCP stay the place to view, tweak, screenshot and export. Re-running the script regenerates everything, so treat the script as the source and the `.kerf` as its output. The script needs no dependencies, only Python 3.

In practice: Claude writes and edits the generator, runs it, pushes the result, and checks it with `take_screenshot` (2D, 3D, exploded) through MCP.

**Script helpers for generators.** `mcp-server/kerf_script.py` is served by the editor at `GET /api/script`, so Claude can fetch it even when Kerf runs on another machine. It is standard library only and handles:
- `Drawing`: a whole project as one batch: parts drawn in their own coordinates with holes, pockets, labels and 3D placement, local world-box checks (`expect`), `arrange()` onto sheets with notes, and `run()` (new tab, or rebuild a tab in place; `--dry`, `--save`);
- the HTTP calls: posting ops to a named tab, opening a new tab without taking over the one you're looking at, saving, `check` (the same checks as `check_cnc`, via `GET /api/check?tab=`) and `assembly_report` (`describe_assembly`, via `GET /api/assembly?tab=`);
- outlines with fillets, dog-bones and arcs, with the SVG arc flags computed for you;
- `place()`, which puts a part on the sheet (optionally turned) and returns the matching 3D matrix;
- `world_box()`, to check where a part lands in 3D.

`add_layer` accepts `exist_ok`, so a script can start with `clear` and be re-run safely. The guide's *Parametric scripts* section has a short example, which the tests run.

### Ideas

- **Generators as a first-class feature:** a `run_generator` MCP tool and a *Parameters* panel. Changing a number (width, height, material thickness) would re-run the script and update the tab, keeping manual edits on separate layers.
- Shapes → paths conversion and text → outlines for CAM.
- True-shape nesting (today `arrange_parts` packs bounding boxes; for tighter layouts: export parts → Deepnest/SVGnest).

Tests
```
docker compose run --rm --no-deps -T --entrypoint python kerf -m pytest -q tests
```
