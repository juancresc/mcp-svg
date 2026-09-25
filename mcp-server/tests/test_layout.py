"""Moving parts keeps their 3D placement; named references; arrange onto sheets; layout checks."""
import importlib
import json
import sys

import pytest


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    sys.modules.pop("server", None)
    import server
    importlib.reload(server)
    return server


def j(s):
    return json.loads(s)


def board(name, x, y, w, h, asm=None, holes=()):
    """Ops for a rectangular part with named references."""
    ops = [{"op": "add_element", "as": f"{name}.o", "tag": "rect", "layer": "CUT_OUTSIDE",
            "attrs": {"x": x, "y": y, "width": w, "height": h, "fill": "none"}}]
    for i, (cx, cy, r) in enumerate(holes):
        ops.append({"op": "add_element", "as": f"{name}.h{i}", "tag": "circle", "layer": "CUT_INSIDE",
                    "attrs": {"cx": cx, "cy": cy, "r": r, "fill": "none"}})
    ops.append({"op": "group", "as": name, "items": [f"${o['as']}" for o in ops], "name": name})
    if asm is not None:
        ops.append({"op": "update_group", "id": f"${name}", "assembly": asm})
    return ops


def boxes(srv):
    return {p["name"]: p["box"] for p in j(srv.describe_assembly())["parts"]}


def test_named_refs_and_native_json(srv):
    r = j(srv.apply_ops(board("Plate", 10, 20, 300, 200, {"rotation": [-90, 0, 0], "position": [0, 0, 0]},
                              holes=[(40, 50, 5)])))
    gid = r["results"][2]
    g = srv.store.doc.group_by_id(gid)
    assert g.name == "Plate" and len(srv.store.doc.descendants(gid)) == 2
    assert g.assembly["matrix"] == [1, 0, 0, -1, -10, 220]          # bottom-left of the drawing, y up
    assert boxes(srv)["Plate"] == [0, 0, -200, 300, 18, 0]           # flat board, front edge at z = 0
    # tools take real JSON objects and id lists, not only strings
    eid = j(srv.add_element("rect", {"x": 0, "y": 0, "width": 5, "height": 5}, layer="NOTES"))["id"]
    j(srv.update_element(eid, {"x": 3}))
    assert srv.store.doc.element(eid).attrs["x"] == "3"
    assert j(srv.measure([gid, eid]))["total"]["x"] == 3
    err = j(srv.apply_ops([{"op": "group", "items": ["$nope"]}]))
    assert "no earlier op" in err["error"]
    err = j(srv.apply_ops([{"op": "add_element", "tag": "rect", "attrs": {}}, {"op": "ungroup", "id": "g-99"}]))
    assert err["error"].startswith("op 1 (ungroup)")


def test_moving_and_turning_parts_keeps_them_in_place_in_3d(srv):
    asm = {"rotation": [0, 90, 0], "position": [-209, 0, 150]}
    gid = j(srv.apply_ops(board("Side", 15, 15, 300, 700, asm, holes=[(100, 100, 4)])))["results"][2]
    before = boxes(srv)["Side"]
    assert before == [-209, 0, -150, -191, 700, 150]
    j(srv.move_elements(gid, 500, -3))
    j(srv.transform_elements([gid], "rotate(90 800 400)"))
    j(srv.transform_elements(gid, "scale(-1 1) translate(-3000 0)"))   # even a mirror
    assert boxes(srv)["Side"] == before
    # updating the placement without a matrix keeps the (moved) matrix
    m = srv.store.doc.group_by_id(gid).assembly["matrix"]
    j(srv.update_group(gid, assembly={"rotation": [0, 90, 0], "position": [-209, 0, 160]}))
    assert srv.store.doc.group_by_id(gid).assembly["matrix"] == m
    assert boxes(srv)["Side"][2] == -140
    # "auto" recomputes it from the drawing as it is now (turned, so the box changes)
    j(srv.update_group(gid, assembly={"rotation": [0, 90, 0], "position": [-209, 0, 150], "matrix": "auto"}))
    assert boxes(srv)["Side"] != before
    # moving only some of a part's shapes is an edit of the part, not a move: matrix unchanged
    m = srv.store.doc.group_by_id(gid).assembly["matrix"]
    hole = [e for e in srv.store.doc.elements if e.tag == "circle"][0].id
    j(srv.move_elements(hole, 5, 5))
    assert srv.store.doc.group_by_id(gid).assembly["matrix"] == m


