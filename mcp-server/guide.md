# Kerf guide — designing CNC parts with Claude

Kerf is a CAD-style editor for CNC-cut parts: flat parts cut from sheet material (plywood, MDF, plastic, aluminium), grouped into an assembly with a live 3D preview. The user watches every change live in their browser. **1 unit = 1 mm, everywhere.**

## Workflow

1. **Ask first** if it isn't clear:
   - what the object is, its overall size, and who uses it (children? load?);
   - the material and thickness (default: 18 mm birch plywood);
   - the **CNC machine's working area**. Common sizes: 6090 = 600 × 900, Shapeoko XXL = 838 × 838, 1212 = 1200 × 1200, 1325 = 1300 × 2500 (full sheets).
2. **Set up the project:**
   - `new_document` (or reuse an empty tab);
   - `set_project_name` with a readable name;
   - `set_material` for name, thickness, colour, sheet size and tool Ø;
   - add the layers you need (see Layers).
3. **Work the numbers out first**, before drawing: part sizes, joints, hole positions and clearances. Keep parts at least 20 mm apart.
4. **Draw each part as one `apply_ops` batch:**
   - its shapes (`add_element` with `fill: "none"`);
   - then `group` with `"$n"` references to those shapes;
   - then `update_group` with `qty` and the `assembly` (3D placement, see below).

   That's one undo step per part.
5. **Lay the parts out for the machine.** Draw each bed or sheet outline as a rectangle on NOTES, with a label ("BED 1 / 3 — 838 × 838"). Keep a 15 mm margin inside it for clamps. Say clearly which parts are too big: plain rectangles can be cut with a saw.
6. **Check your work:**
   - `check_cnc`;
   - `take_screenshot` with `view="2d"`, then `"3d"` and `"3d-exploded"` (needs the editor open in a browser).

   Look at the images: parts in the wrong place in 3D almost always mean a wrong rotation or matrix. `describe_entity` gives exact bounds.
7. **Finish:**
   - `save_document` with a file name;
   - tell the user the share link, `<editor>/?open=<file>` (add `&view=3d` for the 3D view);
   - add a NOTES text with the cut order, the hardware list and assembly or safety notes.

Never discard the user's unsaved work or close their tabs without asking. Tools act on the active tab: `list_tabs` / `switch_tab`.

## Layers

The layer decides the colour, the line style and whether a shape is cut. Never set `stroke` on elements.

- `CUT_OUTSIDE`: part outlines. CAM cuts outside the line.
- `CUT_INSIDE`: holes, slots and windows through the part. CAM cuts inside the line.
- **Pockets** are partial-depth cuts: add a layer with a `depth`, e.g. `add_layer("HINGE_CUPS", depth=12.5)`. Pockets are cut **from the top face**, so make sure that face ends up where the pocket belongs (see Mirrored parts).
- `ENGRAVE`: shallow marking.
- `NOTES`: labels, dimensions and bed outlines. Never cut; not exported.
- **HARDWARE**: bought parts (dowels, rods, pins). Add it with `add_layer("HARDWARE", export=false)`. Draw each part's cross-section there so it shows in 3D. It is never cut.

## 3D placement (assembly)

World axes: **X = width (left → right), Y = up, Z = toward the viewer (front)**. Put the model's centre near X = 0.

Each entity's `assembly` has these fields:
- **`matrix [a,b,c,d,e,f]`** maps drawing coordinates to the part's own 2D coordinates (x right, y up): `local = (a·x + c·y + e, b·x + d·y + f)`.
- **`thickness`**: the extrusion along local +z. The **top face** is at z = thickness; it's the router face, where pockets are cut.
- **`rotation`** in degrees (Euler XYZ), then **`position`**: where the local origin goes, in world mm.
- Optional: `color`, and `move: {param, axis}` to slide the part with a `set_params` slider.

For a part drawn with bounding box `(ox, oy, w, h)`, **`matrix = [1, 0, 0, -1, -ox, oy + h]`** puts the local origin at the bottom-left corner of the drawing.

**Recipes** (local x/y below means after that matrix):

