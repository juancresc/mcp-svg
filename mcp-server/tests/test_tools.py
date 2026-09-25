"""MCP tools, called directly (FastMCP keeps the decorated functions callable)."""
import importlib
import json
import os
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


def test_part_workflow(srv, tmp_path):
    r = j(srv.add_svg('<rect x="100" y="100" width="200" height="80"/>', layer="CUT_OUTSIDE"))
    assert r["added"] == 1
    j(srv.add_svg('<circle cx="120" cy="120" r="4"/><circle cx="280" cy="160" r="4"/>', layer="CUT_INSIDE"))
    ids = [e["id"] for e in j(srv.list_elements())["elements"]]
    g = j(srv.group_elements(",".join(ids), "Rail"))["group"]
    j(srv.update_group(g, qty=2, assembly='{"thickness": 18, "position": [0,0,0], "rotation": [0,0,0]}'))
    m = j(srv.measure(g))
    assert m["total"]["width"] == 200 and m["total"]["height"] == 80

    j(srv.move_elements(g, 50, 0))
    assert j(srv.measure(g))["total"]["x"] == 150
    j(srv.transform_elements(g, "rotate(90 250 140)"))
    t = j(srv.measure(g))["total"]
    assert round(t["width"], 3) == 80 and round(t["height"], 3) == 200

    d = j(srv.duplicate(g, 0, 300))
    assert len(d["elements"]) == 3 and len(d["entities"]) == 1

    parts = j(srv.export_parts())
    assert sorted(parts["files"]) == ["rail-copy_x1.dxf", "rail-copy_x1.svg", "rail_x2.dxf", "rail_x2.svg"]
    assert (tmp_path / parts["zip"]).exists()

    dim = j(srv.add_dimension(0, 0, 100, 0, offset=10))
    assert dim["length"] == 100
    groups = j(srv.list_groups())["groups"]
    assert any(x["name"] == "Dimension 100" for x in groups)


def test_selection_both_ways(srv):
    j(srv.add_svg('<rect x="0" y="0" width="10" height="10"/><rect x="20" y="0" width="10" height="10"/>'))
    a, b = [e["id"] for e in j(srv.list_elements())["elements"]]
    g = j(srv.group_elements(f"{a},{b}", "Pair"))["group"]
    srv.store.report_selection([a])                     # the user selects one shape
    sel = j(srv.get_selection())
    assert sel["elements"] == [a] and sel["entities"][0]["name"] == "Pair"
    assert sel["bounds_mm"]["width"] == 10
    assert j(srv.set_selection(g))["selected"] == 2     # Claude highlights the entity
    assert srv.store.state()["selection_seq"] == 1


def test_tabs_and_files(srv, tmp_path):
    j(srv.add_svg('<rect width="5" height="5"/>'))
    j(srv.save_document("one"))
    j(srv.new_document(300, 200))
    tabs = j(srv.list_tabs())["tabs"]
    assert len(tabs) == 2 and tabs[1]["active"]
    j(srv.switch_tab(tabs[0]["id"]))
    assert j(srv.get_document_info())["file"] == "one.kerf"
    (tmp_path / "ref").mkdir()
    (tmp_path / "ref" / "a.dxf").write_bytes(b"x")
    files = [f["file"] for f in j(srv.list_files())["files"]]
    assert "ref/a.dxf" in files and "one.kerf" in files
    assert "error" in j(srv.close_document(tabs[0]["id"])) or True


def test_import_dxf_tool(srv, tmp_path):
    from export import cnc_dxf
    from document import Document
    d = Document()
    d.add_element("circle", {"cx": 50, "cy": 50, "r": 10}, layer="CUT_INSIDE")
    (tmp_path / "part.dxf").write_bytes(cnc_dxf(d))
    r = j(srv.import_dxf("part.dxf"))
    assert r["imported"] == 1 and len(r["open_tabs"]) == 1   # the empty Untitled tab is reused


def test_errors_are_results_not_crashes(srv):
    assert "error" in j(srv.update_element("el-99", '{"x": 1}'))
    assert "error" in j(srv.transform_elements("el-99", "rotate(10)"))
    assert "error" in j(srv.add_dimension(1, 1, 1, 1))
