"""Translate a DWG drawing (via DXF): add Chinese on its own layer, originals untouched.

usage: cad/10_build.py [--config project.toml] [--dxf in.dxf] [--out-stem NAME] [--dry]
outputs:
  <output>/<stem>_CN.dxf          full drawing + layer CN_TRANSLATION
  <output>/<stem>_CN_overlay.dxf  only the Chinese texts (insert at 0,0 into the original DWG)
  <review>/cad_report.csv, <review>/tm_todo.json

Convert DWG -> DXF first (ODA File Converter or LibreDWG's dwg2dxf, see README).
"""
import argparse, csv, json, math, re, sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ezdxf
from ezdxf import recover
from collect import collect, est_width, lang_of, TItem
from engine import config
from engine.tm import TM

LAYER = "CN_TRANSLATION"
STYLE = "CN_FONT"
CODE = re.compile(r"^[A-ZА-ЯЁ]{1,4}[\d.\-+/]*\d*[A-ZА-Я]?$|^[A-Z]{1,4}\d[\w\-]*$|^\d")

# ---------------------------------------------------------------- geometry helpers
def local(item_or_xy, origin, rot):
    """coordinates in a frame with x along rot, origin at origin"""
    x, y = item_or_xy
    a = math.radians(rot); c, s = math.cos(a), math.sin(a)
    dx, dy = x - origin[0], y - origin[1]
    return dx * c + dy * s, -dx * s + dy * c


def world(u, v, origin, rot):
    a = math.radians(rot); c, s = math.cos(a), math.sin(a)
    return origin[0] + u * c - v * s, origin[1] + u * s + v * c


def same_dir(a, b, tol=1.0):
    d = abs((a - b + 180) % 360 - 180)
    return d < tol


# ---------------------------------------------------------------- grouping
class Phrase:
    def __init__(self, lines):
        self.lines = lines            # list[list[TItem]] in reading order
        self.items = [i for l in lines for i in l]
        self.rot = lines[0][0].rot
        self.h = max(i.h for i in self.items)
        self.text = "\n".join(" ".join(i.text for i in l) for l in lines)
        self.lang = "ru" if any(i.lang == "ru" for i in self.items) else (
            "en" if any(i.lang == "en" for i in self.items) else self.items[0].lang)

    def local_box(self, origin, rot):
        us, vs = [], []
        for i in self.items:
            for c in i.corners():
                u, v = local(c, origin, rot); us.append(u); vs.append(v)
        return min(us), min(vs), max(us), max(vs)


def group(items):
    """words on one baseline -> lines; lines stacked closely -> phrases"""
    items = [i for i in items if i.lang != "neu" or True]
    # dedupe exact duplicates (same text at same place)
    seen, uniq = set(), []
    for i in items:
        k = (i.text, round(i.x, 2), round(i.y, 2), round(i.rot))
        if k not in seen:
            seen.add(k); uniq.append(i)
    by_dir = defaultdict(list)
    for i in uniq:
        by_dir[round(i.rot) % 360].append(i)
    phrases = []
    for rot_key, its in by_dir.items():
        rot = its[0].rot
        o = (its[0].x, its[0].y)
        # lines: cluster by baseline (and height), then split each baseline by horizontal gaps
        uv = {id(i): local((i.x, i.y), o, rot) for i in its}
        by_h = defaultdict(list)
        for i in its:
            by_h[round(i.h, 2)].append(i)
        lines = []
        for h_key, hits in by_h.items():
            hits.sort(key=lambda i: -uv[id(i)][1])
            clusters, cur = [], [hits[0]]
            for i in hits[1:]:
                if abs(uv[id(i)][1] - uv[id(cur[0])][1]) < 0.35 * i.h:
                    cur.append(i)
                else:
                    clusters.append(cur); cur = [i]
            clusters.append(cur)
            for cl in clusters:
                cl.sort(key=lambda i: uv[id(i)][0])
                ln = [cl[0]]
                for i in cl[1:]:
                    prev = ln[-1]
                    gap = uv[id(i)][0] - (uv[id(prev)][0] + prev.width)
                    if gap < 1.8 * i.h:
                        ln.append(i)
                    else:
                        lines.append(ln); ln = [i]
                lines.append(ln)
        # phrases: stack lines (pitch <= 1.45 h, horizontally overlapping or aligned)
        def lbox(l):
            u0 = min(local((i.x, i.y), o, rot)[0] for i in l)
            u1 = max(local((i.x, i.y), o, rot)[0] + i.width for i in l)
            v = local((l[0].x, l[0].y), o, rot)[1]
            return u0, u1, v, l[0].h
        lines.sort(key=lambda l: (-lbox(l)[2], lbox(l)[0]))
        used = set()
        for k, l in enumerate(lines):
            if id(l) in used:
                continue
            grp = [l]; used.add(id(l))
            cur = l
            while True:
                cu0, cu1, cv, ch = lbox(cur)
                nxt = None
                for m in lines:
                    if id(m) in used:
                        continue
                    if lang_of(" ".join(i.text for i in m)) == "neu" or lang_of(" ".join(i.text for i in cur)) == "neu":
                        continue          # numbers / codes are separate cells, not part of a label
                    mu0, mu1, mv, mh = lbox(m)
                    pitch = cv - mv
                    lim = 2.6 * ch if m[0].text.startswith("(") else 2.0 * ch    # "(existing)" etc.
                    if abs(mh - ch) < 0.1 * ch and 0.5 * ch < pitch <= lim:
                        ov = min(cu1, mu1) - max(cu0, mu0)
                        cx = abs((cu0 + cu1) / 2 - (mu0 + mu1) / 2)
                        if ov > 0 or abs(cu0 - mu0) < 1.0 * ch or cx < 1.0 * ch:
                            if lang_of(" ".join(i.text for i in m)) == "neu" and lang_of(" ".join(i.text for i in cur)) != "neu":
                                pass
                            nxt = m; break
                if nxt is None:
                    break
                grp.append(nxt); used.add(id(nxt)); cur = nxt
            phrases.append(Phrase(grp))
    return phrases


