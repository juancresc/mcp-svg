"""Kerf script helpers — build parametric CNC parts in Python and send them to the Kerf editor.

Standard library only. Get the copy that matches your editor:
    curl -s [-H 'Authorization: Bearer <token>'] <editor>/api/script -o kerf_script.py
Settings: KERF_URL (default http://localhost:8765), KERF_TOKEN (only if the server has one).

Draw each part in its own coordinates (x right, y UP, mm), e.g. a side panel as depth × height;
place() maps it into the drawing and returns the matching assembly matrix. Arc flags are
computed for you. See the "Parametric scripts" section of the guide (GET /api/guide).
"""
import json
import math
import os
import urllib.error
import urllib.request

URL = os.environ.get("KERF_URL", "http://localhost:8765").rstrip("/")
TOKEN = os.environ.get("KERF_TOKEN", "")


# ── HTTP ─────────────────────────────────────────────────────────────────────

def call(path, body=None):
    """GET (body None) or POST JSON to /api/<path>; returns the decoded JSON. Raises on errors."""
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"{URL}/api/{path}", data=data, headers=headers,
                                 method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Kerf {path}: {e.read().decode(errors='replace')}") from None


def state():
    return call("state")


def new_tab(width=1200, height=900, keep_active=True):
    """Open an empty tab for the script and return its id. keep_active: the user keeps looking
    at (and other sessions keep editing) the tab that was active before."""
    before = state()["active"]
    tab = call("file/new", {"width": width, "height": height})["result"]
    if keep_active and before != tab and any(t["id"] == before for t in state()["tabs"]):
        call("file/activate", {"tab": before})
    return tab


def post(ops, label, tab):
    """Apply ops to a tab as ONE undo step; returns the per-op results. Always name the tab."""
    return call("ops", {"ops": ops, "label": label, "tab": tab}).get("results")


def save(file, tab):
    return call("file/save", {"file": file, "tab": tab})


def check(tab):
    """The same pre-cut checks as the check_cnc tool: {"ok", "issues", ...}."""
    return call(f"check?tab={tab}")


def layer(name, color, line_style="solid", export=True, depth=None, description=""):
    """add_layer op that also works when the layer exists (it is updated), so scripts can re-run."""
    return {"op": "add_layer", "name": name, "color": color, "line_style": line_style, "export": export,
            "depth": depth, "description": description, "exist_ok": True}


def part(name, pieces, qty=1, assembly=None):
    """Ops for one part: its shapes, the entity, qty and 3D placement. pieces = [(tag, layer, attrs)].
    Post them as one batch: post(part(...), name, tab)."""
    ops = [{"op": "add_element", "tag": t, "layer": l, "attrs": dict(a, fill="none")} for t, l, a in pieces]
    ops.append({"op": "group", "items": [f"${i}" for i in range(len(pieces))], "name": name})
    upd = {"op": "update_group", "id": f"${len(pieces)}", "qty": qty}
    if assembly:
        upd["assembly"] = assembly
    return ops + [upd]


# ── Outlines ─────────────────────────────────────────────────────────────────
# An outline is a list of vertices (x, y) in part coordinates. An ("arc", centre, via) item
# between two vertices makes that edge an arc through `via`. corners = {vertex index:
# ("fillet", r) | ("dog", r)}; indices count vertices only, not arc items.

def _unit(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n)


def _add(a, b, k=1.0):
    return (a[0] + k * b[0], a[1] + k * b[1])


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def build(verts, corners=None, auto_dog=0.0):
    """Closed outline → segments for to_path(). auto_dog=r puts a dog-bone of radius r on every
    concave corner (tenon shoulders); ("dog", r) on a hole's corners lets a square part fit in."""
    corners = dict(corners or {})
    pts, arcs = [], {}
    for v in verts:
        if v[0] == "arc":
            arcs[len(pts) - 1] = v[1:]
        else:
            pts.append(tuple(v))
    n = len(pts)
    area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
    if auto_dog:
        for i in range(n):
            p, v, q = pts[i - 1], pts[i], pts[(i + 1) % n]
            if ((v[0] - p[0]) * (q[1] - v[1]) - (v[1] - p[1]) * (q[0] - v[0])) * area < 0:
                corners.setdefault(i, ("dog", auto_dog))
    ends = []                                   # per vertex: (entry, (centre, via) | None, exit)
    for i, v in enumerate(pts):
        c = corners.get(i)
        if not c:
            ends.append((v, None, v))
            continue
        u1, u2 = _unit(_add(pts[i - 1], v, -1)), _unit(_add(pts[(i + 1) % n], v, -1))
        th = math.acos(max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1])))
        b, r = _unit(_add(u1, u2)), c[1]
        if c[0] == "fillet":
            t, cen = r / math.tan(th / 2), _add(v, b, r / math.sin(th / 2))
            ends.append((_add(v, u1, t), (cen, _add(cen, b, -r)), _add(v, u2, t)))
        else:                                   # dog-bone: circle through the corner
            s = 2 * r * math.cos(th / 2)
            ends.append((_add(v, u1, s), (_add(v, b, r), v), _add(v, u2, s)))
    segs = [("M", ends[0][0])]
    for i in range(n + 1):
        j = i % n
        if i:
            segs.append(("A", ends[j][0]) + arcs[i - 1] if (i - 1) in arcs else ("L", ends[j][0]))
        if i < n and ends[i][1]:
            segs.append(("A", ends[i][2]) + ends[i][1])
    return segs


