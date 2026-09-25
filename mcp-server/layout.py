"""Parts on the drawing: moving them without breaking their 3D placement, packing them onto
stock sheets, and checking the layout (2D) and the assembly (3D) with numbers.

An entity's `assembly.matrix` maps drawing coordinates to the part's own 2D coordinates. When
a part is moved or turned on the sheet, the matrix is composed with the inverse of that move,
so the part stays where it was in 3D. The `move`, `transform` and `arrange` ops all go through
`reposition()`; so do the MCP tools and the editor's drag and nudge.
"""
from __future__ import annotations

import math
import re

from document import DocError, Document, num
from export import IDENTITY, bbox, element_shapes, mul, parse_transform, shape_points

SHEETS_LAYER = "SHEETS"
SHEETS_COLOR = "#94a3b8"
NOTES_ENTITY = "Notes"


# ── Moving parts ─────────────────────────────────────────────────────────

def move_attrs(e, dx, dy) -> dict:
    """Attribute patch moving an element by (dx, dy): same rules as the editor (geometry.js)."""
    a = e.attrs
    n = lambda k: float(a.get(k, 0) or 0)
    r = num                                     # 4 decimals, "3" rather than "3.0"
    if a.get("transform") or e.tag in ("path", "polygon", "polyline"):
        t = a.get("transform", "")
        m = re.match(r"\s*translate\(\s*([-\d.e]+)(?:[\s,]+([-\d.e]+))?\s*\)\s*", t)
        if m:
            x, y = r(float(m.group(1)) + dx), r(float(m.group(2) or 0) + dy)
            rest = t[m.end():].strip()
            return {"transform": " ".join(p for p in (f"translate({x}, {y})" if (x or y) else "", rest) if p)}
        return {"transform": " ".join(p for p in (f"translate({r(dx)}, {r(dy)})", t.strip()) if p)}
    if e.tag == "line":
        return {"x1": r(n("x1") + dx), "y1": r(n("y1") + dy), "x2": r(n("x2") + dx), "y2": r(n("y2") + dy)}
    if e.tag in ("rect", "text"):
        return {"x": r(n("x") + dx), "y": r(n("y") + dy)}
    return {"cx": r(n("cx") + dx), "cy": r(n("cy") + dy)}


def invert(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        raise DocError("That transform flattens the shapes (zero scale)")
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(ia * e + ic * f), -(ib * e + id_ * f))


def element_ids(doc: Document, items) -> list[str]:
    """Element and entity ids → element ids (entities at any depth), in order, no repeats."""
    if isinstance(items, str):
        items = [items]
    out = []
    for i in items or []:
        out += doc.descendants(i) if str(i).startswith("g-") else [doc.element(i).id]
    return list(dict.fromkeys(out))


def reposition(doc: Document, items, dx: float = 0.0, dy: float = 0.0, transform: str | None = None) -> int:
    """Move elements/entities by (dx, dy), or apply an SVG transform to them. Every entity whose
    shapes all move keeps its 3D placement (its matrix follows the move). Returns the count."""
    ids = element_ids(doc, items)
    if transform:
        t = parse_transform(transform)
        if t == IDENTITY and not re.search(r"\w+\s*\(", transform):
            raise DocError(f"Not an SVG transform: {transform!r}")
        for i in ids:
            e = doc.element(i)
            doc.update_element(i, {"transform": " ".join(x for x in (transform.strip(), e.attrs.get("transform")) if x)})
    else:
        dx, dy = float(dx or 0), float(dy or 0)
        t = (1.0, 0.0, 0.0, 1.0, dx, dy)
        for i in ids:
            doc.update_element(i, move_attrs(doc.element(i), dx, dy))
    moved = set(ids)
    inv = invert(t)
    for g in doc.groups:
        if not g.assembly:
            continue
        own = doc.descendants(g.id)
        if own and moved.issuperset(own):
            m = mul(g.assembly.get("matrix") or IDENTITY, inv)
            g.assembly = {**g.assembly, "matrix": [num(v) for v in m]}
    return len(ids)


# ── Part geometry ────────────────────────────────────────────────────────

def cut_layers(doc: Document) -> set[str]:
    return {l.name for l in doc.layers if l.export}


def pieces(doc: Document) -> list:
    """The entities that are single pieces of stock: they have cut shapes and none of their
    sub-entities do (a "Drawer" entity holding its 5 part-entities is not a piece; the parts are)."""
    cut = cut_layers(doc)
    has_cut = {g.id for g in doc.groups if any(doc.element(i).layer in cut for i in doc.descendants(g.id))}
    parents = {g.parent for g in doc.groups if g.id in has_cut}
    return [g for g in doc.groups if g.id in has_cut and g.id not in parents]


