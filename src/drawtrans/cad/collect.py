"""Collect every visible text of modelspace in WCS (block contents exploded virtually).

Item fields: text, x, y (insert point WCS), h (height WCS), rot (deg), halign/valign,
             lang, path (handle chain), box (approx WCS bounding polygon as Rect if rot ~ 0/90)
"""
from __future__ import annotations
import math, re
from dataclasses import dataclass, field
from ezdxf.math import Vec3

CYR = re.compile(r"[А-Яа-яЁё]"); LAT = re.compile(r"[A-Za-z]{2,}"); HAN = re.compile(r"[一-鿿]")


def lang_of(t: str) -> str:
    if HAN.search(t): return "zh"
    if CYR.search(t): return "ru"
    if LAT.search(t): return "en"
    return "neu"


def fix_mojibake(t: str) -> str:
    """Russian cp1251 text that was decoded as GBK ('填眚噫 颀礓忤...'): undo it."""
    if not HAN.search(t):
        return t
    try:
        b = t.encode("gbk")
        r = b.decode("cp1251")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return t
    # accept only clean Russian: Cyrillic letters, spaces, digits, basic punctuation, '?' for
    # bytes that were lost - and at least two real lowercase words
    if not re.fullmatch(r"[А-Яа-яЁё0-9\s\.,;:\-\(\)«»\"'?/]+", r):
        return t
    if len(re.findall(r"[а-яё]{4,}", r)) < 2:
        return t
    return r


@dataclass(eq=False)
class TItem:
    text: str
    x: float
    y: float
    h: float
    rot: float
    width: float           # along the text direction (estimated)
    lang: str
    path: str
    layer: str
    kind: str
    raw: str = ""

    @property
    def ux(self):  # unit vector along text
        a = math.radians(self.rot); return math.cos(a), math.sin(a)

    def corners(self):
        ux, uy = self.ux; vx, vy = -uy, ux
        x0, y0 = self.x, self.y
        w, h = self.width, self.h
        return [(x0, y0), (x0 + ux * w, y0 + uy * w), (x0 + ux * w + vx * h, y0 + uy * w + vy * h), (x0 + vx * h, y0 + vy * h)]

    def bbox(self):
        cs = self.corners(); xs = [c[0] for c in cs]; ys = [c[1] for c in cs]
        return min(xs), min(ys), max(xs), max(ys)


def est_width(text: str, h: float, wf: float = 1.0) -> float:
    w = 0.0
    for ch in text:
        if HAN.match(ch): w += 1.0
        elif ch == " ": w += 0.5
        elif ch.isupper() or ch.isdigit(): w += 0.72
        else: w += 0.6
    return w * h * wf


def _text_geom(e):
    """(x, y, h, rot, text, width) of a TEXT/ATTRIB/MTEXT in its own coordinate system"""
    t = e.dxftype()
    if t == "MTEXT":
        s = e.plain_text()
        h = e.dxf.get("char_height", 1.0) or 1.0
        rot = e.get_rotation() if hasattr(e, "get_rotation") else e.dxf.get("rotation", 0)
        p = e.dxf.insert
        lines = s.split("\n")
        wmax = max(est_width(l, h) for l in lines) if lines else 0
        # MTEXT insert is top-left (attachment 1) mostly; convert to bottom-left of first line
        ap = e.dxf.get("attachment_point", 1)
        a = math.radians(rot); ux, uy = math.cos(a), math.sin(a); vx, vy = -uy, ux
        nl = len(lines)
        x, y = p.x, p.y
        if ap in (1, 2, 3):
            y_shift = -h * (1.0 + 1.4 * (nl - 1))
        elif ap in (4, 5, 6):
            y_shift = -h * (0.5 + 0.7 * (nl - 1))
        else:
            y_shift = 0
        x_shift = {1: 0, 4: 0, 7: 0, 2: -wmax / 2, 5: -wmax / 2, 8: -wmax / 2}.get(ap, -wmax)
        x += ux * x_shift + vx * y_shift; y += uy * x_shift + vy * y_shift
        return x, y, h, rot, s, wmax
    s = e.dxf.text
    h = e.dxf.get("height", 1.0) or 1.0
    rot = e.dxf.get("rotation", 0) or 0
    wf = e.dxf.get("width", 1.0) or 1.0
    w = est_width(s, h, wf)
    p = e.dxf.insert
    ha, va = e.dxf.get("halign", 0), e.dxf.get("valign", 0)
    if (ha or va) and e.dxf.hasattr("align_point"):
        ap = e.dxf.align_point
        a = math.radians(rot); ux, uy = math.cos(a), math.sin(a); vx, vy = -uy, ux
        dx = {0: 0, 1: -w / 2, 2: -w, 3: -w, 4: -w / 2, 5: -w}.get(ha, 0)
        dy = {0: 0, 1: 0, 2: -h / 2, 3: -h}.get(va, 0)
        p = Vec3(ap.x + ux * dx + vx * dy, ap.y + uy * dx + vy * dy, 0)
    return p.x, p.y, h, rot, s, w


def collect(doc, used_only=True):
    msp = doc.modelspace()
    items: list[TItem] = []

    def add(e, path):
        if e.dxftype() not in ("TEXT", "MTEXT", "ATTRIB"):
            return
        try:
            x, y, h, rot, s, w = _text_geom(e)
        except Exception:
            return
        if not s or not s.strip():
            return
        raw = s
        s = fix_mojibake(s)
        s = s.replace("%%c", "Ø").replace("%%C", "Ø").replace("%%d", "°").replace("%%p", "±")
        items.append(TItem(s.strip(), x, y, h, rot % 360, w, lang_of(s), path, e.dxf.layer, e.dxftype(), raw))

    def walk(entities, path, depth):
        for e in entities:
            t = e.dxftype()
            if t == "INSERT":
                for a in e.attribs:
                    add(a, path + "/" + (e.dxf.handle or "v"))
                if depth < 8:
                    try:
                        virt = list(e.virtual_entities())
                    except Exception:
                        virt = []
                    walk(virt, path + "/" + (e.dxf.handle or "v"), depth + 1)
            elif t in ("TEXT", "MTEXT"):
                add(e, path + "/" + (e.dxf.handle or "v"))
    walk(msp, "M", 0)
    return items
