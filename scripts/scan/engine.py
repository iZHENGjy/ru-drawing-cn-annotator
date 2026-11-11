"""Placement engine for scanned drawings (no text layer).

Chinese goes directly under (or beside) the Russian it translates, inside the same table
cell when possible; numbered notes are set as one block in free space or in a widened
column on the right; short title-block / margin fields are collected into one bilingual
legend line next to the title block.  Every decision is recorded in ``COVER`` so the
coverage audit (30_audit.py) can prove that each Russian text region was accounted for.

Coordinates: PDF points, top-left origin, visual page orientation.
"""
import io
import re
from collections import defaultdict

import fitz
import numpy as np
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from . import ctx as C

FONT = "ZH"
BLUE = (0x00 / 255, 0x2E / 255, 0xBF / 255)
CYR = C.CYR
CTX = None   # C.Ctx, set by init()


def init(ctx):
    """Bind the engine to one project (paths, Chinese font, project-specific strings)."""
    global CTX
    CTX = ctx
    ff = C.font_file(ctx.cfg)
    pdfmetrics.registerFont(TTFont(FONT, ff, subfontIndex=0) if ff.lower().endswith(".ttc") else TTFont(FONT, ff))

def clean(s):
    return (s or "").replace("−", "-").replace("∅", "Ø").replace("⌀", "Ø").strip()

def sw(t, fs):
    return pdfmetrics.stringWidth(t, FONT, fs)

def wrap(text, fs, maxw):
    out = []
    paras = []
    for para in text.split("\n"):
        if sw(para, fs) > maxw and " / " in para and len(para) < 60:   # 优先在「 / 」处断行,不拆代号
            ps = para.split(" / ")
            paras += [p + (" /" if i < len(ps) - 1 else "") for i, p in enumerate(ps)]
        else:
            paras.append(para)
    for para in paras:
        # 按「词」装行:连续的字母/数字/符号串(代号、数值如 F200、Км2.1、Ø16)不拆,汉字逐字
        toks = re.findall(r"[A-Za-zА-Яа-яЁёØ0-9][A-Za-zА-Яа-яЁёØ0-9.,×x+\-=/()]*|\s+|.", para)
        cur = ""
        for tk in toks:
            if sw(cur + tk, fs) > maxw and cur.strip():
                if sw(tk, fs) > maxw:          # 单个词超宽:只能逐字拆
                    for ch in tk:
                        if sw(cur + ch, fs) > maxw and cur:
                            out.append(cur); cur = ch
                        else:
                            cur += ch
                    continue
                out.append(cur.rstrip()); cur = tk.lstrip()
            else:
                cur += tk
        out.append(cur)
    return out

APPX = re.compile(r"[,，]?\s*[（(]?\s*(续\s*[,，]\s*)?中文译文见[^)）]*[)）]?")

COVER = {}
# Opt-in output switches (set by 20_build.py from --compact / [output]); defaults = historical output.
#   compact: do not draw the per-word 根/间距/件 count labels (nor their legend entry)
OPTS = {"compact": False}
FRAME_ANYWHERE = {"状态": "Статус", "版次": "Рев.", "状态/版次": "Статус/Рев.", "发布目的": "Назначение выпуска"}
LOW_LEGEND = "虚线下划线=机器译文,置信度低,待核对"
FS_K = 1.0   # 中文字号 = 俄文 OCR 行高 × FS_K(≈原文字号 0.92 倍;原 0.72 只有 0.66 倍)

def load_rapid(doc, pg, PW, PH):
    """Second-detector boxes [(bb_pt, text)]; ``doc`` is only a label."""
    return C.load_rapid(CTX, pg, PW, PH)

def notes_from_trans(j):
    blocks = [b for b in (j or {}).get("blocks", []) if b.get("kind") == "notes"]
    return blocks