def part_elements(doc: Document, gid: str) -> list:
    """The shapes that make a part in 3D (same rule as the preview): through-cut layers, or, for
    an entity with nothing to cut (e.g. HARDWARE), its shapes that aren't notes."""
    ids = set(doc.descendants(gid))
    through = {l.name for l in doc.layers if l.export and not l.depth}
    els = [e for e in doc.elements if e.id in ids and e.layer in through]
    return els or [e for e in doc.elements if e.id in ids and e.layer != "NOTES" and e.tag != "text"]


def unplaced_pieces(doc: Document) -> list:
    """Pieces that won't show in the 3D preview: neither they nor an entity around them is placed."""
    return [g for g in pieces(doc) if not any(doc.group_by_id(a).assembly for a in doc.ancestors(g.id))]


def default_matrix(doc: Document, gid: str):
    """The part's local origin at the bottom-left of its drawing, y up: [1, 0, 0, -1, -x0, y1]."""
    els = part_elements(doc, gid)
    b = bbox(els) if els else None
    if not b:
        raise DocError("The entity has no shapes yet: add them before placing it in 3D")
    return [1, 0, 0, -1, num(-b[0]), num(b[3])]


def outline_polys(doc: Document, gid: str):
    """(outers, holes) of a part as sampled polygons in drawing coordinates."""
    els = [e for e in part_elements(doc, gid) if e.tag != "text"]
    has_outside = any(e.layer == "CUT_OUTSIDE" for e in els)
    outers, holes = [], []
    for e in els:
        for s in element_shapes(e, None):
            if s[0] == "circle":
                _, cx, cy, r = s
                poly = [(cx + r * math.cos(k * math.pi / 18), cy + r * math.sin(k * math.pi / 18)) for k in range(36)]
            elif s[0] == "contour" and s[1].closed:
                poly = [tuple(p[:2]) for p in s[1].points()]
            else:
                continue
            (outers if (e.layer == "CUT_OUTSIDE" or not has_outside) else holes).append(poly)
    if not has_outside and len(outers) > 1:        # no CUT_OUTSIDE: the biggest shape is the outline
        outers.sort(key=lambda p: -abs(_area(p)))
        holes, outers = outers[1:], outers[:1]
    return outers, holes


def pocket_polys(doc: Document, gid: str):
    """[(polygon, depth)] of a part's pockets (shapes on layers with a depth), drawing coordinates."""
    depth = {l.name: l.depth for l in doc.layers if l.depth}
    out = []
    for i in doc.descendants(gid):
        e = doc.element(i)
        if e.layer not in depth or e.tag == "text":
            continue
        for s in element_shapes(e, None):
            if s[0] == "circle":
                _, cx, cy, r = s
                out.append(([(cx + r * math.cos(k * math.pi / 18), cy + r * math.sin(k * math.pi / 18))
                             for k in range(36)], depth[e.layer]))
            elif s[0] == "contour" and s[1].closed:
                out.append(([tuple(p[:2]) for p in s[1].points()], depth[e.layer]))
    return out


def _area(p):
    return sum(p[i - 1][0] * p[i][1] - p[i][0] * p[i - 1][1] for i in range(len(p))) / 2


def _inside(pt, poly):
    c = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i - 1], poly[i]
        if (y1 > pt[1]) != (y2 > pt[1]) and pt[0] < x1 + (pt[1] - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


def _box(pts, pad=0.0):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def _touch(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _seg_dist(p, a, b):
    vx, vy = b[0] - a[0], b[1] - a[1]
    L = vx * vx + vy * vy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / L))
    return math.hypot(p[0] - a[0] - t * vx, p[1] - a[1] - t * vy)


def _poly_dist(P, Q, limit):
    """Distance between two polygon outlines, or `limit` if it's more than that."""
    qb = _box(Q, limit)
    best = limit
    for p in P:
        if not (qb[0] <= p[0] <= qb[2] and qb[1] <= p[1] <= qb[3]):
            continue
        for i in range(len(Q)):
            d = _seg_dist(p, Q[i - 1], Q[i])
            if d < best:
                best = d
    return best