def to_path(segs, f=lambda p: p):
    """Segments → SVG path d, each point mapped through f (part → drawing). Arc sweep and
    large-arc flags are computed in drawing space, so mirrored or turned parts stay right."""
    out, cur = [], None
    for s in segs:
        if s[0] == "M":
            cur = f(s[1])
            out.append("M %.3f %.3f" % cur)
        elif s[0] == "L":
            p = f(s[1])
            if math.hypot(p[0] - cur[0], p[1] - cur[1]) > 1e-6:
                out.append("L %.3f %.3f" % p)
            cur = p
        else:
            e, c, m = f(s[1]), f(s[2]), f(s[3])
            if math.hypot(e[0] - cur[0], e[1] - cur[1]) < 1e-6:
                continue
            r = math.hypot(e[0] - c[0], e[1] - c[1])
            ang = lambda p: math.atan2(p[1] - c[1], p[0] - c[0])
            span = (ang(e) - ang(cur)) % (2 * math.pi)
            sweep = 1 if (ang(m) - ang(cur)) % (2 * math.pi) < span else 0
            if not sweep:
                span = 2 * math.pi - span
            out.append("A %.3f %.3f 0 %d %d %.3f %.3f" % (r, r, span > math.pi, sweep, *e))
            cur = e
    return " ".join(out) + " Z"


def points(segs, step_deg=5):
    """Points along the outline (arcs sampled) — for bounds and checks, in part coordinates."""
    out, cur = [], None
    for s in segs:
        if s[0] in "ML":
            cur = s[1]
            out.append(cur)
            continue
        e, c, m = s[1], s[2], s[3]
        r = math.hypot(e[0] - c[0], e[1] - c[1])
        a0, a1, am = (math.atan2(p[1] - c[1], p[0] - c[0]) for p in (cur, e, m))
        span = (a1 - a0) % (2 * math.pi)
        if (am - a0) % (2 * math.pi) > span:
            span -= 2 * math.pi
        k = max(2, int(abs(math.degrees(span)) / step_deg))
        out += [(c[0] + r * math.cos(a0 + span * i / k), c[1] + r * math.sin(a0 + span * i / k)) for i in range(1, k + 1)]
        cur = e
    return out


def bounds(segs):
    ps = points(segs)
    return (min(p[0] for p in ps), min(p[1] for p in ps), max(p[0] for p in ps), max(p[1] for p in ps))


# ── Placement ────────────────────────────────────────────────────────────────

def place(box, X, Y, turn=0):
    """Put a part whose part-coordinate bounds are box = (x0, y0, x1, y1) (y up) on the drawing
    with its footprint's top-left at (X, Y), turned by turn ∈ {0, 90, 180, 270} degrees clockwise
    on the sheet. Returns (f, matrix): f maps part → drawing points (for to_path), matrix is the
    assembly matrix (drawing → part), so the 3D placement always matches the layout."""
    A = [[1, 0], [0, -1]]                        # y up → y down
    for _ in range(turn // 90 % 4):              # 90° clockwise on screen: (x, y) → (-y, x)
        A = [[-A[1][0], -A[1][1]], [A[0][0], A[0][1]]]
    lin = lambda p: (A[0][0] * p[0] + A[0][1] * p[1], A[1][0] * p[0] + A[1][1] * p[1])
    cs = [lin(p) for p in ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3]))]
    t = (X - min(c[0] for c in cs), Y - min(c[1] for c in cs))
    f = lambda p: (lin(p)[0] + t[0], lin(p)[1] + t[1])
    det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
    Ai = [[A[1][1] / det, -A[0][1] / det], [-A[1][0] / det, A[0][0] / det]]
    e = -(Ai[0][0] * t[0] + Ai[0][1] * t[1])
    g = -(Ai[1][0] * t[0] + Ai[1][1] * t[1])
    return f, [Ai[0][0], Ai[1][0], Ai[0][1], Ai[1][1], e, g]


def assembly(matrix, thickness, rotation=(0, 0, 0), position=(0, 0, 0), **extra):
    """Assembly dict (see the guide's placement recipes). extra: color, move={param, axis}."""
    return dict(matrix=[round(v, 6) for v in matrix], thickness=thickness, rotation=list(rotation),
                position=list(position), **extra)


def world_box(asm, pts):
    """World bounding box (x0, y0, z0, x1, y1, z1) of drawing points pts extruded by the assembly —
    to check a part lands where you meant before looking at a screenshot."""
    a, b, c, d, e, g = asm["matrix"]
    rx, ry, rz = (math.radians(v) for v in asm["rotation"])
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    R = [[cy * cz, -cy * sz, sy],                                   # Euler XYZ (three.js): Rx · Ry · Rz
         [cx * sz + sx * sy * cz, cx * cz - sx * sy * sz, -sx * cy],
         [sx * sz - cx * sy * cz, sx * cz + cx * sy * sz, cx * cy]]
    out = []
    for x, y in pts:
        lx, ly = a * x + c * y + e, b * x + d * y + g
        for z in (0, asm["thickness"]):
            out.append([sum(R[i][k] * (lx, ly, z)[k] for k in range(3)) + asm["position"][i] for i in range(3)])
    return tuple(min(p[i] for p in out) for i in range(3)) + tuple(max(p[i] for p in out) for i in range(3))