class Placer:
    def __init__(self, doc, pg):
        self.doc, self.pg = doc, pg
        src = fitz.open(CTX.cfg.pdf)
        p = src[pg - 1]
        self.PW, self.PH = p.rect.width, p.rect.height
        self.big = max(self.PW, self.PH) > 1000
        # 墨迹图(72dpi,视觉方向):去掉长直线后的「文字/图形」墨迹 与 「直线」墨迹分开
        R = 72
        pix = p.get_pixmap(dpi=R, colorspace=fitz.csGRAY)
        g8 = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        a = (g8 < 150).astype(np.int32)
        al = (g8 < 215).astype(np.int32)     # 细线在 72dpi 下呈浅灰,检测线用宽松阈值
        L = 18
        def runs(m, axis):
            cs = np.cumsum(m, axis=axis)
            pad = np.zeros_like(cs)
            if axis == 1:
                pad[:, L:] = cs[:, :-L]
            else:
                pad[L:, :] = cs[:-L, :]
            full = (cs - pad) >= L          # 以该像素结尾的长度 L 窗口全黑
            # 向前扩展到整段
            out = full.copy()
            for k in range(1, L):
                if axis == 1:
                    out[:, :-k] |= full[:, k:]
                else:
                    out[:-k, :] |= full[k:, :]
            return out
        hl = runs(al, 1); vl = runs(al, 0)
        lines = hl | vl
        # 容差:扫描倾斜 → 横线上下各扩 1px、竖线左右各扩 1px
        hld = hl.copy(); hld[1:, :] |= hl[:-1, :]; hld[:-1, :] |= hl[1:, :]
        vld = vl.copy(); vld[:, 1:] |= vl[:, :-1]; vld[:, :-1] |= vl[:, 1:]
        self.hl, self.vl = hld, vld
        text = (a.astype(bool) & ~lines).astype(np.float32)
        # 图框:最外侧的长竖线/长横线
        vs = np.nonzero(vl.sum(axis=0) > 0.55 * vl.shape[0])[0]
        hs_ = np.nonzero(hl.sum(axis=1) > 0.45 * hl.shape[1])[0]
        k = 72 / R
        PWr, PHr = p.rect.width, p.rect.height
        vx = vs * k; hy = hs_ * k
        fx0 = vx.min() + 2 if len(vx) and vx.min() < 0.2 * PWr else 6
        fx1 = vx.max() - 2 if len(vx) and vx.max() > 0.8 * PWr else PWr - 6
        fy0 = hy.min() + 2 if len(hy) and hy.min() < 0.2 * PHr else 6
        fy1 = hy.max() - 2 if len(hy) and hy.max() > 0.8 * PHr else PHr - 6
        self.frame = (fx0, fy0, fx1, fy1)
        self.R = R
        self.it = np.pad(text.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
        self.il = np.pad(lines.astype(np.float32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
        self.IH, self.IW = a.shape
        self.CELL = 40.0
        self.obs = defaultdict(list)
        self.plc = defaultdict(list)
        self.placed = []

    def cell(self, bb, lim):
        """俄文框所在表格格子的边界(无格线时用 lim 限制)"""
        R = self.R
        x0, y0, x1, y1 = [v * R / 72 for v in bb]
        H, Wd = self.IH, self.IW
        ya, yb = int(max(0, y0)), int(min(H - 1, y1 + 1))
        xa, xb = int(max(0, x0)), int(min(Wd - 1, x1 + 1))
        L = lim * R / 72
        def first(arr, start, stop, step):
            for k in range(int(start), int(stop), step):
                if 0 <= k < len(arr) and arr[k]:
                    return k
            return None
        colv = self.vl[ya:yb + 1, :].mean(axis=0) > 0.4 if yb > ya else self.vl[ya, :]
        right = first(colv, xb + 1, min(Wd, xb + L), 1)
        left = first(colv, xa - 1, max(-1, xa - L), -1)
        rowh = self.hl[:, xa:xb + 1].mean(axis=1) > 0.4 if xb > xa else self.hl[:, xa]
        bottom = first(rowh, yb + 1, min(H, yb + L), 1)
        top = first(rowh, ya - 1, max(-1, ya - L), -1)
        k = 72 / R
        return ((left + 1) * k if left is not None else max(0, bb[0] - lim),
                (top + 1) * k if top is not None else max(0, bb[1] - lim),
                (right - 0.5) * k if right is not None else min(self.PW, bb[2] + lim),
                (bottom - 0.5) * k if bottom is not None else min(self.PH, bb[3] + lim),
                (left is not None) + (right is not None) + (top is not None) + (bottom is not None))

    def colcell(self, bb, maxd=400):
        """按竖线/横线掩膜找 bb 所在格:左右最近整高竖线、下方最近横线。返回 (xl, xr, yb) 或 None"""
        H, Wd = self.vl.shape
        y0, y1 = max(0, int(bb[1])), min(H, int(bb[3]) + 1)
        if y1 <= y0:
            return None
        colv = self.vl[y0:y1, :].mean(axis=0) > 0.7
        xl = next((x for x in range(int(bb[0]) - 1, max(0, int(bb[0]) - maxd), -1) if colv[x]), None)
        xr = next((x for x in range(int(bb[2]) + 1, min(Wd, int(bb[2]) + maxd)) if colv[x]), None)
        if xl is None or xr is None:
            return None
        rowh = self.hl[:, xl + 2:xr - 1].mean(axis=1) > 0.5
        yb = next((y for y in range(int(bb[3]) + 1, min(H, int(bb[3]) + maxd)) if rowh[y]), None)
        if yb is None:
            return None
        return xl, xr, yb

    def row_ends(self, bb):
        """沿本行向左/右越过所有竖格线,返回表格外缘 (left_x, right_x)"""
        R = self.R
        ya, yb = int(max(0, bb[1] * R / 72)), int(min(self.IH - 1, bb[3] * R / 72 + 1))
        colv = self.vl[ya:yb + 1, :].mean(axis=0) > 0.4
        xs = np.nonzero(colv)[0]
        if len(xs) == 0:
            return None, None
        xc = (bb[0] + bb[2]) / 2 * R / 72
        right = [x for x in xs if x > xc]; left = [x for x in xs if x < xc]
        def chain(seq, rev):
            seq = sorted(seq, reverse=rev)
            if not seq:
                return None
            last = seq[0]
            for x in seq[1:]:
                if abs(x - last) > 160 * R / 72:
                    break
                last = x
            return last * 72 / R
        return chain(left, True), chain(right, False)

    # 坐标统一用「左上原点」top-origin (x0, y0, x1, y1)
    def _sum(self, I, bb):
        R = self.R
        x0 = int(max(0, bb[0] * R / 72)); x1 = int(min(self.IW, bb[2] * R / 72 + 0.999))
        y0 = int(max(0, bb[1] * R / 72)); y1 = int(min(self.IH, bb[3] * R / 72 + 0.999))
        if x1 <= x0 or y1 <= y0:
            return 1.0
        return float(I[y1, x1] - I[y0, x1] - I[y1, x0] + I[y0, x0]) / ((x1 - x0) * (y1 - y0))

    def add(self, bb, store):
        C = self.CELL
        for i in range(int(bb[0] // C), int(bb[2] // C) + 1):
            for j in range(int(bb[1] // C), int(bb[3] // C) + 1):
                store[(i, j)].append(bb)

    def hit(self, bb, store, pad):
        C = self.CELL
        for i in range(int((bb[0] - pad) // C), int((bb[2] + pad) // C) + 1):
            for j in range(int((bb[1] - pad) // C), int((bb[3] + pad) // C) + 1):
                for o in store.get((i, j), ()):
                    if not (bb[2] + pad <= o[0] or o[2] + pad <= bb[0] or bb[3] + pad <= o[1] or o[3] + pad <= bb[1]):
                        return o
        return None

    def ok(self, bb, ink_t=0.07, ign=None, lines_ok=False):
        if bb[0] < 6 or bb[2] > self.PW - 6 or bb[1] < 6 or bb[3] > self.PH - 6:
            return False
        if not getattr(self, "allow_out", False):
            fx0, fy0, fx1, fy1 = self.frame
            if bb[0] < fx0 or bb[2] > fx1 or bb[1] < fy0 or bb[3] > fy1:
                return False
        for o in self._all_hits(bb, self.obs, 0.2):
            if o is not ign:
                return False
        if self.hit(bb, self.plc, 0.8) is not None:
            return False
        h = bb[3] - bb[1]
        inner = (bb[0] + 0.5, bb[1] + h * 0.22, bb[2] - 0.5, bb[3] - h * 0.22)
        if not lines_ok and self._sum(self.il, inner) > 0.02:     # 中文不压线
            return False
        if not lines_ok:   # 细竖线(表格列线)贯穿标签:面积占比虽小也不允许
            xa, xb = max(0, int(bb[0] + 1)), min(self.vl.shape[1], int(bb[2] - 1))
            ya, yb = max(0, int(bb[1] + h * 0.15)), min(self.vl.shape[0], int(bb[3] - h * 0.15))
            if xb > xa and yb > ya and (self.vl[ya:yb, xa:xb].mean(axis=0) > 0.7).any():
                return False
        return self._sum(self.it, bb) <= ink_t

    def _all_hits(self, bb, store, pad):
        C = self.CELL; seen = []
        for i in range(int((bb[0] - pad) // C), int((bb[2] + pad) // C) + 1):
            for j in range(int((bb[1] - pad) // C), int((bb[3] + pad) // C) + 1):
                for o in store.get((i, j), ()):
                    if not (bb[2] + pad <= o[0] or o[2] + pad <= bb[0] or bb[3] + pad <= o[1] or o[3] + pad <= bb[1]):
                        if all(o is not s for s in seen):
                            seen.append(o)
        return seen

def load_meta(pg):
    """map/pNN.meta.json sidecar -> (set of low-confidence line ids, set of low-confidence extra indices).
    Absent / unreadable sidecar -> empty sets (output unchanged)."""
    import json
    try:
        fn = CTX.f("map", pg, ".meta.json")
        m = json.loads(fn.read_text(encoding="utf-8")) if fn.exists() else {}
    except Exception:
        return set(), set()
    lines = m.get("lines") or {}
    extra = m.get("extra") or []
    low_l = {int(k) for k, v in lines.items() if isinstance(v, dict) and v.get("conf") == "low" and str(k).lstrip("-").isdigit()}
    low_e = {i for i, v in enumerate(extra) if isinstance(v, dict) and v.get("conf") == "low"}
    return low_l, low_e


def _dots(c, x, y, w):
    """Dotted underline (low-confidence marker): same blue, hairline, prints in B/W, adds no text."""
    c.saveState()
    c.setLineWidth(0.35); c.setDash(0.5, 1.1)
    c.line(x, y, x + w, y)
    c.restoreState()


def annotate(doc, pg, mapping, trans, report):
    reader = PdfReader(str(CTX.cfg.pdf))
    # pypdf >= 7 only edits content streams of pages that belong to a writer
    page = PdfWriter().add_page(reader.pages[pg - 1])
    if page.rotation:
        page.transfer_rotation_to_content()
    PW, PH = float(page.mediabox.width), float(page.mediabox.height)
    pl = Placer(doc, pg)
    lines = C.load_lines(CTX, pg, PW, PH)
    byid = {d["id"]: d for d in lines}
    words = [(bb, t) for bb, t, _, _ in C.read_ocr_words(CTX.ocr(pg), 10)]
    wboxes = [bb for bb, _ in words if bb[2] - bb[0] < 96 and bb[3] - bb[1] < 14.4]   # obstacles
    for b in wboxes:
        pl.add(b, pl.obs)
    lineboxes = {}
    for d in lines:
        bb = (d["x0"], d["y0"], d["x1"], d["y1"])
        lineboxes[d["id"]] = bb
        pl.add(bb, pl.obs)
    big = pl.big
    # 典型字高(用于字号归一)
    hs = sorted(d["y1"] - d["y0"] for d in lines if len(CYR.findall(d["text"])) >= 3)
    hmed = hs[len(hs) // 2] if hs else (9 if big else 6)

    lm = {int(k): v for k, v in (mapping.get("lines") or {}).items()}
    low_lines, low_extra = load_meta(pg)
    compact = bool(OPTS.get("compact"))
    cov = dict(placed=[], legend=[], dup=[], notes=[], stamp=None, blank=[], lost=[], PW=PW, PH=PH)
    COVER[(doc, pg)] = cov
    for d in lines:
        if d["id"] in lm and not clean(lm[d["id"]]):
            cov["blank"].append(((d["x0"], d["y0"], d["x1"], d["y1"]), d["text"]))
    items, heads = [], []
    for d in lines:
        zh = lm.get(d["id"])
        if zh is None:
            if len(CYR.findall(d["text"])) >= 3:
                report.append((doc, pg, d["id"], d["text"]))
            continue
        zh = clean(zh)
        if not zh:
            continue
        if re.fullmatch(r"/?\s*(\d[\d+()]*\s*)?(根|件|个|间距\s*\d*)", zh):   # 纯数量/间距标注统一由单词框机制挂,避免整行合并后挂错
            continue
        if "中文译文见" in zh:
            heads.append((d, zh))
            zh = APPX.sub("", zh).strip(" ,，(（") or "说明"
            if (re.match(r"^\W*\d", d["text"]) or len(d["text"]) > 30) and len(zh) <= 8:
                continue   # 条文行上的「说明」定位标记不画(整块译文在旁边),否则像是该条的译文
        items.append(dict(zh=zh, box=lineboxes[d["id"]], src=lineboxes[d["id"]], id=d["id"], ru=d["text"],
                          low=d["id"] in low_lines))
    for ei, e in enumerate(mapping.get("extra") or []):
        zh = clean(e.get("zh"))
        if not zh:
            continue
        if re.fullmatch(r"/?\s*(\d[\d+()]*\s*)?(根|件|个|间距\s*\d*)", zh) and not str(e.get("src", "")).startswith("manual"):   # 目测补框的数量标注:改由单词框机制
            continue
        if compact and re.fullmatch(r"/?\s*(\d[\d+()]*\s*)?(根|件|个|间距\s*\d*)", zh):   # 精简版:不画数量/间距标注
            continue
        elow = ei in low_extra
        ishead = "中文译文见" in zh
        if ishead:
            zh = APPX.sub("", zh).strip(" ,，") or "说明"
        if e.get("anchor") is not None and int(e["anchor"]) in lineboxes:
            bb = lineboxes[int(e["anchor"])]
            if ishead:
                heads.append((byid[int(e["anchor"])], zh))
                if re.match(r"^\W*\d", byid[int(e["anchor"])]["text"]) or len(byid[int(e["anchor"])]["text"]) > 30:
                    continue   # 锚在条文行上的说明标记只用于定位,不画(否则像是该条的译文)
            items.append(dict(zh=zh, box=bb, src=bb, id=None, low=elow))
        elif e.get("box"):
            b = e["box"]
            bb = (b[0] * PW, b[1] * PH, b[2] * PW, b[3] * PH)
            pl.add(bb, pl.obs)
            if ishead:
                heads.append((dict(id=None, text="", x0=bb[0], y0=bb[1], x1=bb[2], y1=bb[3]), zh))
            items.append(dict(zh=zh, box=bb, src=bb, id=None, ru=e.get("ru"), low=elow))

    std2 = int(getattr(CTX.cfg, "output_standard", 1) or 1) >= 2
    prose_col, para_legend = [], []
    for q in (mapping.get("paras") or []) if std2 else []:
        b = q["box"]
        bb = (b[0] * PW, b[1] * PH, b[2] * PW, b[3] * PH)
        if q.get("kind") == "tb":
            para_legend.append((q.get("ru") or "", clean(q["zh"]))); cov["legend"].append(bb)
        else:
            prose_col.append((bb[1], clean(q["zh"]))); cov["notes"].append(bb)

    # Raster approval stamp ([scan] stamp_image_size + stamp_zh): its lines are merged into one
    # Chinese line placed just above the stamp.
    stamp = None
    ssz = list(CTX.cfg.stamp_image_size or [])
    try:
        sp = fitz.open(CTX.cfg.pdf)[pg - 1]
        for im in (sp.get_images(full=True) if len(ssz) == 2 and CTX.cfg.stamp_zh else []):
            if im[2] == ssz[0] and im[3] == ssz[1]:
                r = sp.get_image_bbox(im)
                stamp = (r.x0, r.y0, r.x1, r.y1)
                if sp.rotation:
                    rr = fitz.Rect(r) * sp.rotation_matrix
                    stamp = (rr.x0, rr.y0, rr.x1, rr.y1)
    except Exception:
        stamp = None
    if stamp:
        sx0, sy0, sx1, sy1 = stamp
        def in_stamp(bb):
            cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            return sx0 - 2 <= cx <= sx1 + 2 and sy0 - 2 <= cy <= sy1 + 2
        keep = []
        for it in items:
            if in_stamp(it["box"]) and CTX.cfg.stamp_merge_regex and re.search(CTX.cfg.stamp_merge_regex, it["zh"]):
                continue
            keep.append(it)
        items = keep
        items.append(dict(zh=CTX.cfg.stamp_zh,
                          box=(sx0, sy0, sx1, sy0 + 1), src=None, id=None, stamp=True, sb=(sx0, sy0, sx1, sy1)))

    # 自动:шт./ш. 小标注 → 按「单词框」精确挂「根」「间距」(不再按整行,避免挂错对象)
    def _ov(a, b):
        ix = min(a[2], b[2]) - max(a[0], b[0]); iy = min(a[3], b[3]) - max(a[1], b[1])
        return ix > 0 and iy > 0 and ix * iy > 0.3 * min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    TOK = re.compile(r"(шт|ш|wt|w|m|lut|lu)\.?\s*/\s*(p[cс]+[sѕ5]|[рp]с[s5]?|pc5|sp|ср|5р)\.?\s*[\d+()]*|шт\.\s*[кk]?\s*[\d+]+|\b(p[cс]+s|sp)\.\s*[\d+()]+", re.I)
    def kind(t):
        return CTX.cfg.count_word if re.search(r"шт|p[cс]+s|[рp]с|pc5|wt|lut", t, re.I) else "间距"
    toks = []
    for bb, t in words:
        if TOK.search(t) and not re.search(r"[а-яА-Я]{4,}", t):
            toks.append((bb, kind(t)))
    for bb, t in load_rapid(doc, pg, PW, PH):
        for m in TOK.finditer(t):
            n = max(len(t), 1); a, b = m.start() / n, m.end() / n
            if len(t.replace(" ", "")) > 1.8 * len(m.group(0).replace(" ", "")) + 4:
                continue   # 行内还有大量其他文字时,按比例估的位置不可靠
            tb_ = (bb[0] + (bb[2] - bb[0]) * a, bb[1], bb[0] + (bb[2] - bb[0]) * b, bb[3])
            if not any(_ov(tb_, o) for o, _ in toks):
                toks.append((tb_, kind(m.group(0))))
    for bb, z in toks:
        if any(_ov(bb, it["box"]) and re.search("根|件|间距", it["zh"]) for it in items):
            continue
        if not compact:
            items.append(dict(zh=z, box=bb, src=bb, id=None, tiny=True))
        pl.add(bb, pl.obs)

    buf = io.BytesIO()
    extra_w = 0.0
    draws = []        # (kind, payload) 延后绘制(可能需要加宽页面)

    legend = []
    legend_side = None
    tbl = [d for d in lines if re.search(r"Разраб|Проверил|ГИП|Н\.\s?контр|Стади|Листов|Утвержден|Назначение выпуска|Нач\.\s?отд|Гл\.\s?(констр|спец)" + (("|" + CTX.cfg.title_code_regex) if CTX.cfg.title_code_regex else ""), d["text"])
           and d["y0"] > PH * (0.78 if big else 0.70) and d["x0"] > PW * (0.40 if big else 0.10)]
    tbbox = None
    if len(tbl) >= 3:
        tbbox = (min(d["x0"] for d in tbl) - 3, min(d["y0"] for d in tbl) - 4, max(d["x1"] for d in tbl) + 12, max(d["y1"] for d in tbl) + 20)
    def in_tb(bb):
        if not tbbox:
            return False
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        return tbbox[0] <= cx <= tbbox[2] and tbbox[1] <= cy <= tbbox[3]

    def place(it):
        zh = it["zh"]; x0, y0, x1, y1 = it["box"]
        if it.get("stamp"):
            for fs in ((7.0, 6.0, 5.0, 4.2) if big else (4.6, 4.0, 3.4, 2.9, 2.5)):
                for wf in (1.0, 1.4, 2.0, 2.6):
                    ls = wrap(zh, fs, (x1 - x0) * wf); w = max(sw(t, fs) for t in ls); hh = len(ls) * fs * 1.18
                    for xx in (x0, x1 - w):
                        sy1 = it["sb"][3]
                        for bb in ((xx, y0 - 2 - hh, xx + w, y0 - 2), (xx, sy1 + 2, xx + w, sy1 + 2 + hh),
                                   (x0 - 4 - w, y0, x0 - 4, y0 + hh), (x1 + 4, y0, x1 + 4 + w, y0 + hh),
                                   (x0 - 4 - w, sy1 - hh, x0 - 4, sy1)):
                            pl.allow_out = True
                            okk = pl.ok(bb, ign=None, lines_ok=True)
                            pl.allow_out = False
                            if okk:
                                cov["stamp"] = it["sb"]
                                return ("h", fs, ls, bb), False
            # 兜底:章内上部空白处直接压印(章文本区与签名之间常有空白)
            sx0, sy0, sx1, sy1 = it["sb"]
            for fs in ((5.0, 4.2) if big else (3.4, 2.9, 2.5)):
                ls = wrap(zh, fs, (sx1 - sx0) - 4); hh = len(ls) * fs * 1.18
                for yy in np.arange(sy0 + 1, sy1 - hh, 1.5):
                    bb = (sx0 + 2, yy, sx1 - 2, yy + hh)
                    if pl.ok(bb, ign=None, lines_ok=True, ink_t=0.03):
                        cov["stamp"] = it["sb"]
                        return ("h", fs, ls, bb), False
            legend.append(("В ПРОИЗВОДСТВО РАБОТ", zh)); cov["stamp"] = it["sb"]
            report.append((doc, pg, "章译文入对照", ""))
            return None, False
        bw, bh0 = x1 - x0, y1 - y0
        vertical = bh0 > 2.5 * max(bw, 1) and bh0 > 25
        h = bw if vertical else bh0
        multi = False
        if not vertical:
            # 源框跨多行时,用框内单词的实际字高定字号,且不按标题处理
            wh = sorted(b[3] - b[1] for b, t in words if b[0] >= x0 - 2 and b[2] <= x1 + 2 and b[1] >= y0 - 2 and b[3] <= y1 + 2 and len(t.strip()) >= 2)
            if wh and wh[len(wh) // 2] < 0.62 * bh0:
                h = wh[len(wh) // 2] * 1.15; multi = True
        # 人工补框(无 OCR 行高可依)、图签内栏目:字号不超过正文典型字高
        if (it.get("id") is None and not multi) or in_tb(it["box"]):
            h = min(h, hmed * 1.1)
        TBW = r"修改|处数|文件号|签字|日期|图幅|替换|新增|编制|校核|批准|审查|负责人|主任|存档号|阶段|总页数|页次|^页$|会签|版权"
        if in_tb(it["box"]) or (vertical and it.get("id") is None) or (len(zh) <= 24 and re.search(TBW, zh)):   # 图签/侧栏小字:绝对上限
            h = min(h, (8.5 if big else 5.8) / FS_K)
        fmin = (6.0 if big else 4.5) if std2 else (4.5 if big else 3.2)
        fmax = 26 if big else 14
        fs0 = max(fmin, min(h * FS_K, fmax))
        if it.get("tiny"):
            fs0 = max(fmin, min(fs0, 9 if big else 6))
        short = len(zh) <= 5
        title = (not vertical) and (not multi) and bh0 > hmed * 1.45 and len(zh) >= 4
        cands = []
        tb = in_tb(it["box"])
        def inside_tb(bb):
            return tbbox[0] <= bb[0] and bb[2] <= tbbox[2] and tbbox[1] - 2 <= bb[1] and bb[3] <= tbbox[3]
        if not vertical:
            cx0, cy0, cx1, cy1, nwalls = pl.cell(it["box"], 120 if big else 60)
            incell = nwalls >= 3
            if incell:
                # 格内文字行(含英文对照行),用于逐行下移
                inner = [o for o in pl._all_hits((cx0, y0, cx1, cy1), pl.obs, 0) if o is not it["src"]]
                fmin_c = 5.0 if big else 4.0
                fs_list = []
                strict = set()
                f = fs0
                while f >= fmin_c - 1e-6:
                    fs_list.append(f); f *= 0.85
                for fs in fs_list:
                    g = fs * 0.15
                    full = zh if not short else zh
                    # (a) 正下方 / 逐行下移
                    room_w = cx1 - x0 - 1
                    ls = wrap(full, fs, max(room_w, fs * 3)) if sw(full, fs) > room_w else [full]
                    w = max(sw(t, fs) for t in ls); hh = len(ls) * fs * 1.18
                    xl = x0 if not title else max(cx0 + 1, (x0 + x1) / 2 - w / 2)
                    ys = [y1 + g] + sorted(o[3] + g for o in inner if o[1] >= y1 - 1 and o[0] < x0 + w and o[2] > x0)
                    for yy in ys:
                        bb = (xl, yy, xl + w, yy + hh)
                        if bb[3] <= cy1 and bb[2] <= cx1:
                            cands.append(("h", fs, ls, bb))
                    # (b) 右侧并排(本行或英文行右侧)
                    inl_t = "/" + zh if short else zh
                    iw = sw(inl_t, fs)
                    for o in [it["src"]] + [o for o in inner if o[1] >= y0 - 1]:
                        yc = (o[1] + o[3]) / 2 - fs * 0.6
                        bb = (o[2] + fs * 0.3, yc, o[2] + fs * 0.3 + iw, yc + fs * 1.18)
                        if bb[2] <= cx1 and bb[3] <= cy1 and bb[1] >= cy0:
                            cands.append(("h", fs, [inl_t], bb)); strict.add(len(cands) - 1)
                    # (c) 多行折行贴右侧剩余宽度
                    rw = cx1 - x1 - fs * 0.6
                    if rw > fs * 3 and not short:
                        ls2 = wrap(zh, fs, rw); hh2 = len(ls2) * fs * 1.18
                        bb = (x1 + fs * 0.3, y0, x1 + fs * 0.3 + max(sw(t, fs) for t in ls2), y0 + hh2)
                        if bb[3] <= cy1:
                            cands.append(("h", fs, ls2, bb)); strict.add(len(cands) - 1)
                for ci, c in enumerate(cands):
                    if tb and not inside_tb(c[3]):
                        continue
                    if pl.ok(c[3], ign=it["src"], lines_ok=ci not in strict):   # 并排类不得跨表格线
                        return c, False
                cands = []
                # 表格行太挤:排到表格外缘同一水平线(译文栏)
                lx, rx = pl.row_ends(it["box"])
                for fs in ([] if True else fs_list[:3]):   # 表外译文栏易被误读为无主译文,停用,改走引线
                    tw = sw(zh, fs); yc = (y0 + y1) / 2 - fs * 0.6
                    if rx is not None:
                        cands.append(("h", fs, [zh], (rx + fs * 0.5, yc, rx + fs * 0.5 + tw, yc + fs * 1.18)))
                    if lx is not None:
                        cands.append(("h", fs, [zh], (lx - fs * 0.5 - tw, yc, lx - fs * 0.5, yc + fs * 1.18)))
                for c in cands:
                    if tb and not inside_tb(c[3]):
                        continue
                    if pl.ok(c[3], ign=it["src"]):
                        return c, False
                cands = []
        for fs in sorted({fs0, max(fmin, fs0 * 0.86), max(fmin, fs0 * 0.74)}, reverse=True):
            g = fs * 0.18
            if vertical:
                w = sw(zh, fs)
                ls = [zh] if w <= bh0 * 1.6 else wrap(zh, fs, bh0 * 1.2)
                tw = max(sw(t, fs) for t in ls); th = len(ls) * fs * 1.15
                # 竖排:占位宽=th,高=tw;放在竖排原文右侧/左侧,底端对齐
                for xx in (x1 + g, x0 - g - th):
                    cands.append(("v", fs, ls, (xx, y1 - tw, xx + th, y1)))
                    cands.append(("v", fs, ls, (xx, y0, xx + th, y0 + tw)))
                continue
            maxw = max(bw * 1.08, fs * 10) if not short else 10 ** 6
            ls = wrap(zh, fs, maxw) if sw(zh, fs) > maxw else [zh]
            w = max(sw(t, fs) for t in ls); hh = len(ls) * fs * 1.18
            xl = (x0 + x1) / 2 - w / 2 if title else x0
            below = ("h", fs, ls, (xl, y1 + g, xl + w, y1 + g + hh))
            # 下方被文字挡住(如英文对照行)→ 放到挡住它的那一行下面(最多跨两行)
            belows = [below]
            cc = pl.colcell(it["box"])
            if cc and not vertical:   # 格内最后一行文字之下(多行格子)
                xl_, xr_, yb_ = cc
                low = max([o[3] for o in pl._all_hits((xl_ + 1, y0, xr_ - 1, yb_ - 1), pl.obs, 0) if o[1] >= y0 - 1] + [y1])
                fs_c = min(fs, (yb_ - low - 1.2) / 1.2)
                # 本格底部太挤 → 同一行右侧相邻空格(如「备注」栏)
                if fs_c < (6.0 if big else 4.6):
                    H_, W_ = pl.vl.shape
                    colv2 = pl.vl[max(0, int(y0)):min(H_, int(y1) + 1), :].mean(axis=0) > 0.7
                    xa_ = xr_
                    while xa_ < W_ - 1 and colv2[xa_ + 1]:
                        xa_ += 1          # 跳过粗线本身
                    xr2 = next((x for x in range(xa_ + 2, min(W_, xa_ + 400)) if colv2[x]), None)
                    if xr2 and xr2 - xa_ > fs * 4:
                        fsn = min(fs, max(fmin, (xr2 - xa_) / 8))
                        lsn = wrap(zh, fsn, xr2 - xa_ - 6); hn = len(lsn) * fsn * 1.18
                        ytop = max(y0 - 1, yb_ - 1 - hn - 1) if hn > yb_ - y0 else y0
                        bbn = (xa_ + 3, ytop, xa_ + 3 + max(sw(t, fsn) for t in lsn), ytop + hn)
                        if bbn[3] <= yb_ - 0.5 and pl._sum(pl.it, bbn) < 0.01:
                            belows.insert(0, ("h", fsn, lsn, bbn))
                if fs_c >= fmin * 0.95:
                    ls_c = wrap(zh, fs_c, xr_ - xl_ - 4) if sw(zh, fs_c) > xr_ - xl_ - 4 else [zh]
                    w_c = max(sw(t, fs_c) for t in ls_c); h_c = len(ls_c) * fs_c * 1.18
                    xc_ = min(max(xl_ + 2, x0), xr_ - 2 - w_c)
                    if low + 0.6 + h_c <= yb_ - 0.4:
                        belows.append(("h", fs_c, ls_c, (xc_, low + 0.6, xc_ + w_c, low + 0.6 + h_c)))
            if title:   # 标题:正下方居中放不下时,先试左对齐/右对齐,再考虑下移
                belows += [("h", fs, ls, (x0, y1 + g, x0 + w, y1 + g + hh)),
                           ("h", fs, ls, (x1 - w, y1 + g, x1, y1 + g + hh))]
            yy = y1
            for _ in range(4):   # 下方被多行文字挡住时逐行下移(最多 4 行)
                probe = (xl, yy + g, xl + w, yy + g + hh)
                o = None
                for o2 in pl._all_hits(probe, pl.obs, 0.2):
                    if o2 is not it["src"] and o2[1] >= yy - 1:
                        if o is None or o2[3] > o[3]:
                            o = o2
                if o is None:
                    break
                yy = o[3]
                belows.append(("h", fs, ls, (xl, yy + g, xl + w, yy + g + hh)))
            inl_t = "/" + zh if short else zh
            iw = sw(inl_t, fs)
            yc = (y0 + y1) / 2 - fs * 0.6
            inline = ("h", fs, [inl_t], (x1 + fs * 0.25, yc, x1 + fs * 0.25 + iw, yc + fs * 1.18))
            above = ("h", fs, ls, (xl, y0 - g - hh, xl + w, y0 - g))
            order = ([inline] + belows + [above]) if (short or it.get("tiny")) else (belows + [inline, above])
            if std2:                     # never above the Russian (docs/output_standard.md)
                order = [c for c in order if c is not above]
            cands += order
        for c in cands:
            if tb and not inside_tb(c[3]):
                continue
            if pl.ok(c[3], ign=it["src"]):
                return c, False
        if vertical:
            for c in cands:
                if pl.ok(c[3], ign=it["src"], lines_ok=True):
                    return c, False
            for fs in (fmin * 0.85, fmin * 0.7):
                ls = [zh]; tw = sw(zh, fs); th = fs * 1.15
                for xx in (x1 + 0.5, x0 - 0.5 - th):
                    for bb in ((xx, y1 - tw, xx + th, y1), (xx, y0, xx + th, y0 + tw), (xx, (y0 + y1) / 2 - tw / 2, xx + th, (y0 + y1) / 2 + tw / 2)):
                        pl.allow_out = True
                        okk = pl.ok(bb, ign=it["src"], lines_ok=True)
                        pl.allow_out = False
                        if okk:
                            return ("v", fs, ls, bb), False
        def ru_of():
            ru = it.get("ru") or " ".join(t for b, t in words if b[0] >= x0 - 1 and b[2] <= x1 + 1 and b[1] >= y0 - 1 and b[3] <= y1 + 1 and CYR.search(t))
            return re.sub(r"[|_]+", " ", ru or "").strip(" .:;,")
        if tb and not it.get("tiny"):
            legend.append((ru_of(), zh)); cov["legend"].append(it["box"]); return None, False
        if it.get("tiny"):
            for c in cands:
                if pl.ok(c[3], ign=it["src"], lines_ok=True):
                    return c, False
            for fs in (fmin,):   # 小标注不再缩到看不清,放不下就走引线
                g = fs * 0.15
                for bb in ((x1 + g, (y0 + y1) / 2 - fs * 0.6, x1 + g + sw("/" + zh, fs), (y0 + y1) / 2 + fs * 0.6),
                           (x0, y1 + g, x0 + sw(zh, fs), y1 + g + fs * 1.18),
                           (x0, y0 - g - fs * 1.18, x0 + sw(zh, fs), y0 - g))[:2 if std2 else 3]:
                    pl.allow_out = True    # 贴近图框边的小标注:允许越出内框检测
                    okk = pl.ok(bb, ign=it["src"], lines_ok=True)
                    pl.allow_out = False
                    if okk:
                        t = "/" + zh if bb[0] > x1 else zh
                        return ("h", fs, [t], bb), False
        # 引线兜底:半径内最近空白
        fs = max(fmin, fs0 * 0.9)
        ls = [zh] if sw(zh, fs) < 260 else wrap(zh, fs, 240 if big else 150)
        w = max(sw(t, fs) for t in ls); hh = len(ls) * fs * 1.18
        Rr = (120 if big else 45) if len(zh) > 7 else (80 if big else 35)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        best = None
        st = max(3.0, hh * 0.5)
        for yy0 in np.arange(cy - Rr, cy + Rr, st):
            for xx0 in np.arange(cx - Rr - w, cx + Rr, st):
                bb = (xx0, yy0, xx0 + w, yy0 + hh)
                dx = max(bb[0] - x1, x0 - bb[2], 0); dy = max(bb[1] - y1, y0 - bb[3], 0)
                dist = (dx * dx + dy * dy) ** 0.5
                if dist > Rr or (best and dist >= best[0]) or (std2 and bb[3] <= y0):
                    continue
                if pl.ok(bb, ign=it["src"]):
                    best = (dist, bb)
        if not best:
            Rr2 = Rr * 2
            st2 = st * 1.5
            for yy0 in np.arange(cy - Rr2, cy + Rr2, st2):
                for xx0 in np.arange(cx - Rr2 - w, cx + Rr2, st2):
                    bb = (xx0, yy0, xx0 + w, yy0 + hh)
                    dx = max(bb[0] - x1, x0 - bb[2], 0); dy = max(bb[1] - y1, y0 - bb[3], 0)
                    dist = (dx * dx + dy * dy) ** 0.5
                    if dist > Rr2 or (best and dist >= best[0]) or (std2 and bb[3] <= y0):
                        continue
                    if pl.ok(bb, ign=it["src"]):
                        best = (dist, bb)
        if best:
            report.append((doc, pg, "引线", zh[:30]))
            return ("h", fs, ls, best[1]), True
        ru = ru_of()
        if not it.get("tiny"):
            legend.append((ru, zh)); cov["legend"].append(it["box"]); report.append((doc, pg, "入对照", zh[:30])); return None, False
        report.append((doc, pg, "未放置", zh[:30])); cov["lost"].append((it["box"], zh))
        return None, False

    # 图签/侧栏标准栏目:统一进「图签译文」对照行(俄文键用规范写法),不在挤满签名的格子里硬塞
    CANON = {"修改": "Изм.", "修改处数": "Кол.уч.", "处数": "Кол.уч.", "页": "Лист", "文件号": "№ док.", "签字": "Подп.", "日期": "Дата",
             "替换": "Зам.", "新增": "Нов.", "编制": "Разраб.", "校核": "Проверил", "批准": "Утвердил", "标准化审查": "Н.контр.",
             "设计总负责人": "ГИП", "主任专业工程师": "Гл.спец.", "部门主任": "Нач.отд.", "总结构师": "Гл.констр.", "阶段": "Стадия",
             "总页数": "Листов", "页次": "Лист", "版次": "Рев.", "比例": "Масштаб", "质量": "Масса", "发布目的": "Назначение выпуска",
             "状态": "Статус", "会签": "Согласовано", "替代存档号": "Взам. инв. №", "签字及日期": "Подп. и дата", "原件存档号": "Инв. № подл.",
             "修改/处数/页/文件号/签字": "Изм./Кол.уч./Лист/№ док./Подп.", "修改/修改处数/页/文件号/签字": "Изм./Кол.уч./Лист/№ док./Подп.",
             "批准用于施工": "Утверждено для строительства", "准予施工": "В производство работ",
             "版权声明": "左侧竖排声明", "设计阶段:施工图(P/DD)": "Стадия Р"}
    tbz = None
    if std2:      # title-block zone: tbbox, else the area of the recognised standard fields (Разраб., Стадия ...)
        cb = [it["box"] for it in items if CANON.get(re.sub(r"\s+", "", it["zh"].strip(" /")))
              and (it["box"][1] + it["box"][3]) / 2 > PH * 0.7 and (it["box"][0] + it["box"][2]) / 2 > PW * 0.4]
        tbz = tbbox or ((min(b[0] for b in cb) - 10, min(b[1] for b in cb) - PH * 0.04, PW, PH) if len(cb) >= 3 else None)
    def in_tbz(bb):
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        return bool(tbz) and tbz[0] <= cx <= tbz[2] and tbz[1] <= cy <= tbz[3]
    whole = []    # title-block texts page_notes read whole from the image: they replace OCR fragments
    if std2 and tbz and trans:
        if trans.get("title_ru") and trans.get("title_zh"):
            whole.append((trans["title_ru"], clean(trans["title_zh"])))
        for b in trans.get("blocks") or []:
            if b.get("kind") == "pairs":
                whole += [(i.get("ru") or "", clean(i.get("zh"))) for i in b.get("items") or [] if i.get("zh")]
    whole_words = set(w.lower() for ru, _ in whole for w in re.findall(r"[А-Яа-яЁё]{3,}", ru))
    def covered_whole(ru):
        ws = [w.lower() for w in re.findall(r"[А-Яа-яЁё]{3,}", ru or "")]
        import difflib
        hit = sum(w in whole_words or bool(difflib.get_close_matches(w, whole_words, 1, 0.75)) for w in ws)
        return bool(ws) and hit / len(ws) >= 0.6
    keep = []
    for it in items:
        z = re.sub(r"\s+", "", it["zh"].strip(" /"))
        bx = it["box"]; cx = (bx[0] + bx[2]) / 2; cy = (bx[1] + bx[3]) / 2
        zone = in_tb(bx) or cx < PW * 0.09 or cy > PH * 0.80 or (stamp is not None and in_stamp(bx))
        key = CANON.get(z) or CANON.get(it["zh"].strip(" /"))
        if not key and zone:   # 组合栏目(如「左侧竖排(自上而下)替代存档号/签字及日期/原件存档号」)
            parts = [q for q in re.split(r"[/;、|]+", re.sub(r"^.*?[)）][:：]?", "", z)) if q]
            if parts and all(q in CANON for q in parts):
                key = "/".join(CANON[q] for q in parts)
        if z.startswith("图幅") and zone:
            key = "Формат"
        # sheet-frame-only fields (revision box, sheet count) belong in the legend wherever they sit
        if not zone and z in FRAME_ANYWHERE:
            zone, key = True, FRAME_ANYWHERE[z]
        elif not zone and re.fullmatch(r"总页数\d*", z):
            zone, key = True, "Листов"
        if zone and (z.startswith("本文件为") or z.startswith("版权声明")):
            key = "左侧竖排声明"
        side = std2 and cx < PW * 0.09 and (bx[3] - bx[1]) > 2.5 * max(bx[2] - bx[0], 1)   # left margin strip, vertical
        if std2 and not key and (in_tbz(bx) or side) and not it.get("tiny") and not it.get("stamp"):
            ru_ = re.sub(r"[|_]+", " ", it.get("ru") or "").strip(" .:;,")
            if not covered_whole(ru_) and (ru_ or not whole):
                legend.append((ru_, it["zh"].strip(" /")))
            cov["legend"].append(bx)
            continue
        if zone and key and not it.get("tiny") and not it.get("stamp"):
            zz = it["zh"].strip(" /")
            if key == "左侧竖排声明":
                zz = CTX.cfg.notice_zh or zz
            legend.append((key, zz)); cov["legend"].append(bx)
            continue
        keep.append(it)
    items = keep
    legend += para_legend + whole

    # 去重:连续行相同译文只标第一行
    done = []
    order = sorted(items, key=lambda it: (round(it["box"][1] / 4), it["box"][0]))
    # 先放普通标签(长的优先占位,短标签更灵活)
    for it in sorted(order, key=lambda it: -len(it["zh"]) if not it.get("tiny") else 1):
        x0, y0, x1, y1 = it["box"]
        lh = max(y1 - y0, 4.0)
        if any(z == it["zh"] and abs(ax - x0) < 25 and abs(ay - y0) < 2.0 * lh for z, ax, ay in done):
            done.append((it["zh"], x0, y0)); cov["dup"].append(it["box"]); continue
        done.append((it["zh"], x0, y0))
        c, leader = place(it)
        if c is None:
            continue
        cov["placed"].append((it["box"], it["zh"]))
        pl.add(c[3], pl.plc)
        draws.append(("lbl", c, leader, it["box"], bool(it.get("low"))))

    # ---- 栏目对照表(放不下的短栏目) ----
    if toks and not compact:   # 有数量/间距小标注的页:对照行首写明缩写含义
        legend.insert(0, ("шт./pcs.", CTX.cfg.count_word + "数"))
        if any(z == "间距" for _, z in toks):
            legend.insert(1, ("ш./sp.", "间距"))
    if any(d[0] == "lbl" and len(d) > 4 and d[4] for d in draws) and (tbbox or not std2):   # 有低置信度译文被画出:对照行写明标记含义
        legend.append(("", LOW_LEGEND))
    if legend:
        seen, ent = set(), []
        for ru, zh in legend:
            k = (ru.lower(), zh)
            if k in seen:
                continue
            seen.add(k)
            if ru and ru not in ("左侧竖排声明", "шт./pcs.", "ш./sp.") and re.search(r"[A-Za-z$|]", ru):
                ru = ""   # OCR 形近乱码键(如 Nogn/Pes)不显示
            if ru == "左侧竖排声明":
                ent.append(f"左侧竖排声明:{zh}"); continue
            ent.append(zh if (not ru or len(ru) > 30) else f"{ru}={zh}")
        fsL = 8.5 if big else 5.2
        ent = [e[1:] if e.startswith("=") else e for e in ent]
        txt = "图签译文:" + ";  ".join(ent)
        best = None
        tbw = (tbbox[2] - tbbox[0] - 60) if tbbox else PW * 0.38
        for wf, fk, ink in ((1.0, 1, 0.004), (1.25, 1, 0.004), (0.8, 1, 0.004), (1.0, 0.85, 0.008), (1.4, 0.8, 0.012), (0.7, 0.75, 0.015), (1.8, 0.7, 0.02)):
            fsL = (9.0 if big else 6.2) * fk
            bw = tbw * wf
            ls = wrap(txt, fsL, bw); w = max(sw(t, fsL) for t in ls); hh = len(ls) * fsL * 1.2
            tx, ty = ((tbbox[2] - 30), tbbox[1]) if tbbox else (PW - 20, PH * 0.75)
            for yy0 in np.arange(10, PH - hh - 10, 4):
                for xx0 in np.arange(PW * 0.35, PW - w - 8, 6):
                    bb = (xx0, yy0, xx0 + w, yy0 + hh)
                    d2 = (bb[2] - tx) ** 2 + 4 * (bb[3] - ty) ** 2
                    if best and d2 >= best[0]:
                        continue
                    if pl.ok(bb, ink_t=ink) and pl._sum(pl.il, bb) < 0.004:   # 对照行整体不压任何框线
                        best = (d2, bb, ls, fsL)
            if best:
                break
        if best:
            pl.add(best[1], pl.plc)
            draws.append(("lbl", ("h", best[3], best[2], best[1]), False, best[1]))
        else:
            legend_side = txt
            report.append((doc, pg, "对照表入加宽栏", txt[:40]))

    # ---- 说明条文整块 ----
    nb = notes_from_trans(trans)
    widen_items = []
    if nb and not heads and mapping.get("appendix"):
        top = sorted([d for d in lines if len(CYR.findall(d["text"])) >= 5 and d["y0"] > PH * 0.06], key=lambda d: d["y0"])
        if top:
            heads.append((top[0], "说明"))
    if nb and heads:
        heads.sort(key=lambda h: (h[0]["y0"], h[0]["x0"]))
        groups = []
        if len(nb) == len(heads):
            groups = [(heads[i][0], [nb[i]]) for i in range(len(nb))]
        else:
            groups = [(heads[0][0], nb)]
        for hd, blks in groups:
            # 俄文条文区域:标题下方、左缘相近、映射为空的行
            reg = [d for d in lines if d["y0"] >= hd["y0"] - 2 and abs(d["x0"] - hd["x0"]) < (60 if big else 30)
                   and d["y0"] - hd["y1"] < (0.45 * PH)]
            rx1 = max([d["x1"] for d in reg] + [hd["x1"]])
            ry1 = max([d["y1"] for d in reg] + [hd["y1"]])
            _yl = (tbbox[1] if tbbox else PH * 0.97)
            _reg2 = [d for d in lines if d["y0"] >= hd["y0"] - 2 and d["y1"] <= _yl and abs(d["x0"] - hd["x0"]) < (60 if big else 30)]
            _rx = max([d["x1"] for d in _reg2] + [rx1]); _ry = max([d["y1"] for d in _reg2] + [ry1])
            cov["notes"].append((hd["x0"] - 4, hd["y0"] - 2, _rx + 4, _ry + 2))
            rh = sorted(d["y1"] - d["y0"] for d in reg) or [hd["y1"] - hd["y0"]]
            fs = max(5.0 if big else 4.0, min(rh[len(rh) // 2] * 0.98, 16 if big else 9.5))
            paras = []
            for b in blks:
                hz = re.sub(r"[（(](俄文|俄文原文|俄文版)[)）]", "", clean(b.get("heading_zh") or "说明")).strip() or "说明"
                paras.append(("H", hz))
                for itn in b.get("items") or []:
                    no = (itn.get("no") or "").strip()
                    z = clean(itn.get("zh"))
                    paras.append(("P", (f"{no} " if no and not z.startswith(no) else "") + z, no, itn.get("ru") or ""))
            regw = max(rx1 - hd["x0"], 180 if big else 120)
            found = None
            for wfac in (1.0, 1.35, 0.8, 1.7):
                for fsc in (fs, fs * 0.88):
                    bw = regw * wfac
                    ls = []
                    for p in paras:
                        f2 = fsc * (1.1 if p[0] == "H" else 1.0)
                        for t in wrap(p[1], f2, bw):
                            ls.append((t, f2, p[0]))
                    bh = sum(f * 1.3 for _, f, _ in ls) + 4
                    best = None
                    st = 8 if big else 4
                    cx, cy = (hd["x0"] + rx1) / 2, (hd["y0"] + ry1) / 2
                    for yy0 in np.arange(10, PH - bh - 10, st * 2):
                        for xx0 in np.arange(10, PW - bw - 10, st * 2):
                            bb = (xx0, yy0, xx0 + bw, yy0 + bh)
                            d2 = (xx0 + bw / 2 - cx) ** 2 + (yy0 + bh / 2 - cy) ** 2
                            if best and d2 >= best[0]:
                                continue
                            if pl.ok(bb, ink_t=0.012) and pl._sum(pl.il, (bb[0] + 2, bb[1] + 2, bb[2] - 2, bb[3] - 2)) < 0.003:
                                best = (d2, bb)
                    if best:
                        found = (best[1], ls); break
                if found:
                    break
            if found:
                bb, ls = found
                pl.add(bb, pl.plc)
                draws.append(("blk", bb, ls))
            else:
                widen_items.append((hd, paras, fs))
                report.append((doc, pg, "加宽", "说明条文"))
    elif mapping.get("appendix") and nb:
        report.append((doc, pg, "说明无锚点", ""))

    # 无图面空白 → 右侧加宽对照栏,逐条与俄文条号对齐
    if widen_items:
        colw = (PW * 0.62) if not big else min(PW * 0.3, 900)
        extra_w = colw + 20
        X = PW + 14
        ylim = (tbbox[1] if tbbox else PH) - 4
        state = {}
        def numy(no, hd):
            no = re.sub(r"[.)\s]+$", "", no or "")
            key = id(hd)
            prev, xcol = state.get(key, (hd["y0"] - 2, None))
            if not re.fullmatch(r"\d{1,2}", no):
                return None
            xc = (hd["x0"] + hd["x1"]) / 2
            best = None
            for bb, t in words:
                if re.fullmatch(re.escape(no) + r"[.)]?", t.strip()) and prev < bb[1] < ylim and bb[0] < xc \
                        and (xcol is None or abs(bb[0] - xcol) < (25 if big else 14)):
                    if best is None or bb[1] < best[1]:
                        best = bb
            if best is None:
                return None
            state[key] = (best[1], best[0] if xcol is None else xcol)
            return best[1]
        import difflib
        def ruy(ru, hd):
            ru = re.sub(r"^\W*\d+[.)]?\s*", "", ru or "").strip()[:40].lower()
            if len(ru) < 8:
                return None
            key = ("ru", id(hd)); prev = state.get(key, hd["y0"] - 2)
            best = None
            for d in lines:
                if d["y0"] <= prev or d["y0"] > ylim:
                    continue
                t = re.sub(r"^\W*\d+[.)]?\s*", "", d["text"]).strip()[:len(ru)].lower()
                r = difflib.SequenceMatcher(None, ru, t).ratio()
                if r > 0.62 and (best is None or r > best[0] + 0.05 or (abs(r - best[0]) <= 0.05 and d["y0"] < best[1])):
                    best = (r, d["y0"])
            if best:
                state[key] = best[1]
                return best[1]
            return None
        for scale in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5):
            out = []; ycur = 20; state.clear()
            for hd, paras, fs in widen_items:
                fs = max(fs, 7.5 if not big else 9) * scale
                ycur = max(ycur, hd["y0"])
                for p in paras:
                    tgt = hd["y0"] if p[0] == "H" else (ruy(p[3], hd) or numy(p[2], hd))
                    f2 = fs * (1.12 if p[0] == "H" else 1.0)
                    if tgt is not None:
                        ycur = max(ycur, tgt)
                    for t in wrap(p[1], f2, colw - 10):
                        out.append(("txt", X, ycur, t, f2)); ycur += f2 * 1.3
                    ycur += f2 * 0.25
            if ycur < PH - 12:
                break
        draws += out

    # 成段文字(output_standard 2):右侧拓展栏,逐段与俄文段落顶部对齐,整句
    if prose_col:
        colw = (PW * 0.62) if not big else min(PW * 0.3, 900)
        extra_w = max(extra_w, colw + 20)
        X = PW + 14
        taken = [(d[2], d[2] + d[4] * 1.3) for d in draws if d[0] == "txt"]
        fsP = 12.0 if big else 9.0
        for scale in (1.0, 0.94, 0.89):
            f = fsP * scale; out = []; ycur = 18.0
            for top, zh in sorted(prose_col):
                ls = wrap(zh, f, colw - 10); hh = len(ls) * f * 1.3
                ycur = max(ycur, top)
                while any(a < ycur + hh and ycur < b for a, b in taken):
                    ycur = max(b for a, b in taken if a < ycur + hh and ycur < b) + f * 0.3
                for t in ls:
                    out.append(("txt", X, ycur, t, f)); ycur += f * 1.3
                ycur += f * 0.35
            if ycur < PH - 12:
                break
        draws += out
        report.append((doc, pg, "加宽", "成段文字"))

    # 图签对照行在图面放不下 → 放到右侧加宽栏底部(没有加宽栏就新开一栏)
    if legend_side:
        fsL = 9 if big else 6.5
        colw2 = (PW * 0.62) if not big else min(PW * 0.3, 900)
        if not extra_w:
            extra_w = colw2 + 20
        lsL = wrap(legend_side, fsL, extra_w - 30)
        yy = PH - 20 - len(lsL) * fsL * 1.3
        yy = max(yy, max([d[2] + d[4] * 1.3 for d in draws if d[0] == "txt"] + [0]) + 10)
        for t in lsL:
            draws.append(("txt", PW + 14, yy, t, fsL)); yy += fsL * 1.3

    # ---- 绘制 ----
    c = canvas.Canvas(buf, pagesize=(PW + extra_w, PH))
    c.setFillColorRGB(*BLUE); c.setStrokeColorRGB(*BLUE)
    def Y(y):  # top-origin → pdf
        return PH - y
    n = 0
    for d in draws:
        if d[0] == "lbl":
            _, (kind, fs, ls, bb), leader, src = d[:4]
            low = len(d) > 4 and d[4]
            c.setFont(FONT, fs)
            if kind == "v":
                c.saveState()
                c.translate(bb[0] + fs * 0.92, Y(bb[3]))
                c.rotate(90)
                yy = 0
                for t in ls:
                    c.drawString(0, yy, t); yy -= fs * 1.15
                    if low:
                        _dots(c, 0, yy + fs * 1.15 - fs * 0.16, sw(t, fs))
                c.restoreState()
            else:
                yy = bb[1]
                for t in ls:
                    c.drawString(bb[0], Y(yy + fs * 0.92), t); yy += fs * 1.18
                    if low:
                        _dots(c, bb[0], Y(yy - fs * 0.14), sw(t, fs))
            if leader:
                c.setLineWidth(0.3)
                sx = min(max((bb[0] + bb[2]) / 2, src[0]), src[2]); sy = src[3] if bb[1] > src[3] else src[1]
                tx = min(max(sx, bb[0]), bb[2]); ty = bb[1] if bb[1] > src[3] else bb[3]
                c.line(sx, Y(sy), tx, Y(ty))
            n += 1
        elif d[0] == "blk":
            _, bb, ls = d
            yy = bb[1] + 2
            for t, f, kind in ls:
                c.setFont(FONT, f)
                c.drawString(bb[0] + 2, Y(yy + f * 0.95), t); yy += f * 1.3
            n += 1
        elif d[0] == "txt":
            _, X, yy, t, f = d
            c.setFont(FONT, f); c.drawString(X, Y(yy + f * 0.95), t)
    if extra_w:
        c.setLineWidth(0.6)
        c.line(PW + 4, 10, PW + 4, PH - 10)
        c.setFont(FONT, 9 if big else 6.5)
        c.drawString(PW + 14, 8, "← 左侧俄文段落的中文对照(逐段对齐)" if prose_col else
                     "← 左侧俄文说明条文的中文对照(逐条对齐)" if widen_items else "← 左侧图签栏目的中文对照")
    c.save(); buf.seek(0)
    ov = PdfReader(buf).pages[0]
    if extra_w:
        page.mediabox = RectangleObject([0, 0, PW + extra_w, PH])
        page.cropbox = RectangleObject([0, 0, PW + extra_w, PH])
    page.merge_page(ov)
    return page, n