def layout_issues(doc: Document, min_gap: float) -> list[dict]:
    """Parts that overlap on the drawing, sit closer than min_gap (the cuts would run into each
    other), or aren't fully on a sheet of the SHEETS layer (when there is one)."""
    issues = []
    cut = cut_layers(doc)
    parts = []
    for g in pieces(doc):
        outers, holes = outline_polys(doc, g.id)
        if outers:
            parts.append((g, outers, holes, _box([p for o in outers for p in o])))
    for i, (ga, oa, ha, ba) in enumerate(parts):
        for gb, ob, hb, bb in parts[i + 1:]:
            if not _touch(_box([(ba[0], ba[1]), (ba[2], ba[3])], min_gap), bb):
                continue

            def within(o1, o2, h2):   # a point of o1 inside o2's material (not in one of its holes)
                return any(_inside(p, q) and not any(_inside(p, h) for h in h2)
                           for poly in o1 for p in poly[::4] for q in o2)
            if within(oa, ob, hb) or within(ob, oa, ha):
                issues.append({"kind": "parts_overlap", "ids": [ga.id, gb.id],
                               "message": f"'{ga.name}' and '{gb.name}' overlap on the drawing"})
                continue
            d = min(_poly_dist(p, q, min_gap) for p in oa for q in ob + hb)
            d = min([d] + [_poly_dist(p, q, min_gap) for p in ob for q in oa + ha])
            if d < min_gap - 0.01:
                issues.append({"kind": "parts_too_close", "ids": [ga.id, gb.id],
                               "message": f"'{ga.name}' and '{gb.name}' are {d:.1f} mm apart: less than the "
                                          f"{min_gap:g} mm tool, the cuts would run into each other"})
    sheets = [e for e in doc.elements if e.layer == SHEETS_LAYER and e.tag == "rect"]
    if sheets:
        boxes = [bbox([s]) for s in sheets]
        for g, outers, _, b in parts:
            if not any(s[0] - 0.01 <= b[0] and s[1] - 0.01 <= b[1] and b[2] <= s[2] + 0.01 and b[3] <= s[3] + 0.01
                       for s in boxes):
                issues.append({"kind": "off_sheet", "ids": [g.id],
                               "message": f"'{g.name}' isn't fully on a sheet (SHEETS layer): run arrange_parts or move it"})
    return issues


# ── Packing parts onto sheets ────────────────────────────────────────────

def _pack(sizes, W, H, rotate):
    """Best of a few orders (fewest sheets, then the emptiest last sheet)."""
    orders = [lambda i: (-sizes[i][0] * sizes[i][1], -max(sizes[i])), lambda i: (-max(sizes[i]), -min(sizes[i])),
              lambda i: (-sizes[i][1], -sizes[i][0]), lambda i: (-sizes[i][0], -sizes[i][1])]
    best = None
    for key in orders:
        out = _pack_order(sizes, W, H, rotate, sorted(range(len(sizes)), key=key))
        n = max((p[0] for p in out if p), default=-1) + 1
        last = sum(sizes[i][0] * sizes[i][1] for i, p in enumerate(out) if p and p[0] == n - 1)
        if best is None or (n, last) < best[0]:
            best = ((n, last), out)
    return best[1]


def _pack_order(sizes, W, H, rotate, order):
    """MaxRects (best short side fit), sheet after sheet. sizes are (w, h) including the gap.
    Returns per item (sheet, x, y, turned) or None when it fits no sheet."""
    eps = 1e-6
    sheets = []                                  # free rectangles per sheet: [x, y, w, h]
    out = [None] * len(sizes)
    for i in order:
        w, h = sizes[i]
        options = [(w, h, False)] + ([(h, w, True)] if rotate and abs(w - h) > eps else [])
        if not any(pw <= W + eps and ph <= H + eps for pw, ph, _ in options):
            continue
        best = None
        for s in range(len(sheets) + 1):
            if s == len(sheets):
                sheets.append([[0.0, 0.0, W, H]])
            for fx, fy, fw, fh in sheets[s]:
                for pw, ph, turned in options:
                    if pw <= fw + eps and ph <= fh + eps:
                        score = (min(fw - pw, fh - ph), max(fw - pw, fh - ph), turned)
                        if best is None or score < best[0]:
                            best = (score, fx, fy, pw, ph, turned)
            if best:
                break
        _, x, y, pw, ph, turned = best
        out[i] = (s, x, y, turned)
        free = []
        for f in sheets[s]:
            fx, fy, fw, fh = f
            if x >= fx + fw - eps or x + pw <= fx + eps or y >= fy + fh - eps or y + ph <= fy + eps:
                free.append(f)
                continue
            if x > fx + eps: free.append([fx, fy, x - fx, fh])
            if x + pw < fx + fw - eps: free.append([x + pw, fy, fx + fw - x - pw, fh])
            if y > fy + eps: free.append([fx, fy, fw, y - fy])
            if y + ph < fy + fh - eps: free.append([fx, y + ph, fw, fy + fh - y - ph])
        sheets[s] = [f for k, f in enumerate(free)
                     if not any(k != j and g[0] <= f[0] + eps and g[1] <= f[1] + eps and f[0] + f[2] <= g[0] + g[2] + eps
                                and f[1] + f[3] <= g[1] + g[3] + eps and (g != f or j < k) for j, g in enumerate(free))]
    return out


