import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

SVG_NS = "http://www.w3.org/2000/svg"
SHAPE_TAGS = {"line", "rect", "circle", "ellipse", "text", "path", "polygon", "polyline"}


@dataclass
class LayerInfo:
    name: str
    color: str
    stroke_dash: str = ""
    visible: bool = True


DEFAULT_LAYERS = [
    LayerInfo("CUT_OUTSIDE", "#e74c3c", "", True),
    LayerInfo("CUT_INSIDE", "#e74c3c", "6 3", True),
    LayerInfo("ENGRAVE", "#3498db", "", True),
    LayerInfo("NOTES", "#2ecc71", "", True),
]


@dataclass
class SvgElement:
    id: str
    tag: str
    attrs: dict[str, str]
    text_content: str = ""


@dataclass
class SvgCanvas:
    width: int = 800
    height: int = 600
    elements: dict[str, SvgElement] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    next_id: int = 1
    version: int = 0
    screenshot_requested: bool = False
    screenshot_data: str | None = None
    layers: list[LayerInfo] = field(default_factory=lambda: [LayerInfo(l.name, l.color, l.stroke_dash, l.visible) for l in DEFAULT_LAYERS])
    lock: threading.Lock = field(default_factory=threading.Lock)

    def _new_id(self) -> str:
        eid = f"el-{self.next_id}"
        self.next_id += 1
        return eid

    def add_element(self, tag: str, attrs: dict[str, str], text_content: str = "") -> SvgElement:
        eid = self._new_id()
        el = SvgElement(id=eid, tag=tag, attrs=dict(attrs), text_content=text_content)
        self.elements[eid] = el
        self.order.append(eid)
        self.version += 1
        return el

    def update_element(self, element_id: str, attrs: dict[str, str]) -> SvgElement | None:
        el = self.elements.get(element_id)
        if not el:
            return None
        el.attrs.update(attrs)
        self.version += 1
        return el

    def remove_element(self, element_id: str) -> bool:
        if element_id not in self.elements:
            return False
        del self.elements[element_id]
        self.order = [eid for eid in self.order if eid != element_id]
        self.version += 1
        return True

    def list_elements(self) -> list[SvgElement]:
        return [self.elements[eid] for eid in self.order if eid in self.elements]

    def get_element(self, element_id: str) -> SvgElement | None:
        return self.elements.get(element_id)

    def to_svg_markup(self) -> str:
        parts = [f'<svg xmlns="{SVG_NS}" width="{self.width}" height="{self.height}">']
        for el in self.list_elements():
            attr_str = " ".join(f'{k}="{v}"' for k, v in el.attrs.items())
            if el.tag == "text":
                parts.append(f"  <text id=\"{el.id}\" {attr_str}>{el.text_content}</text>")
            else:
                parts.append(f"  <{el.tag} id=\"{el.id}\" {attr_str}/>")
        parts.append("</svg>")
        return "\n".join(parts)

    def from_svg_markup(self, markup: str) -> None:
        try:
            root = ET.fromstring(markup)
        except ET.ParseError:
            return

        # Update canvas size from root attributes
        w = root.get("width")
        h = root.get("height")
        if w:
            try:
                self.width = int(float(w))
            except ValueError:
                pass
        if h:
            try:
                self.height = int(float(h))
            except ValueError:
                pass

        self.elements.clear()
        self.order.clear()

        max_id = 0
        for child in root:
            # Strip namespace prefix if present
            tag = child.tag
            if "}" in tag:
                tag = tag.split("}", 1)[1]

            if tag not in SHAPE_TAGS:
                continue

            el_id = child.get("id", "")
            if el_id == "_sel":
                continue

            # Parse existing el-N IDs to keep counter in sync
            if el_id.startswith("el-"):
                try:
                    num = int(el_id[3:])
                    max_id = max(max_id, num)
                except ValueError:
                    pass

            attrs = {}
            for k, v in child.attrib.items():
                if k == "id":
                    continue
                # Strip namespace prefixes from attribute names
                if "}" in k:
                    k = k.split("}", 1)[1]
                attrs[k] = v

            text_content = child.text or "" if tag == "text" else ""

            if not el_id or el_id == "_sel":
                el_id = f"el-{max_id + 1}"
                max_id += 1

            el = SvgElement(id=el_id, tag=tag, attrs=attrs, text_content=text_content)
            self.elements[el_id] = el
            self.order.append(el_id)

        self.next_id = max_id + 1
        self.version += 1
