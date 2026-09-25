"""kerf_script.py (the helpers served at /api/script) and the guide's Parametric scripts example."""
import asyncio
import importlib
import json
import re
import sys

import pytest

import kerf_script as k


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    sys.modules.pop("server", None)
    import server
    importlib.reload(server)
    return server


@pytest.fixture
def wired(srv, monkeypatch):
    """kerf_script.call routed straight to the server's Store (no network)."""
    st = srv.store

    def call(path, body=None):
        path, _, query = path.partition("?")
        if path == "state":
            return st.state()
        if path == "file/new":
            return {"result": st.new(body.get("width", 800), body.get("height", 600))}
        if path == "file/activate":
            return {"result": st.activate(body["tab"])}
        if path == "ops":
            return {"results": st.apply(body["ops"], body.get("label"), body.get("tab"))}
        if path == "file/save":
            return {"result": st.save(body["file"], body.get("tab"))}
        if path == "check":
            return srv.cnc_issues(st.get_tab(dict(q.split("=") for q in query.split("&"))["tab"]).doc)
        raise AssertionError(path)
    monkeypatch.setattr(k, "call", call)
    return srv


def close(a, b, eps=1e-6):
    return all(abs(x - y) < eps for x, y in zip(a, b))


def test_outline_bounds_fillets_and_dogbones():
    plate = k.build(k.rect(0, 0, 200, 100), {i: ("fillet", 10) for i in range(4)})
    assert close(k.bounds(plate), (0, 0, 200, 100), 1e-3)
    d = k.to_path(plate)
    assert d.count(" A ") == 4 and d.endswith("Z")
    slot = k.build(k.rect(10, 10, 90, 28.4), {i: ("dog", 3.2) for i in range(4)})
    x0, y0, x1, y1 = k.bounds(slot)
    assert x0 < 10 and y0 < 10 and x1 > 90 and y1 > 28.4        # dog-bones reach past the corners
    # auto_dog: a board with one tenon on the right has exactly 2 concave corners
    board = k.build([(0, 0), (100, 0), (100, 20), (118, 20), (118, 60), (100, 60), (100, 80), (0, 80)], auto_dog=3.2)
    assert sum(1 for s in board if s[0] == "A") == 2


def test_arc_edge_flags_follow_the_via_point():
    # arch notch in the bottom edge of a 500 × 300 panel (part y up), bulging up into the part
    arch = k.build([(0, 0), (100, 0), ("arc", (250, -200), (250, 50)), (400, 0), (500, 0), (500, 300), (0, 300)])
    assert close(k.bounds(arch), (0, 0, 500, 300), 1e-3)
    for turn in (0, 90, 180, 270):
        f, m = k.place(k.bounds(arch), 10, 20, turn)
        d = k.to_path(arch, f)
        a = re.search(r"A ([\d.]+) [\d.]+ 0 (\d) (\d)", d)
        assert a and a.group(2) == "0"                          # < 180°: never the large arc
        xs = [f(p) for p in k.points(arch)]
        w, h = (500, 300) if turn in (0, 180) else (300, 500)
        assert close((min(p[0] for p in xs), min(p[1] for p in xs)), (10, 20), 1e-3)
        assert close((max(p[0] for p in xs), max(p[1] for p in xs)), (10 + w, 20 + h), 1e-3)


@pytest.mark.parametrize("turn", [0, 90, 180, 270])
def test_place_matrix_inverts_the_mapping(turn):
    f, m = k.place((-45, -215, 77, 300), 300, 40, turn)
    a, b, c, d, e, g = m
    for p in [(-45, -215), (77, 300), (0, 0), (12.5, -80)]:
        x, y = f(p)
        assert close((a * x + c * y + e, b * x + d * y + g), p)


def test_world_box_side_panel_recipe():
    side = k.build(k.rect(0, 0, 300, 700))
    f, m = k.place(k.bounds(side), 15, 15)
    box = k.world_box(k.assembly(m, 18, (0, 90, 0), (-209, 0, 150)), [f(p) for p in k.points(side)])
    assert close(box, (-209, 0, -150, -191, 700, 150), 1e-6)   # depth runs to −Z, thickness to +X


def test_layer_exist_ok_updates_instead_of_failing(srv):
    ops = [k.layer("POCKET", "#123456", depth=6)]
    srv.store.apply(ops)
    srv.store.apply([k.layer("POCKET", "#654321", depth=8)])
    lay = srv.store.doc.layer("POCKET")
    assert (lay.color, lay.depth) == ("#654321", 8)
    with pytest.raises(Exception):
        srv.store.apply([{"op": "add_layer", "name": "POCKET", "color": "#000000"}])


def test_guide_example_runs_and_passes_check(wired, tmp_path):
    guide = wired.get_guide("scripts")
    assert guide.startswith("## Parametric scripts")
    code = re.search(r"```python\n(.*?)```", guide, re.S).group(1)
    user_tab = wired.store.state()["active"]
    out = {}
    code = code.replace("print(k.check(tab))", "out['check'] = k.check(tab)")
    exec(code, {"kerf_script": k, "out": out, "__name__": "example"})
    st = wired.store
    assert st.state()["active"] == user_tab or len(st.tabs) == 1     # user's tab stays active
    assert out["check"]["ok"], out["check"]
    assert (tmp_path / "shelf" / "shelf.kerf").exists()
    doc = json.loads((tmp_path / "shelf" / "shelf.kerf").read_text())["document"]
    assert [g["name"] for g in doc["groups"]] == ["Side L"]
    # re-running the same script into the same tab is safe (clear + exist_ok layers)
    tab = st.state()["tabs"][-1]["id"]
    exec(code.replace("tab = k.new_tab(1200, 900)", f"tab = {tab!r}"), {"kerf_script": k, "out": out, "__name__": "example"})
    assert len(st.get_tab(tab).doc.groups) == 1


def test_script_and_check_endpoints(srv):
    from aiohttp.test_utils import make_mocked_request
    r = asyncio.run(srv.get_script(make_mocked_request("GET", "/api/script")))
    assert "def place(" in r.text and r.content_type == "text/x-python"
    tab = srv.store.state()["active"]
    r = asyncio.run(srv.get_check(make_mocked_request("GET", f"/api/check?tab={tab}")))
    assert json.loads(r.body)["ok"] is True
    r = asyncio.run(srv.get_check(make_mocked_request("GET", "/api/check?tab=nope")))
    assert r.status == 400
