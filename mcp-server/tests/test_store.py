import json

import pytest

from document import Document, DocError
from store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def add_rect(store, layer=None, **attrs):
    a = {"x": 10, "y": 10, "width": 100, "height": 50, **attrs}
    return store.apply([{"op": "add_element", "tag": "rect", "attrs": a, "layer": layer}])[0]


def test_add_update_remove_and_undo_redo(store):
    eid = add_rect(store)
    assert store.dirty and store.doc.element(eid).attrs["x"] == "10"
    store.apply([{"op": "update_element", "id": eid, "attrs": {"x": 50}}])
    assert store.doc.element(eid).attrs["x"] == "50"
    store.undo()
    assert store.doc.element(eid).attrs["x"] == "10"
    store.redo()
    assert store.doc.element(eid).attrs["x"] == "50"
    store.apply([{"op": "remove_elements", "ids": [eid]}])
    assert not store.doc.elements
    store.undo()
    assert store.doc.element(eid)


def test_failed_batch_changes_nothing(store):
    eid = add_rect(store)
    v = store.version
    with pytest.raises(DocError):
        store.apply([{"op": "update_element", "id": eid, "attrs": {"x": 1}},
                     {"op": "update_element", "id": "el-999", "attrs": {"x": 2}}])
    assert store.doc.element(eid).attrs["x"] == "10" and store.version == v


def test_typing_coalesces_into_one_undo_step(store):
    eid = add_rect(store)
    for x in (1, 12, 123):
        store.apply([{"op": "update_element", "id": eid, "attrs": {"x": x}}])
    store.undo()
    assert store.doc.element(eid).attrs["x"] == "10"


def test_layer_crud(store):
    store.apply([{"op": "add_layer", "name": "POCKET", "color": "#123456", "line_style": "dotted",
                  "description": "6 mm deep"}])
    eid = add_rect(store, layer="POCKET")
    store.apply([{"op": "update_layer", "name": "POCKET", "new_name": "POCKET_6", "color": "#654321"}])
    assert store.doc.element(eid).layer == "POCKET_6"
    assert store.doc.layer("POCKET_6").color == "#654321"
    with pytest.raises(DocError):  # has elements, no target
        store.apply([{"op": "remove_layer", "name": "POCKET_6"}])
    store.apply([{"op": "remove_layer", "name": "POCKET_6", "move_to": "ENGRAVE"}])
    assert store.doc.element(eid).layer == "ENGRAVE"
    with pytest.raises(DocError):
        store.apply([{"op": "add_layer", "name": "bad/name"}])
    with pytest.raises(DocError):
        store.apply([{"op": "update_layer", "name": "ENGRAVE", "color": "red"}])


def test_visibility_is_not_an_undo_step_and_not_dirty(store):
    store.save("a")
    store.apply([{"op": "set_layer_visibility", "name": "NOTES", "visible": False}])
    assert not store.dirty and not store.undo_stack
    assert not store.doc.layer("NOTES").visible


def test_default_layers_have_descriptions(store):
    assert all(l.description for l in store.doc.layers)
    assert store.doc.layer("NOTES").export is False


def test_save_open_roundtrip(store, tmp_path):
    store.apply([{"op": "set_size", "width": 2440, "height": 1220},
                 {"op": "add_layer", "name": "DRILL", "color": "#0000ff", "line_style": "4 2",
                  "export": False, "description": "hand drill"},
                 {"op": "set_layer_visibility", "name": "NOTES", "visible": False}])
    eid = add_rect(store, layer="DRILL")
    store.apply([{"op": "add_element", "tag": "text", "attrs": {"x": 5, "y": 5}, "text": "A & <B>",
                  "layer": "NOTES"}])
    saved = store.save("sub/part one")
    assert saved == "sub/part one.svg" and not store.dirty
    svg = (tmp_path / saved).read_text()
    assert 'width="2440mm"' in svg and 'viewBox="0 0 2440 1220"' in svg
    assert 'inkscape:groupmode="layer"' in svg and 'stroke="#0000ff"' in svg

    store.new(discard=True)
    store.open("sub/part one")
    d = store.doc
    assert (d.width, d.height) == (2440, 1220)
    drill = d.layer("DRILL")
    assert (drill.color, drill.line_style, drill.export, drill.description) == ("#0000ff", "4 2", False, "hand drill")
    assert not d.layer("NOTES").visible
    assert d.element(eid).layer == "DRILL" and "stroke" not in d.element(eid).attrs
    assert [e.text for e in d.elements if e.tag == "text"] == ["A & <B>"]
    assert d.next_id > int(eid.split("-")[1])


def test_cnc_export_skips_non_export_and_hidden_layers(store):
    add_rect(store, layer="CUT_OUTSIDE")
    add_rect(store, layer="NOTES")
    add_rect(store, layer="ENGRAVE")
    store.apply([{"op": "set_layer_visibility", "name": "ENGRAVE", "visible": False}])
    svg = store.doc.to_svg("cnc")
    assert svg.count("<rect") == 1 and 'width="800mm"' in svg and "inkscape" not in svg


def test_unsaved_changes_protect_open_and_new(store):
    store.save("one")
    add_rect(store)
    with pytest.raises(DocError, match="unsaved"):
        store.new()
    with pytest.raises(DocError, match="unsaved"):
        store.open("one")
    store.open("one", discard=True)
    assert not store.doc.elements