def arrange(doc: Document, op: dict) -> dict:
    """Pack the parts (see pieces()) onto stock sheets (bounding boxes, optionally turned 90°), draw
    the sheets on the SHEETS layer, keep every part's 3D placement. See the `arrange` op."""
    W = float(op.get("sheet_width") or doc.material.get("sheet_width") or 2440)
    H = float(op.get("sheet_height") or doc.material.get("sheet_height") or 1220)
    margin = float(op.get("margin", 15))
    gap = float(op.get("gap", 20))
    rotate = bool(op.get("rotate", True))
    ox, oy = (float(v) for v in op.get("origin") or (40, 70))
    spacing = float(op.get("spacing", 120))
    if min(W, H) <= 2 * margin or gap < 0 or margin < 0:
        raise DocError("Sheet too small for the margin (or a negative gap/margin)")
    cut = cut_layers(doc)
    parts = []
    for g in pieces(doc):
        els = [doc.element(i) for i in doc.descendants(g.id)]
        cut_els = [e for e in els if e.layer in cut]
        b = bbox(cut_els) if cut_els else None
        if b:
            parts.append((g, b))
    if not parts:
        raise DocError("No parts to arrange: group each part (outline + holes) into an entity first")
    sizes = [(b[2] - b[0] + gap, b[3] - b[1] + gap) for _, b in parts]
    placed = _pack(sizes, W - 2 * margin + gap, H - 2 * margin + gap, rotate)
    n_sheets = max((p[0] for p in placed if p), default=-1) + 1
    sheet_y = lambda s: oy + s * (H + spacing)
    bottom = sheet_y(n_sheets) - spacing if n_sheets else oy
    over_x, over_y, over_h = ox, bottom + spacing, 0.0
    report = {"sheets": n_sheets, "sheet_size": [num(W), num(H)], "parts": [], "too_big": [], "turned": []}
    for (g, b), p in zip(parts, placed):
        w, h = b[2] - b[0], b[3] - b[1]
        if p:
            s, x, y, turned = p
            X, Y = ox + margin + x, sheet_y(s) + margin + y
        else:                                    # bigger than a sheet: a row under the sheets
            s, turned, X, Y = None, False, over_x, over_y
            over_x += w + gap
            over_h = max(over_h, h)
            report["too_big"].append(g.name)
            if sum(1 for i in doc.descendants(g.id) if doc.element(i).layer == "CUT_OUTSIDE") > 1:
                report.setdefault("hints", []).append(
                    f"'{g.name}' holds several separate outlines: make one entity per piece so each can be packed")
        if turned:                               # rotate(90) about the origin, then to (X, Y)
            reposition(doc, [g.id], transform=f"translate({num(X + b[3])} {num(Y - b[0])}) rotate(90)")
            report["turned"].append(g.name)
        elif abs(X - b[0]) > 1e-6 or abs(Y - b[1]) > 1e-6:
            reposition(doc, [g.id], X - b[0], Y - b[1])
        report["parts"].append({"id": g.id, "name": g.name, "sheet": None if s is None else s + 1})
    if not doc.has_layer(SHEETS_LAYER):
        doc.add_layer(SHEETS_LAYER, SHEETS_COLOR, "dashed", export=False,
                      description="Stock sheets drawn by arrange_parts (clamp margin inside). Never cut.")
    doc.elements = [e for e in doc.elements if e.layer != SHEETS_LAYER]
    doc.prune_groups()
    label = op.get("label") or f"{doc.material.get('name') or 'Sheet'}"
    for s in range(n_sheets):
        y = sheet_y(s)
        doc.add_element("rect", {"x": num(ox), "y": num(y), "width": num(W), "height": num(H), "fill": "none"},
                        layer=SHEETS_LAYER)
        doc.add_element("text", {"x": num(ox), "y": num(y - 14), "font-size": 20},
                        f"SHEET {s + 1} / {n_sheets} — {W:g} × {H:g} — {label}", SHEETS_LAYER)
    if report["too_big"]:
        doc.add_element("text", {"x": num(ox), "y": num(over_y - 14), "font-size": 20},
                        "TOO BIG FOR THE SHEET — cut by hand or split: " + ", ".join(report["too_big"]), SHEETS_LAYER)
        bottom = over_y + over_h
    # Notes below everything: an entity called "Notes" (replaced when `notes` is given)
    notes_g = next((g for g in doc.groups if g.name == NOTES_ENTITY and not g.parent), None)
    lines = op.get("notes")
    if lines:
        if isinstance(lines, str):
            lines = lines.split("\n")
        if notes_g:
            doc.remove_elements(doc.descendants(notes_g.id))
        ids = [doc.add_element("text", {"x": 0, "y": 22 * k, "font-size": 18 if k == 0 else 15}, str(t)[:500], "NOTES").id
               for k, t in enumerate(lines)]
        notes_g = doc.group(ids, NOTES_ENTITY)
    if notes_g:
        nb = bbox([doc.element(i) for i in doc.descendants(notes_g.id)])
        if nb:
            reposition(doc, [notes_g.id], ox - nb[0], bottom + 60 - nb[1])
            bottom = bottom + 60 + nb[3] - nb[1]
    everything = bbox(doc.elements)
    right = max(ox + W, everything[2] if everything else 0)
    doc.set_size(math.ceil(right + 40), math.ceil(max(bottom, everything[3] if everything else 0) + 40))
    per = {}
    for p in report["parts"]:
        per.setdefault(p["sheet"], []).append(p["name"])
    report["per_sheet"] = {("too big" if k is None else f"sheet {k}"): v for k, v in per.items()}
    extra = sum(g.qty - 1 for g, _ in parts if g.qty > 1)
    if extra:
        report["note"] = (f"{extra} extra copies (qty > 1) aren't on these sheets: draw them as their own "
                          f"entities, or nest the part files (export_parts)")
    return report