def test_browser_move_op_and_nudge_coalescing(srv):
    st = srv.store
    gid = st.apply(board("A", 0, 0, 100, 50, {"rotation": [0, 0, 0], "position": [0, 0, 0]}))[2]
    ids = st.doc.descendants(gid)
    before = boxes(srv)["A"]
    n = len(st.tab.undo_stack)
    for _ in range(3):                              # arrow key nudges: one undo step
        st.apply([{"op": "move", "items": ids, "dx": 1, "dy": 0, "coalesce": "nudge"}])
    assert len(st.tab.undo_stack) == n + 1
    assert st.doc.element(ids[0]).attrs["x"] == "3"
    assert boxes(srv)["A"] == before


def test_arrange_packs_parts_on_sheets_and_keeps_3d(srv):
    srv.set_material(sheet_width=1220, sheet_height=610)
    ops = []
    sizes = [(1000, 400), (500, 300), (500, 300), (400, 1100), (300, 200), (200, 150), (2000, 300)]
    for i, (w, h) in enumerate(sizes):
        ops += board(f"P{i}", 0, 0, w, h, {"rotation": [0, 0, 0], "position": [i * 100, 0, 0]}, holes=[(20, 20, 4)])
    j(srv.apply_ops(ops))
    assert not j(srv.check_cnc())["ok"]            # all piled up at the origin
    before = boxes(srv)
    r = j(srv.arrange_parts(notes=["CUT ORDER: holes, then outlines", "Hardware: none"]))
    assert r["too_big"] == ["P6"]                  # 2000 long doesn't fit a 1220 sheet
    assert "P3" in r["turned"]                      # 400 × 1100 only fits turned
    assert r["sheets"] >= 2
    assert boxes(srv) == before                     # 3D unchanged
    c = j(srv.check_cnc())
    kinds = {i["kind"] for i in c["issues"]}
    assert kinds <= {"bigger_than_sheet", "off_sheet"}, c
    assert all(i["ids"] == [g] for i in c["issues"] for g in [i["ids"][0]]
               if srv.store.doc.group_by_id(g).name == "P6")
    d = srv.store.doc
    assert sum(1 for e in d.elements if e.layer == "SHEETS" and e.tag == "rect") == r["sheets"]
    notes = next(g for g in d.groups if g.name == "Notes")
    assert len(d.descendants(notes.id)) == 2
    # again: sheets are redrawn, not duplicated; the notes entity is replaced
    r2 = j(srv.arrange_parts(notes=["Only one line"]))
    assert sum(1 for e in srv.store.doc.elements if e.layer == "SHEETS" and e.tag == "rect") == r2["sheets"]
    d = srv.store.doc
    assert len(d.descendants(next(g for g in d.groups if g.name == "Notes").id)) == 1
    assert boxes(srv) == before
    j(srv.undo())                                   # one undo step
    assert len(srv.store.doc.descendants(next(g for g in srv.store.doc.groups if g.name == "Notes").id)) == 2