| Part | Draw it as | rotation | position (local origin →) | top face faces |
|---|---|---|---|---|
| Board standing, facing front (back panel, rail, drawer front) | x = width, y = height | `[0, 0, 0]` | [left X, bottom Y, back-face Z] | +Z (front) |
| Door with hinge cups inside | x = 0 at the door's **right** edge (world), y = height; cups near whichever edge has the hinges | `[0, 180, 0]` | [right-edge X, bottom Y, front-face Z] | −Z (inside) |
| Side panel, profile in depth × height | x = depth from the front, y = height | `[0, 90, 0]` | [its left-face X, bottom Y, front Z] | +X |
| Opposite side whose pockets face −X | drawn **mirrored** (front on the right); matrix `[1,0,0,-1,-(ox+w), oy+h]` | `[0, -90, 0]` | [its right-face X, bottom Y, front Z] | −X |
| Flat board (shelf, top, bottom, desktop) | x = width, y = depth with the **front edge at the bottom** of the drawing | `[-90, 0, 0]` | [left X, underside Y, front Z] | +Y (up) |
| Part turned 90° on the sheet to fit | matrix `[0, 1, 1, 0, -oy, -ox]`: local x runs down the sheet, local y to the right | as its recipe | as its recipe | as its recipe |
| Rod / dowel through side panels | its circle on HARDWARE, matrix mapping the circle centre to the panel's local (depth, height) | same as the panels | [start X, 0, front Z], thickness = rod length | — |

**Moving parts:** `set_params([{name: "lift", label: "Height", min: 0, max: 450, step: 37.5, unit: "mm"}])`, then add `"move": {"param": "lift", "axis": [0, 1, 0]}` to each part that slides.

**Nested entities:** group a drawer's 5 part-entities into one "Drawer 1" entity (the `group` op accepts entity ids). Quantities (`qty`) are used by `export_parts`.

## CNC rules of thumb (18 mm plywood)

- Inside corners get the tool radius (3 mm for a 6 mm end mill). Add dog-bones where a square part must fit in.
- Keep at least 12 mm of wood around holes; 20 mm between parts; 15 mm from the bed edge.
- **Bolting into a board's edge:**
  - a cross-dowel (barrel nut) joint: an Ø8.5 hole in the face panel, an Ø10 cross hole 25 mm from the end of the other board;
  - the face hole sits 9 mm from the panel edge (the middle of an 18 mm board);
  - inset the board about 10 mm from the panel's edge so there's wood left around the hole.
- Closed outlines, no duplicate lines, no text on cut layers, no parts bigger than the sheet or bed.
- **Opposite-hand parts with pockets must be drawn mirrored**, so both pockets end up on their inner faces.

## SVG gotchas

- **Arc flags:** `A rx ry 0 large sweep x y`. large = 1 only when the arc spans **more than 180°**. A circular segment whose centre lies beyond the chord (e.g. an arch whose circle centre is below the floor) spans less than 180°, so large = 0. A wrong flag bulges the shape out: always check the 2D screenshot or `describe_entity` bounds.
- In the drawing y points **down**; in part-local coordinates y points **up**, and the matrix flips it.

## Furniture for children

- Anti-tip: every tall piece gets holes for a wall strap. Put it in the notes.
- Round over every edge (3–6 mm), sand to 180–240, and use a child-safe finish.
- **Finger slots or holes instead of handles**, placed at the child's height (about 50–65 cm for toddlers).
- Soft-close hinges, drawer stops, and no gaps that pinch fingers.
- Hanging rails at about 80–85 cm are reachable by 2–5 year olds.

## Without MCP (HTTP API)

Every tool has an HTTP equivalent on the editor's address:
- `GET /api/state`: the active document;
- `POST /api/ops {ops, label, tab?}`: the same ops as `apply_ops`;
- `POST /api/file/new|open|save`;
- `GET /api/export/cnc|cnc-dxf|parts|project`.

Send `Content-Type: application/json` on POSTs. When the server has a token, add `Authorization: Bearer <token>` to every request.

For big or parametric designs, a script that computes the geometry and posts one ops batch is often easier than hundreds of calls.