@pytest.mark.parametrize("name", ["../x", "/etc/passwd", ".hidden", "a/../../b", ""])
def test_file_names_stay_inside_data_dir(store, name):
    with pytest.raises(DocError):
        store.save(name)


def test_import_plain_and_legacy_svg(store):
    legacy = ('<svg xmlns="http://www.w3.org/2000/svg" width="500" height="400">'
              '<rect id="el-7" x="1" y="2" width="3" height="4" stroke="#e74c3c" data-layer="CUT_INSIDE"/>'
              '<g transform="translate(10,0)"><circle cx="5" cy="5" r="2" style="fill:none;stroke:red"/></g>'
              '</svg>')
    store.import_svg(legacy, replace=True, discard=True)
    d = store.doc
    assert (d.width, d.height) == (500, 400)
    assert d.element("el-7").layer == "CUT_INSIDE"
    circle = [e for e in d.elements if e.tag == "circle"][0]
    assert circle.attrs["transform"] == "translate(10,0)" and circle.attrs["fill"] == "none"
    # fragment import adds to the current document as one undo step
    n = store.import_svg('<line x1="0" y1="0" x2="5" y2="5"/><line x1="1" y1="1" x2="2" y2="2"/>', "NOTES")
    assert n == 2 and len(d.elements) == 2  # `d` is the pre-import snapshot
    store.undo()
    assert len(store.doc.elements) == 2


def test_session_survives_restart(tmp_path):
    s1 = Store(tmp_path)
    add_rect(s1)
    s1.flush_session()
    s2 = Store(tmp_path)
    assert len(s2.doc.elements) == 1 and s2.dirty


def test_wait_for_change_times_out(store):
    assert store.wait_for_change(store.version, 0.05) is False


def test_json_roundtrip():
    d = Document()
    d.add_element("circle", {"cx": 1, "cy": 2, "r": 3}, layer="CUT_INSIDE")
    d2 = Document.from_json(json.loads(json.dumps(d.to_json())))
    assert d2.to_json() == d.to_json()


def test_plain_svg_gets_standard_layers_in_order(store):
    store.import_svg('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                     '<rect data-layer="NOTES" width="1" height="1"/><rect data-layer="DRILL" width="1" height="1"/></svg>',
                     replace=True, discard=True)
    assert [l.name for l in store.doc.layers] == ["CUT_OUTSIDE", "CUT_INSIDE", "ENGRAVE", "NOTES", "DRILL"]
    assert store.doc.layer("CUT_INSIDE").description


def test_undo_back_to_saved_state_is_clean(store):
    store.save("clean")
    eid = add_rect(store)
    assert store.dirty
    store.undo()
    assert not store.dirty
    store.redo()
    assert store.dirty
    store.apply([{"op": "remove_elements", "ids": [eid]}])
    assert not store.dirty


def test_bad_attribute_names_are_rejected(store):
    for bad in ({"xlink:href": "#a"}, {"a b": "1"}, {"onclick": "alert(1)"}):
        with pytest.raises(DocError, match="attribute"):
            store.apply([{"op": "add_element", "tag": "rect", "attrs": bad}])
    eid = add_rect(store)
    with pytest.raises(DocError):
        store.apply([{"op": "update_element", "id": eid, "attrs": {"onmouseover": "x"}}])
    with pytest.raises(DocError):
        store.apply(["not an op"])


def test_import_scales_physical_units_to_mm(store):
    store.import_svg('<svg xmlns="http://www.w3.org/2000/svg" width="4in" height="2in" viewBox="0 0 384 192">'
                     '<rect x="0" y="0" width="96" height="96"/></svg>', replace=True, discard=True)
    d = store.doc
    assert (d.width, d.height) == (101.6, 50.8)
    assert d.elements[0].attrs["transform"].startswith("scale(0.264583")
    store.import_svg('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="10 10 100 50">'
                     '<circle cx="20" cy="20" r="5"/></svg>', replace=True, discard=True)
    assert store.doc.elements[0].attrs["transform"] == "translate(-10, -10)"


def test_import_drops_hidden_unsafe_and_namespaced(store):
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" viewBox="0 0 100 100">'
           '<rect width="1" height="1" style="display:none"/>'
           '<g style="display:none"><circle r="3"/></g>'
           '<path d="M0 0 L5 5" sodipodi:type="arc" onmouseover="alert(1)"/>'
           '<text x="1" y="2" style="text-anchor:middle;font-weight:bold"><tspan x="1" y="2">Line1</tspan><tspan x="1" y="8">Line2</tspan></text>'
           '</svg>')
    store.import_svg(svg, replace=True, discard=True)
    els = store.doc.elements
    assert [e.tag for e in els] == ["path", "text", "text"]
    assert els[0].attrs == {"d": "M0 0 L5 5"}
    assert [e.text for e in els[1:]] == ["Line1", "Line2"]
    assert els[2].attrs["y"] == "8" and els[1].attrs["text-anchor"] == "middle"


def test_state_hides_background_data_and_consumes_screenshot(store):
    store.apply([{"op": "set_background", "href": "data:image/png;base64,AAAA", "opacity": 0.5}])
    bg = store.state()["doc"]["background"]
    assert "href" not in bg and bg["opacity"] == 0.5 and bg["id"]
    store.screenshot_requested = True
    assert store.state(consume_screenshot=True)["screenshot_requested"] is True
    assert store.screenshot_requested is False
