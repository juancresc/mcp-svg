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

### Ideas

- **Generators as a first-class feature:** a `run_generator` MCP tool and a *Parameters* panel. Changing a number (width, height, material thickness) would re-run the script and update the tab, keeping manual edits on separate layers.
- Shapes → paths conversion and text → outlines for CAM.
- Nesting inside the editor (today: export parts → Deepnest/SVGnest).

Tests
```
docker compose run --rm --no-deps -T --entrypoint python kerf -m pytest -q tests
```