def test_check_cnc_finds_overlapping_close_and_off_sheet_parts(srv):
    j(srv.apply_ops(board("A", 0, 0, 200, 200) + board("B", 100, 100, 200, 200)
                    + board("C", 400, 0, 100, 100) + board("D", 503, 0, 100, 100)
                    # E sits inside the window of F: that's fine
                    + board("F", 0, 400, 400, 400)
                    + [{"op": "add_element", "tag": "rect", "layer": "CUT_INSIDE", "group": "$F",
                        "attrs": {"x": 50, "y": 450, "width": 300, "height": 300, "fill": "none"}}]
                    + board("E", 100, 500, 100, 100)))
    issues = [i for i in j(srv.check_cnc())["issues"] if i["kind"].startswith("parts_")]
    pairs = {(i["kind"], tuple(sorted(srv.store.doc.group_by_id(g).name for g in i["ids"]))) for i in issues}
    assert ("parts_overlap", ("A", "B")) in pairs
    assert ("parts_too_close", ("C", "D")) in pairs          # 3 mm apart, 6 mm tool
    assert not any("E" in p[1] for p in pairs)
    # off_sheet only once there are sheets
    j(srv.apply_ops([{"op": "add_layer", "name": "SHEETS", "export": False},
                     {"op": "add_element", "tag": "rect", "layer": "SHEETS",
                      "attrs": {"x": 0, "y": 0, "width": 450, "height": 1000, "fill": "none"}}]))
    off = {srv.store.doc.group_by_id(i["ids"][0]).name for i in j(srv.check_cnc())["issues"] if i["kind"] == "off_sheet"}
    assert off == {"C", "D"}


def test_describe_assembly_finds_clashes_not_joints(srv):
    flat = {"rotation": [-90, 0, 0]}
    side = {"rotation": [0, 90, 0]}
    j(srv.apply_ops(board("Top", 0, 0, 600, 400, {**flat, "position": [-300, 700, 200]})
                    + board("Shelf", 0, 500, 600, 400, {**flat, "position": [-300, 682, 200]})   # right under the top
                    + board("Side L", 700, 0, 400, 700, {**side, "position": [-300, 0, 200]})
                    + board("Side R", 1200, 0, 400, 700, {**side, "position": [-300, 0, 200]})  # forgot to move it
                    + board("Loose", 0, 1000, 100, 100)))
    r = j(srv.describe_assembly())
    assert r["model_box"] == [-300, 0, -200, 300, 718, 200]
    assert r["not_in_3d"][0]["name"] == "Loose"
    assert [c["parts"] for c in r["clashes"]] == [["Side L", "Side R"]]
    assert r["clashes"][0]["inside_pct"] == [100, 100]
    j(srv.update_group(next(g.id for g in srv.store.doc.groups if g.name == "Side R"),
                       assembly={**side, "position": [282, 0, 200]}))
    assert j(srv.describe_assembly())["clashes"] == []
    # the shelf sunk halfway into the top is a clash too
    j(srv.update_group(next(g.id for g in srv.store.doc.groups if g.name == "Shelf"),
                       assembly={**flat, "position": [-300, 709, 200]}))
    assert [c["parts"] for c in j(srv.describe_assembly())["clashes"]] == [["Top", "Shelf"]]
    assert j(srv.check_cnc())["warnings"][0]["kind"] == "not_in_3d"
    gid = next(g.id for g in srv.store.doc.groups if g.name == "Top")
    assert j(srv.describe_entity(gid))["world_box_mm"] == [-300, 700, -200, 300, 718, 200]


def test_arrange_packs_the_pieces_inside_an_assembly_entity(srv):
    flat = {"rotation": [-90, 0, 0], "position": [0, 0, 0]}
    ops = []
    for i, (w, h) in enumerate([(1000, 500), (1000, 500), (500, 200), (500, 200)]):
        ops += board(f"Drawer part {i}", i * 2000, 3000, w, h, flat)       # spread far apart
    ops.append({"op": "group", "as": "drawer", "items": [f"$Drawer part {i}" for i in range(4)], "name": "Drawer"})
    j(srv.apply_ops(ops))
    before = boxes(srv)
    r = j(srv.arrange_parts())
    assert r["sheets"] == 1 and len(r["parts"]) == 4          # the parts, not one 7000-wide "Drawer"
    assert boxes(srv) == before
    assert j(srv.check_cnc())["ok"]
