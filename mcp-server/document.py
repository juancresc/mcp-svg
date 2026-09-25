"""Document model: layers + elements, SVG file (de)serialization.

Units: 1 document unit = 1 mm. Files are written as SVG with width/height in mm and a
viewBox, and layers as Inkscape layers (<g inkscape:groupmode="layer">), so a saved file
opens correctly in Inkscape and CAM software and can be cut as-is (minus non-export layers).

Stroke colour and line style belong to the layer (CNC colour convention); element
attributes never carry them in the model — they are applied when rendering/serializing.
"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from xml.sax.saxutils import escape, quoteattr

SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
SHAPE_TAGS = ("line", "rect", "circle", "ellipse", "text", "path", "polygon", "polyline")
# Attributes owned by the layer or by the model itself
LAYER_OWNED = {"stroke", "stroke-dasharray", "stroke-linecap", "id", "data-layer", "style"}

LINE_STYLES = {"solid": "", "dashed": "6 3", "dotted": "0.5 2.5"}
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class DocError(ValueError):
    """User-facing validation error."""


@dataclass
class Layer:
    name: str
    color: str = "#000000"
    line_style: str = "solid"      # solid | dashed | dotted | custom dasharray "a b"
    visible: bool = True
    locked: bool = False
    export: bool = True            # included in CNC export
    description: str = ""

    def dasharray(self) -> str:
        return LINE_STYLES.get(self.line_style, self.line_style)


@dataclass
class Element:
    id: str
    tag: str
    layer: str
    attrs: dict[str, str] = field(default_factory=dict)
    text: str = ""


DEFAULT_LAYER_DESCRIPTIONS = {
    "CUT_OUTSIDE": "Through-cut along the OUTSIDE of the line: part outlines. CAM offsets the tool "
                   "outward by its radius so the part keeps its drawn size. Cut these last (use tabs).",
    "CUT_INSIDE": "Through-cut along the INSIDE of the line: holes, slots, windows. CAM offsets the "
                  "tool inward so the hole keeps its drawn size. Cut these before the outlines.",
    "ENGRAVE": "Partial-depth work ON the line (no offset): marks, labels, drill points, pockets. "
               "Set the depth in CAM; text must be converted to paths.",
    "NOTES": "Reference only: dimensions, labels, sheet outlines. Never cut; left out of CNC export.",
}


def default_layers() -> list[Layer]:
    d = DEFAULT_LAYER_DESCRIPTIONS
    return [
        Layer("CUT_OUTSIDE", "#e74c3c", "solid", description=d["CUT_OUTSIDE"]),
        Layer("CUT_INSIDE", "#e74c3c", "dashed", description=d["CUT_INSIDE"]),
        Layer("ENGRAVE", "#3498db", "solid", description=d["ENGRAVE"]),
        Layer("NOTES", "#2ecc71", "solid", export=False, description=d["NOTES"]),
    ]


def validate_layer_fields(color=None, line_style=None, name=None):
    if name is not None and not re.fullmatch(r"[A-Za-z0-9 _\-.]{1,40}", name):
        raise DocError(f"Invalid layer name {name!r}: use 1-40 letters, digits, space, _ - .")
    if color is not None and not HEX_COLOR.match(color):
        raise DocError(f"Invalid color {color!r}: use #rrggbb")
    if line_style is not None and line_style not in LINE_STYLES and not re.fullmatch(r"[\d.]+([ ,]+[\d.]+)*", line_style):
        raise DocError(f"Invalid line style {line_style!r}: solid, dashed, dotted or a dasharray like '4 2'")


@dataclass
class Document:
    width: float = 800
    height: float = 600
    layers: list[Layer] = field(default_factory=default_layers)
    elements: list[Element] = field(default_factory=list)
    background: dict | None = None          # {"href": data-uri, "opacity": float}
    next_id: int = 1

    # ── lookup ─────────────────────────────────────────────
    def layer(self, name: str) -> Layer:
        for l in self.layers:
            if l.name == name:
                return l
        raise DocError(f"Layer '{name}' not found")

    def has_layer(self, name: str) -> bool:
        return any(l.name == name for l in self.layers)

    def element(self, eid: str) -> Element:
        for e in self.elements:
            if e.id == eid:
                return e
        raise DocError(f"Element '{eid}' not found")

    def new_id(self) -> str:
        eid = f"el-{self.next_id}"
        self.next_id += 1
        return eid

    # ── elements ───────────────────────────────────────────
    def add_element(self, tag: str, attrs: dict, text: str = "", layer: str | None = None) -> Element:
        check_attr_names(attrs)
        if tag not in SHAPE_TAGS:
            raise DocError(f"Unsupported tag '{tag}'. Use one of: {', '.join(SHAPE_TAGS)}")
        layer = layer or self.layers[0].name
        self.layer(layer)
        el = Element(self.new_id(), tag, layer, clean_attrs(attrs), text or "")
        self.elements.append(el)
        return el

    def update_element(self, eid: str, attrs: dict | None = None, text: str | None = None,
                       layer: str | None = None) -> Element:
        el = self.element(eid)
        if attrs is not None and not isinstance(attrs, dict):
            raise DocError("attrs must be an object")
        check_attr_names(attrs)
        for k, v in (attrs or {}).items():
            if k in LAYER_OWNED:
                continue
            if v is None or v == "":
                el.attrs.pop(k, None)
            else:
                el.attrs[k] = str(v)
        if text is not None:
            el.text = text
        if layer:
            self.layer(layer)
            el.layer = layer
        return el

    def remove_elements(self, ids: list[str]) -> int:
        ids = set(ids)
        missing = ids - {e.id for e in self.elements}
        if missing:
            raise DocError(f"Element(s) not found: {', '.join(sorted(missing))}")
        self.elements = [e for e in self.elements if e.id not in ids]
        return len(ids)

    def reorder_element(self, eid: str, where: str):
        el = self.element(eid)
        self.elements.remove(el)
        if where == "front":
            self.elements.append(el)
        elif where == "back":
            self.elements.insert(0, el)
        else:
            raise DocError("where must be 'front' or 'back'")

    # ── layers ─────────────────────────────────────────────
    def add_layer(self, name: str, color: str = "#000000", line_style: str = "solid",
                  export: bool = True, visible: bool = True, locked: bool = False,
                  description: str = "") -> Layer:
        validate_layer_fields(color, line_style, name)
        if self.has_layer(name):
            raise DocError(f"Layer '{name}' already exists")
        layer = Layer(name, color, line_style, visible, locked, export, (description or "")[:500])
        self.layers.append(layer)
        return layer

    def update_layer(self, name: str, new_name: str | None = None, color: str | None = None,
                     line_style: str | None = None, visible: bool | None = None,
                     locked: bool | None = None, export: bool | None = None,
                     description: str | None = None) -> Layer:
        layer = self.layer(name)
        validate_layer_fields(color, line_style, new_name)
        if new_name and new_name != name:
            if self.has_layer(new_name):
                raise DocError(f"Layer '{new_name}' already exists")
            layer.name = new_name
            for e in self.elements:
                if e.layer == name:
                    e.layer = new_name
        if color is not None: layer.color = color
        if line_style is not None: layer.line_style = line_style
        if visible is not None: layer.visible = bool(visible)
        if locked is not None: layer.locked = bool(locked)
        if export is not None: layer.export = bool(export)
        if description is not None: layer.description = description[:500]
        return layer

    def remove_layer(self, name: str, move_to: str | None = None) -> int:
        self.layer(name)
        if len(self.layers) == 1:
            raise DocError("Cannot remove the last layer")
        members = [e for e in self.elements if e.layer == name]
        if members:
            if not move_to:
                raise DocError(f"Layer '{name}' has {len(members)} element(s): pass move_to=<layer> "
                               f"to keep them, or delete them first")
            if move_to == "__delete__":
                self.elements = [e for e in self.elements if e.layer != name]
            else:
                self.layer(move_to)
                for e in members:
                    e.layer = move_to
        self.layers = [l for l in self.layers if l.name != name]
        return len(members)

    def move_layer(self, name: str, index: int):
        layer = self.layer(name)
        self.layers.remove(layer)
        self.layers.insert(max(0, min(index, len(self.layers))), layer)

    # ── size ───────────────────────────────────────────────
    def set_size(self, width: float, height: float):
        if not (0 < float(width) <= 100000 and 0 < float(height) <= 100000):
            raise DocError("Width and height must be between 0 and 100000 mm")
        self.width, self.height = num(width), num(height)

    # ── (de)serialization: JSON ────────────────────────────
    def to_json(self) -> dict:
        return {
            "width": self.width, "height": self.height,
            "layers": [asdict(l) for l in self.layers],
            "elements": [asdict(e) for e in self.elements],
            "background": self.background,
            "next_id": self.next_id,
        }

    @classmethod
    def from_json(cls, d: dict) -> "Document":
        doc = cls(width=d["width"], height=d["height"],
                  layers=[Layer(**l) for l in d["layers"]],
                  elements=[Element(**e) for e in d["elements"]],
                  background=d.get("background"), next_id=d.get("next_id", 1))
        doc._fix_next_id()
        return doc

    def clone(self) -> "Document":
        return copy.deepcopy(self)

    def _fix_next_id(self):
        for e in self.elements:
            m = re.fullmatch(r"el-(\d+)", e.id)
            if m:
                self.next_id = max(self.next_id, int(m.group(1)) + 1)

    # ── (de)serialization: SVG ─────────────────────────────
    def to_svg(self, mode: str = "file") -> str:
        """mode: 'file' (all layers, Inkscape layers, background), 'cnc' (export+visible layers,
        no background, no ids), 'screen' (plain, for previews)."""
        w, h = fmt(self.width), fmt(self.height)
        head = [f'<svg xmlns="{SVG_NS}"']
        if mode == "file":
            head.append(f' xmlns:inkscape="{INKSCAPE_NS}"')
        head.append(f' width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}">')
        out = ["".join(head)]
        if mode == "file" and self.background:
            out.append(f'  <image data-role="background" x="0" y="0" width="{w}" height="{h}" '
                       f'preserveAspectRatio="xMidYMid meet" opacity="{self.background.get("opacity", 0.3)}" '
                       f'href={quoteattr(self.background["href"])}/>')
        for i, layer in enumerate(self.layers):
            if mode == "cnc" and not (layer.export and layer.visible):
                continue
            members = [e for e in self.elements if e.layer == layer.name]
            if mode == "cnc" and not members:
                continue
            g = f'  <g id="layer{i + 1}" inkscape:groupmode="layer" inkscape:label={quoteattr(layer.name)}' \
                if mode == "file" else f'  <g id={quoteattr(layer.name)}'
            if mode == "file":
                g += (f' data-color="{layer.color}" data-line-style={quoteattr(layer.line_style)}'
                      f' data-export="{str(layer.export).lower()}" data-locked="{str(layer.locked).lower()}"'
                      f' data-description={quoteattr(layer.description)}'
                      f'{"" if layer.visible else " style=" + quoteattr("display:none")}')
            out.append(g + ">")
            for e in members:
                out.append("    " + element_svg(e, layer, with_id=(mode != "cnc")))
            out.append("  </g>")
        out.append("</svg>")
        return "\n".join(out)

    @classmethod
    def from_svg(cls, markup: str, default_layer: str | None = None,
                 base: "Document | None" = None) -> "Document":
        """Parse an SVG file. Recognizes our/Inkscape layers, data-layer attributes and plain
        SVGs. With `base`, elements are appended to a copy of it (import) instead of a new doc."""
        body = re.sub(r"^\s*(<\?xml[^>]*\?>)?\s*(<!DOCTYPE[^>]*>)?\s*", "", markup or "")
        if not re.match(r"<(\w+:)?svg[\s>]", body):
            # Fragment (one or more elements): wrap in an <svg>
            body = f'<svg xmlns="{SVG_NS}">{body}</svg>'
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            raise DocError(f"Invalid SVG: {e}")

        doc = base.clone() if base else cls(layers=[])
        width_mm, height_mm, root_transform = root_geometry(root)
        if not base:
            doc.width, doc.height = width_mm, height_mm

        def ensure_layer(name, g=None):
            if doc.has_layer(name):
                return
            defaults = {l.name: l for l in default_layers()}
            layer = copy.copy(defaults.get(name, Layer(name, "#000000")))
            if g is not None:
                if HEX_COLOR.match(g.get("data-color") or ""): layer.color = g.get("data-color")
                if g.get("data-line-style"): layer.line_style = g.get("data-line-style")
                if g.get("data-export"): layer.export = g.get("data-export") == "true"
                if g.get("data-locked"): layer.locked = g.get("data-locked") == "true"
                if g.get("data-description") is not None: layer.description = g.get("data-description")
                if "display:none" in (g.get("style") or "").replace(" ", ""): layer.visible = False
            doc.layers.append(layer)

        fallback = default_layer or (doc.layers[0].name if doc.layers else "CUT_OUTSIDE")
        found_real_layers = False

        def walk(node, layer_name, transform):
            for child in node:
                tag = local(child.tag)
                if tag == "image" and child.get("data-role") == "background" and not base:
                    href = child.get("href") or child.get("{http://www.w3.org/1999/xlink}href")
                    if href:
                        doc.background = {"href": href, "opacity": float(child.get("opacity") or 0.3)}
                    continue
                if tag == "g":
                    label = child.get(f"{{{INKSCAPE_NS}}}label")
                    is_layer = child.get(f"{{{INKSCAPE_NS}}}groupmode") == "layer"
                    if is_hidden(child) and not (is_layer and label):
                        continue  # hidden group: never import (it would get cut)
                    name = layer_name
                    if is_layer and label:
                        nonlocal found_real_layers
                        found_real_layers = True
                        name = sanitize_layer_name(label)
                        ensure_layer(name, child)
                    t = " ".join(x for x in (transform, child.get("transform")) if x)
                    walk(child, name, t)
                    continue
                if tag not in SHAPE_TAGS or is_hidden(child):
                    continue
                name = sanitize_layer_name(child.get("data-layer")) if child.get("data-layer") else layer_name
                ensure_layer(name)
                attrs = {k: v for k, v in child.attrib.items() if "}" not in k}   # drop namespaced
                attrs.update(style_attrs(attrs.pop("style", "")))
                if transform:
                    attrs["transform"] = " ".join(x for x in (transform, attrs.get("transform")) if x)
                eid = attrs.get("id", "")
                if not re.fullmatch(r"el-\d+", eid) or any(e.id == eid for e in doc.elements):
                    eid = ""
                lines = text_lines(child) if tag == "text" else None
                if lines:  # multi-line text (positioned <tspan>s): one text element per line
                    for i, (line_attrs, line) in enumerate(lines):
                        doc.elements.append(Element(eid if i == 0 else "", tag, name,
                                                    clean_attrs({**attrs, **line_attrs}), line))
                    continue
                el = Element(eid, tag, name, clean_attrs(attrs), "".join(child.itertext()) if tag == "text" else "")
                doc.elements.append(el)

        walk(root, fallback, root_transform)
        if not base and not found_real_layers:
            # Plain SVG (layers only via data-layer, or none): standard CNC layers first, extras after
            extras = [l for l in doc.layers if l.name not in DEFAULT_LAYER_DESCRIPTIONS]
            doc.layers = default_layers() + extras
        doc._fix_next_id()
        for e in doc.elements:
            if not e.id:
                e.id = doc.new_id()
            if not doc.has_layer(e.layer):
                ensure_layer(e.layer)
        return doc


# ── helpers ────────────────────────────────────────────────

def element_svg(e: Element, layer: Layer, with_id: bool = True) -> str:
    attrs = dict(e.attrs)
    if e.tag == "text":
        attrs.setdefault("fill", layer.color)
        if attrs.get("fill") not in ("none",):
            attrs["fill"] = layer.color
    else:
        attrs["stroke"] = layer.color
        attrs.setdefault("fill", "none")
        attrs.setdefault("stroke-width", "1")
        dash = layer.dasharray()
        if dash:
            attrs["stroke-dasharray"] = dash
            if layer.line_style == "dotted":
                attrs["stroke-linecap"] = "round"
    a = (f'id="{e.id}" ' if with_id else "") + " ".join(f"{k}={quoteattr(str(v))}" for k, v in attrs.items())
    if e.tag == "text":
        return f"<text {a}>{escape(e.text)}</text>"
    return f"<{e.tag} {a}/>"


ATTR_NAME = re.compile(r"[A-Za-z_][\w.\-]*")


def valid_attr_name(k: str) -> bool:
    """Plain SVG attribute names only: no namespaces, no event handlers (on*)."""
    return bool(ATTR_NAME.fullmatch(k)) and not k.lower().startswith("on")


def check_attr_names(attrs: dict | None):
    if attrs is not None and not isinstance(attrs, dict):
        raise DocError("attrs must be an object")
    bad = [k for k in (attrs or {}) if not valid_attr_name(str(k))]
    if bad:
        raise DocError(f"Invalid attribute name(s): {', '.join(map(repr, bad))}")


def clean_attrs(attrs: dict) -> dict:
    """Drop layer-owned, namespaced and unsafe attributes (used for imports too)."""
    out = {}
    for k, v in (attrs or {}).items():
        if k in LAYER_OWNED or v is None or not valid_attr_name(k):
            continue
        out[k] = str(v)
    return out


STYLE_KEEP = {"fill", "fill-rule", "fill-opacity", "opacity", "stroke-width", "font-size", "font-family",
              "font-weight", "font-style", "text-anchor", "dominant-baseline", "letter-spacing"}


def is_hidden(node) -> bool:
    style = (node.get("style") or "").replace(" ", "")
    return node.get("display") == "none" or "display:none" in style or node.get("visibility") == "hidden"


def style_attrs(style: str) -> dict:
    out = {}
    for part in (style or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            k, v = k.strip(), v.strip()
            if k in STYLE_KEEP:
                out[k] = v
    return out


def local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def sanitize_layer_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9 _\-.]", "_", (name or "").strip())[:40]
    return name or "Layer"


UNIT_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72, "pc": 25.4 / 6, "px": 25.4 / 96,
           "": 1.0}  # unitless = this app's convention (1 unit = 1 mm)


def root_geometry(root) -> tuple[float, float, str]:
    """Document size in mm and the transform mapping the file's user units to mm."""
    def length(v):
        m = re.fullmatch(r"\s*([\d.]+(?:e-?\d+)?)\s*([a-z%]*)\s*", v or "")
        if not m or m.group(2) not in UNIT_MM:
            return None
        return float(m.group(1)) * UNIT_MM[m.group(2)]

    w, h = length(root.get("width")), length(root.get("height"))
    vb = (root.get("viewBox") or "").replace(",", " ").split()
    if len(vb) != 4:
        # No viewBox: user units are the unit of width/height (px→mm etc.)
        m = re.match(r"\s*[\d.]+\s*([a-z]*)", root.get("width") or "")
        k = UNIT_MM.get(m.group(1), 1.0) if m else 1.0
        return (num(w) if w else 800, num(h) if h else 600,
                f"scale({fmt(k)})" if abs(k - 1) > 1e-9 else "")
    minx, miny, vw, vh = (float(x) for x in vb)
    sx = w / vw if w and vw else 1.0
    sy = h / vh if h and vh else sx
    t = []
    if abs(sx - 1) > 1e-9 or abs(sy - 1) > 1e-9:
        t.append(f"scale({fmt(sx)})" if abs(sx - sy) < 1e-9 else f"scale({fmt(sx)}, {fmt(sy)})")
    if minx or miny:
        t.append(f"translate({fmt(-minx)}, {fmt(-miny)})")
    return num(vw * sx), num(vh * sy), " ".join(t)


def text_lines(node) -> list[tuple[dict, str]] | None:
    """Positioned <tspan> children (Inkscape multi-line text) as (attrs, text) lines."""
    spans = [c for c in node if local(c.tag) == "tspan" and (c.get("x") or c.get("y"))]
    if len(spans) < 2:
        return None
    out = []
    for sp in spans:
        a = {k: v for k, v in sp.attrib.items() if k in ("x", "y") }
        a.update(style_attrs(sp.get("style", "")))
        out.append((a, "".join(sp.itertext())))
    return out


def num(v) -> float:
    f = round(float(v), 4)
    return int(f) if f == int(f) else f


def fmt(v) -> str:
    f = round(float(v), 6)
    return str(int(f)) if f == int(f) else repr(f)