# ── 3D: where each part ends up ──────────────────────────────────────────

def _rotation(asm):
    """Euler XYZ rotation matrix (three.js order), rows = world axes."""
    rx, ry, rz = (math.radians(v) for v in asm.get("rotation") or (0, 0, 0))
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    return [[cy * cz, -cy * sz, sy],
            [cx * sz + sx * sy * cz, cx * cz - sx * sy * sz, -sx * cy],
            [sx * sz - cx * sy * cz, sx * cz + cx * sy * sz, cx * cy]]


class Placed:
    """A part in 3D: drawing point + height above its bottom face ↔ world point."""

    def __init__(self, g, thickness: float, outers, holes, pockets=()):
        a = g.assembly
        self.g, self.t = g, a.get("thickness") or thickness
        self.m = tuple(a.get("matrix") or IDENTITY)
        self.inv = invert(self.m)
        self.R = _rotation(a)
        self.pos = a.get("position") or (0, 0, 0)
        self.outers, self.holes, self.pockets = outers, holes, pockets

    def world(self, x, y, z):
        a, b, c, d, e, f = self.m
        v = (a * x + c * y + e, b * x + d * y + f, z)
        return tuple(sum(self.R[i][k] * v[k] for k in range(3)) + self.pos[i] for i in range(3))

    def solid_contains(self, w, eps=0.05) -> bool:
        """Is world point w inside this part's material (outline minus holes and pockets, 0 < z <
        thickness; pockets are cut from the top face, z = thickness)?"""
        v = [w[i] - self.pos[i] for i in range(3)]
        lx, ly, lz = (sum(self.R[k][i] * v[k] for k in range(3)) for i in range(3))   # Rᵀ·v
        if not eps < lz < self.t - eps:
            return False
        a, b, c, d, e, f = self.inv
        p = (a * lx + c * ly + e, b * lx + d * ly + f)
        if not any(_inside(p, o) for o in self.outers) or any(_inside(p, h) for h in self.holes):
            return False
        return not any(lz > self.t - dep and _inside(p, poly) for poly, dep in self.pockets)

    def samples(self, n=240):
        """Points spread through the part's material (a grid over the outline, two heights)."""
        pts = [p for o in self.outers for p in o]
        if not pts:
            return []
        x0, y0, x1, y1 = _box(pts)
        area = max(sum(abs(_area(o)) for o in self.outers), 1e-6)
        step = max(math.sqrt(area / n), 0.5)
        out = []
        y = y0 + step / 2
        while y < y1:
            x = x0 + step / 2
            while x < x1:
                if any(_inside((x, y), o) for o in self.outers) and not any(_inside((x, y), h) for h in self.holes):
                    out += [self.world(x, y, self.t * 0.25), self.world(x, y, self.t * 0.75)]
                x += step
            y += step
        return out