def needs_translation(p: Phrase) -> bool:
    if p.lang not in ("ru", "en"):
        return False
    words = re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]*", p.text)
    if not words:
        return False
    # designations: P101, K2, AB, 0Б+50, В3, Д, ИГЭ...
    if all(CODE.match(w) or (w.isupper() and len(w) <= 4) for w in p.text.replace("\n", " ").split()):
        return False
    # only numbers + units, e.g. "(1000 mm)", "t=50 mm"
    if all(w.lower() in ("mm", "m", "cm", "kg", "t", "мм", "м", "кг") for w in words):
        return False
    return True


# ---------------------------------------------------------------- obstacles (texts + line art), spatial grid
class Grid:
    def __init__(self, cell):
        self.c = cell; self.g = defaultdict(list)
    def add(self, box, obj):
        x0, y0, x1, y1 = box
        for gx in range(int(x0 // self.c), int(x1 // self.c) + 1):
            for gy in range(int(y0 // self.c), int(y1 // self.c) + 1):
                self.g[(gx, gy)].append((box, obj))
    def query(self, box):
        x0, y0, x1, y1 = box; out = {}
        for gx in range(int(x0 // self.c), int(x1 // self.c) + 1):
            for gy in range(int(y0 // self.c), int(y1 // self.c) + 1):
                for b, o in self.g.get((gx, gy), ()):
                    out[id(o)] = (b, o)
        return out.values()


def segments(doc):
    """line art of modelspace in WCS (lines, polylines; blocks exploded)"""
    out = []
    def walk(ents, depth):
        for e in ents:
            t = e.dxftype()
            try:
                if t == "LINE":
                    out.append(((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y), e.dxf.layer))
                elif t == "LWPOLYLINE":
                    pts = [(p[0], p[1]) for p in e.get_points()]
                    if e.closed and pts:
                        pts.append(pts[0])
                    out.extend((a, b, e.dxf.layer) for a, b in zip(pts, pts[1:]))
                elif t == "INSERT" and depth < 8:
                    walk(list(e.virtual_entities()), depth + 1)
            except Exception:
                pass
    walk(doc.modelspace(), 0)
    return out


def poly_box(pts):
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def seg_hits_rect(seg, origin, rot, rect):
    """does segment intersect local-frame rect (u0,v0,u1,v1)?"""
    (x1, y1), (x2, y2) = seg
    a = local((x1, y1), origin, rot); b = local((x2, y2), origin, rot)
    u0, v0, u1, v1 = rect
    # Liang-Barsky clip
    dx, dy = b[0] - a[0], b[1] - a[1]
    p = [-dx, dx, -dy, dy]; q = [a[0] - u0, u1 - a[0], a[1] - v0, v1 - a[1]]
    t0, t1 = 0.0, 1.0
    for pi, qi in zip(p, q):
        if abs(pi) < 1e-12:
            if qi < 0: return False
        else:
            t = qi / pi
            if pi < 0: t0 = max(t0, t)
            else: t1 = min(t1, t)
            if t0 > t1: return False
    return True


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--dxf", default=None, help="input DXF (overrides [project] dxf)")
    ap.add_argument("--tm", default=None)
    ap.add_argument("--out-stem", default=None, help="output name stem (default: input stem)")
    ap.add_argument("--font", default="simhei.ttf", help="TTF the DXF text style refers to")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = config.load(a.config)
    src = Path(a.dxf).resolve() if a.dxf else cfg.dxf
    if src is None:
        ap.error("no input DXF: pass --dxf or set [project] dxf in project.toml")
    stem = a.out_stem or src.stem
    cfg.review.mkdir(parents=True, exist_ok=True)
    cfg.output.mkdir(parents=True, exist_ok=True)
    doc, aud = recover.readfile(src)
    items = collect(doc)
    phrases = group(items)
    tm = TM(Path(a.tm).resolve() if a.tm else cfg.tm, cfg.glossaries)
    zh_items = [i for i in items if i.lang == "zh"]

    todo, report = [], []
    plan = []
    cand = [p for p in phrases if needs_translation(p)]
    # already bilingual: a Chinese label right next to the English one. One Chinese label
    # can vouch for ONE English phrase only (its nearest), so a nearby room name does not
    # make an unrelated note look translated.
    claim = {}
    for z in zh_items:
        if len(re.findall(r"[一-鿿]", z.text)) < 3:
            continue                  # 待定 / 上 / 由 ... are not translations of anything
        best, bd = None, None
        for p in cand:
            if len(p.text.split()) > 8:
                continue              # long notes are never covered by a short Chinese label
            n_en = len(re.findall(r"[A-Za-zА-Яа-я]+", p.text))
            if len(re.findall(r"[一-鿿]", z.text)) > 3 * n_en + 2:
                continue              # a long Chinese note is not the translation of a short label
            if not same_dir(z.rot, p.rot, 2) and not same_dir(z.rot, p.rot + 180, 2):
                continue
            o = (p.items[0].x, p.items[0].y)
            u0, v0, u1, v1 = p.local_box(o, p.rot)
            zu, zv = local((z.x, z.y), o, p.rot)
            if -2.5 * p.h < zv - v0 < (v1 - v0) + 2.5 * p.h and (u0 - 2 * p.h) < zu < (u1 + 2 * p.h):
                d = abs(zv - (v0 + v1) / 2) + 0.3 * abs(zu - (u0 + u1) / 2)
                if bd is None or d < bd:
                    best, bd = p, d
        if best is not None and (id(best) not in claim or bd < claim[id(best)][1]):
            claim[id(best)] = (z, bd)
    # a phrase that itself contains Chinese lines is bilingual by construction
    for p in cand:
        if any(i.lang == "zh" for i in p.items):
            claim.setdefault(id(p), (None, 0))
    for p in cand:
        if id(p) in claim:
            z = claim[id(p)][0]
            report.append(("skip_has_zh", p.text.replace("\n", " / ") + (f"  ← {z.text}" if z else ""))); continue
        lines = [" ".join(i.text for i in l) for l in p.lines]
        zh, keys = tm.translate_lines(lines, "body")
        if zh is None:
            # try as one sentence (multi-line labels without numbering)
            zh, k2 = tm.translate(" ".join(lines), "body")
        if zh is None:
            todo.append(p.text); report.append(("missing", p.text)); continue
        if zh == "":
            report.append(("skipped", p.text)); continue
        plan.append((p, zh.replace("\n", "")))
    # todo dump (unique, keep first occurrence order)
    seen = {}
    for k, v in tm.missing.items():
        seen.setdefault(k, v)
    (cfg.review / "tm_todo.json").write_text(json.dumps({k: {"ru": v, "pages": [0]} for k, v in seen.items()},
                                                             ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"phrases={len(phrases)} to_place={len(plan)} missing_keys={len(seen)} "
          f"{Counter(r[0] for r in report)}")
    if a.dry:
        return report, []

    # ---------- obstacles
    segs = segments(doc)
    hs = [p.h for p, _ in plan] or [1]
    grid = Grid(max(2.0, sorted(hs)[len(hs) // 2] * 4))
    for i in items:
        grid.add(i.bbox(), ("t", i))
    for s in segs:
        grid.add(poly_box(s[:2]), ("s", s))

    font_w = 1.0                       # Chinese glyph width / height for simhei at width factor 0.8 -> 0.8
    WF = 0.8
    placed, report2 = [], []
    for p, zh in plan:
        rot = p.rot
        flip = 90 < (rot % 360) <= 270            # write Chinese upright
        wrot = (rot - 180) % 360 if flip else rot
        o = (p.items[0].x, p.items[0].y)
        u0, v0, u1, v1 = p.local_box(o, wrot)
        own = {id(i) for i in p.items}
        own_layers = {i.layer for i in p.items}
        best = None
        # position first, size second: a smaller Chinese line right under the original reads
        # better (and keeps tables tidy) than a big one somewhere to the side
        order = [("below", k) for k in (0.85, 0.7, 0.55, 0.45)] + [("above", k) for k in (0.85, 0.7, 0.55, 0.45)] \
              + [("right", k) for k in (0.85, 0.7, 0.55)] + [("left", k) for k in (0.85, 0.7, 0.55)]
        for where, k in order:
            ch = p.h * k
            w = est_width(zh, ch, WF)
            gap = 0.1 * p.h
            cu, cv = {"below": (u0, v0 - gap - ch), "above": (u0, v1 + gap),
                      "right": (u1 + 0.5 * p.h, v0), "left": (u0 - 0.5 * p.h - w, v0)}[where]
            if True:
                rect = (cu - 0.05 * ch, cv - 0.15 * ch, cu + w + 0.05 * ch, cv + 1.05 * ch)
                # world bbox of the candidate for the grid query
                corners = [world(rect[0], rect[1], o, wrot), world(rect[2], rect[1], o, wrot),
                           world(rect[2], rect[3], o, wrot), world(rect[0], rect[3], o, wrot)]
                wb = poly_box(corners)
                hit = False
                for b, (kind, obj) in grid.query(wb):
                    if kind == "t":
                        # background annotation much smaller than this label (spot heights,
                        # survey numbers) is not an obstacle - the original text sits on it too
                        if id(obj) in own or obj.h < 0.4 * p.h:
                            continue
                        bu0, bv0, bu1, bv1 = poly_box([local(c, o, wrot) for c in obj.corners()])
                        if bu0 < rect[2] and bu1 > rect[0] and bv0 < rect[3] and bv1 > rect[1]:
                            hit = True; break
                    else:
                        # only line art of the label's own layer family (table rules, room
                        # boundaries drawn with it); terrain contours etc. are background
                        if obj[2] not in own_layers and "TAB" not in obj[2].upper():
                            continue
                        if seg_hits_rect(obj[:2], o, wrot, rect):
                            hit = True; break
                if not hit:
                    best = (cu, cv, ch, where, w)
            if best:
                break
        status = "ok"
        if best is None:
            ch = p.h * 0.6; w = est_width(zh, ch, WF)
            best = (u0, v0 - 0.25 * p.h - ch, ch, "below-forced", w); status = "crowded"
        cu, cv, ch, where, w = best
        wx, wy = world(cu, cv, o, wrot)
        placed.append((zh, wx, wy, ch, wrot))
        # the new text is an obstacle for the next ones
        ti = TItem(zh, wx, wy, ch, wrot, w, "zh", "CN", LAYER, "TEXT")
        grid.add(ti.bbox(), ("t", ti))
        report2.append((status, where, round(ch, 3), p.text.replace("\n", " / "), zh))

    # ---------- write
    def setup(d):
        if LAYER not in d.layers:
            d.layers.add(LAYER, color=5)
        if STYLE not in d.styles:
            d.styles.add(STYLE, font=a.font).dxf.width = WF
    setup(doc)
    msp = doc.modelspace()
    for zh, x, y, ch, r in placed:
        msp.add_text(zh, dxfattribs={"layer": LAYER, "style": STYLE, "height": ch, "rotation": r,
                                    "insert": (x, y), "width": WF, "color": 256})
    out_full = cfg.output / f"{stem}_CN.dxf"
    doc.saveas(out_full)
    ov = ezdxf.new(doc.dxfversion)
    ov.header["$INSUNITS"] = doc.header.get("$INSUNITS", 4)
    setup(ov)
    for zh, x, y, ch, r in placed:
        ov.modelspace().add_text(zh, dxfattribs={"layer": LAYER, "style": STYLE, "height": ch, "rotation": r,
                                                "insert": (x, y), "width": WF})
    ov.saveas(cfg.output / f"{stem}_CN_overlay.dxf")
    with open(cfg.review / "cad_report.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["status", "where", "height", "source", "chinese"])
        w.writerows(report2)
        w.writerows([(s, "", "", t, "") for s, t in report])
    print("placed", len(placed), Counter(r[0] for r in report2), Counter(r[1] for r in report2))
    return report, report2


if __name__ == "__main__":
    main()