def world_box(asm: dict, pts, thickness: float):
    """World box (x0, y0, z0, x1, y1, z1) of drawing points extruded by an assembly (Euler XYZ,
    same as three.js and kerf_script.world_box)."""
    a, b, c, d, e, g = asm.get("matrix") or IDENTITY
    R = _rotation(asm)
    px, py, pz = asm.get("position") or (0, 0, 0)
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    t = asm.get("thickness") or thickness
    for x, y in pts:
        lx, ly = a * x + c * y + e, b * x + d * y + g
        for z in (0, t):
            v = (lx, ly, z)
            for i, p in enumerate((px, py, pz)):
                w = R[i][0] * v[0] + R[i][1] * v[1] + R[i][2] * v[2] + p
                lo[i], hi[i] = min(lo[i], w), max(hi[i], w)
    return tuple(lo) + tuple(hi)


def assembly_report(doc: Document, threshold: float = 20.0) -> dict:
    """Every placed part's world box (mm), the whole model's box, parts not placed in 3D, parts
    below the floor, and clashes: pairs where more than `threshold` % of one part's material is
    inside the other's. Joints (tenons, dados, dowels in holes) stay far below that; two parts in
    the same spot, or a part sunk into another, don't."""
    thick = float(doc.material.get("thickness") or 18)
    placed = []
    for g in doc.groups:
        if g.assembly:
            pts = [p for e in part_elements(doc, g.id) for s in element_shapes(e, None) if s[0] != "text"
                   for p in shape_points(s)]
            if pts:
                placed.append((g, world_box(g.assembly, pts, thick)))
    unplaced = unplaced_pieces(doc)
    r = lambda b: [round(v, 1) for v in b]
    parts = [{"id": g.id, "name": g.name, "box": r(b), "size": r([b[3] - b[0], b[4] - b[1], b[5] - b[2]])}
             for g, b in placed]
    out = {"units": "mm; box = [x0, y0, z0, x1, y1, z1]; X = width, Y = up, Z = toward the viewer",
           "parts": parts}
    if placed:
        allb = [min(b[i] for _, b in placed) for i in range(3)] + [max(b[i] for _, b in placed) for i in range(3, 6)]
        out["model_box"] = r(allb)
        out["model_size"] = r([allb[3] - allb[0], allb[4] - allb[1], allb[5] - allb[2]])
    if placed and unplaced:
        out["not_in_3d"] = [{"id": g.id, "name": g.name} for g in unplaced]
    below = [g.name for g, b in placed if b[1] < -0.5]
    if below:
        out["below_floor"] = below
    solids, samples = {}, {}

    def solid(g):
        if g.id not in solids:
            outers, holes = outline_polys(doc, g.id)
            solids[g.id] = Placed(g, thick, outers, holes, pocket_polys(doc, g.id))
        return solids[g.id]
    clashes = []
    for i, (ga, a) in enumerate(placed):
        for gb, b in placed[i + 1:]:
            if not all(min(a[k + 3], b[k + 3]) - max(a[k], b[k]) > 0.1 for k in range(3)):
                continue
            if ga.id in doc.ancestors(gb.parent) or gb.id in doc.ancestors(ga.parent):
                continue
            pa, pb = solid(ga), solid(gb)
            share = []
            for p, q in ((pa, pb), (pb, pa)):
                pts = samples.setdefault(p.g.id, p.samples())
                share.append(100.0 * sum(q.solid_contains(w) for w in pts) / len(pts) if pts else 0.0)
            if max(share) > threshold:
                clashes.append({"parts": [ga.name, gb.name], "ids": [ga.id, gb.id],
                                "inside_pct": [round(v) for v in share],
                                "message": f"{share[0]:.0f}% of '{ga.name}' is inside '{gb.name}', "
                                           f"{share[1]:.0f}% of '{gb.name}' inside '{ga.name}'"})
    clashes.sort(key=lambda c: -max(c["inside_pct"]))
    out["clashes"] = clashes[:40]
    return out
