"""Step 5 — coverage audit: every Russian text region must be accounted for.

    python scripts/scan/30_audit.py [--config project.toml] [--pages 3,5] [--no-packets]

For each page the engine is run once more and every Russian text box found by either
detector (RapidOCR boxes, plus Tesseract lines the map left blank) is checked against what
the engine actually did: placed Chinese, legend line, notes block, stamp, duplicate of a
neighbour, or an ``accept`` entry from an earlier review.  What is left becomes a candidate:

    #k [未覆盖]   no translation at all
    #k [空映射]   the map set this line to "" (said "merged / not needed") — often wrong
    #k [放置失败] translated but could not be placed
    Ck            a line directly above/below a translated line: probably its continuation

Candidates matching names / codes / standards are skipped automatically (packets/_autoskip.log).
Per page: work/audit/pNN.json and, for review, work/packets/pNN.md + pNN_sK.png (crops with
the box in red) + pNN.cands.json (the ids the review answers refer to).
Delivery gate: ``remaining 0``.
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.getLogger("pypdf").setLevel(logging.ERROR)
import pymupdf  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from scan import ctx as C, engine  # noqa: E402

CYR = C.CYR
MARK = re.compile(r"[БПРКМбпркм][мmMм]\s?\d[\d.,]*|[МM][НH]\d")
TINY = re.compile(r"pcs|(^|[\s/])шт\.?|(^|[\s/])ш\.\s*/|/\s*sp\.", re.I)
LOOK = str.maketrans("aehopcxkmtby36", "аенорсхкмтвузб")   # Latin/digit look-alikes -> Cyrillic


def ov(a, b, pad=1.5):
    ix = min(a[2], b[2] + pad) - max(a[0], b[0] - pad); iy = min(a[3], b[3] + pad) - max(a[1], b[1] - pad)
    if ix <= 0 or iy <= 0:
        return 0.0
    return ix * iy / max((a[2] - a[0]) * (a[3] - a[1]), 1e-6)


def norm_ru(t):
    return re.sub(r"[^а-яa-z0-9$]", "", t.lower().replace("ё", "е")).translate(LOOK)


def make_auto_skip(cfg):
    names = [norm_ru(n)[:6] for n in cfg.names if len(norm_ru(n)) >= 4]
    skip = re.compile(cfg.skip_regex, re.I) if cfg.skip_regex else None

    def auto_skip(t):
        n = t.lower().replace("ё", "е"); n2 = norm_ru(t)
        if names and any(x in n2 for x in names):
            return "人名/标识"
        if skip and skip.search(t):
            return "项目跳过规则"
        if re.fullmatch(r"\W*[оОoO0]{2,3}\W*", t):
            return "钢筋圆点误识"
        if re.search(r"гост|gost|foct|toct|со\$т|созт", n):
            rest = re.sub(r"гост|gost|foct|toct|со\$т|созт|[нhт][0-9]|р[шф]|мф", "", n)
            if len(re.findall(r"[а-я]", rest)) <= 2:
                return "标准号/焊缝代号"
        return None
    return auto_skip


def audit(ctx, pg):
    notes = C.load_notes(ctx, pg)
    rep = []
    engine.annotate(ctx.cfg.code, pg, C.load_map(ctx, pg), notes, rep)
    cov = engine.COVER[(ctx.cfg.code, pg)]
    PW, PH = cov["PW"], cov["PH"]
    good = [b for b, _ in cov["placed"]] + cov["legend"] + cov["dup"] + cov["notes"] + ([cov["stamp"]] if cov["stamp"] else [])
    blank = [b for b, _ in cov["blank"]]
    for x in C.load_map(ctx, pg).get("accept", []):
        bx = x["box"]; good.append((bx[0] * PW, bx[1] * PH, bx[2] * PW, bx[3] * PH))
    notes_txt = ""
    if cov["notes"] or any(r[2] == "加宽" for r in rep):
        notes_txt = " ".join((i.get("ru") or "") for b in (notes or {}).get("blocks", []) if b.get("kind") == "notes"
                             for i in (b.get("items") or [])).lower().replace("ё", "е")

    def in_notes(t):   # line already contained in the translated notes block
        ws = re.findall(r"[а-яё]{3,}", t.lower().replace("ё", "е"))
        return bool(notes_txt and ws) and sum(w in notes_txt for w in ws) / len(ws) >= 0.7

    rapid = C.load_rapid(ctx, pg, PW, PH)
    res, claimed, cont = [], [], []
    for bb, t in rapid:
        t0 = MARK.sub("", t)
        tiny = TINY.search(t)
        if len(CYR.findall(t0)) < 2 and not tiny:
            continue
        lat, cy = len(re.findall(r"[A-Za-z]", t0)), len(CYR.findall(t0))
        if lat > 2 * cy and cy <= 3:       # English line with a member mark in it
            continue
        if sum(ov(bb, g) for g in good) >= 0.5 or in_notes(t):
            continue
        rec = dict(box=[round(v, 1) for v in bb], text=t)
        h = bb[3] - bb[1]; nb = None
        for pb, z in cov["placed"]:
            if len(z) <= 2 or tiny:
                continue
            if min(pb[2], bb[2]) - max(pb[0], bb[0]) <= 0.3 * min(pb[2] - pb[0], bb[2] - bb[0]):
                continue
            gap = max(pb[1] - bb[3], bb[1] - pb[3])
            if gap <= 1.9 * max(h, pb[3] - pb[1]) and (nb is None or gap < nb[0]):
                nb = (gap, pb, z)
        if nb:
            rec["nbz"] = nb[2]
            rec["nbru"] = " ".join(tt for b2, tt in rapid if ov(b2, nb[1]) > 0.5)[:80]
            cont.append(rec); continue
        (claimed if sum(ov(bb, g) for g in blank) >= 0.5 else res).append(rec)
    seen = [r["box"] for r in res + claimed + cont]
    for bb, t in cov["blank"]:          # Tesseract lines left blank (RapidOCR may read Cyrillic as Latin)
        if len(CYR.findall(t)) < 3 or sum(ov(bb, g) for g in good) >= 0.5 or in_notes(t) \
                or any(ov(bb, s2) > 0.3 for s2 in seen):
            continue
        claimed.append(dict(box=[round(v, 1) for v in bb], text=t, src="tess"))
    out = dict(pg=pg, PW=PW, PH=PH, residual=res, blank_claimed=claimed, cont=cont,
               lost=[dict(box=list(b), zh=z) for b, z in cov["lost"]], report=[r for r in rep if not str(r[2]).isdigit()])
    ctx.audit(pg).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def near(bb, others, r):
    out = []
    for ob, t in others:
        dx = max(ob[0] - bb[2], bb[0] - ob[2], 0); dy = max(ob[1] - bb[3], bb[1] - ob[3], 0)
        d = (dx * dx + dy * dy) ** .5
        if d <= r and tuple(ob) != tuple(bb):
            out.append((d, ob, t))
    return sorted(out)[:6]


def packet(ctx, pg, a, auto_skip, fnt):
    """Review packet: text list + crop sheets.  Returns number of candidates."""
    P = ctx.w / "packets"
    for old in P.glob(f"p{pg:02d}*"):
        old.unlink()
    cands = [dict(r, kind="未覆盖") for r in a["residual"]] + [dict(r, kind="空映射") for r in a["blank_claimed"]]
    cands += [dict(box=r["box"], text="(已有译文但放不下)" + r["zh"], kind="放置失败") for r in a["lost"]]
    keep, conts = [], []
    with open(P / "_autoskip.log", "a", encoding="utf-8") as lf:
        for c in cands:
            why = auto_skip(c["text"]) if c["kind"] != "放置失败" else None
            if why:
                lf.write(f"p{pg:02d}\t{why}\t{c['text']}\n")
            else:
                keep.append(c)
        for c in a.get("cont") or []:
            if not auto_skip(c["text"]):
                conts.append(c)
    cands = keep
    if not cands and not conts:
        return 0
    PW, PH = a["PW"], a["PH"]; big = max(PW, PH) > 1000
    cov = engine.COVER.get((ctx.cfg.code, pg))
    placed = [(tuple(b), z) for b, z in (cov["placed"] if cov else [])]
    rap = [(tuple(b), t) for b, t in C.load_rapid(ctx, pg, PW, PH)]
    src = pymupdf.open(ctx.cfg.pdf)[pg - 1]
    title = (C.load_notes(ctx, pg) or {}).get("title_zh", "")
    md = [f"## 第{pg}页  {title}  (页面 {PW:.0f}x{PH:.0f}pt)", ""]
    crops = []
    for i, c in enumerate(cands, 1):
        bb = c["box"]; h = bb[3] - bb[1]; w = bb[2] - bb[0]
        mx = max(w * 0.6, h * 6, 40 if big else 25); my = max(h * 3.5, 22 if big else 14)
        vx0, vy0, vx1, vy1 = max(0, bb[0] - mx), max(0, bb[1] - my), min(PW, bb[2] + mx), min(PH, bb[3] + my)
        clip = pymupdf.Rect(vx0, vy0, vx1, vy1)
        if src.rotation:                       # clip is in visual coordinates
            clip = clip * src.derotation_matrix; clip.normalize()
        pix = src.get_pixmap(matrix=pymupdf.Matrix(300 / 72, 300 / 72), clip=clip, colorspace=pymupdf.csRGB)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        k = im.width / (vx1 - vx0)
        ImageDraw.Draw(im).rectangle([(bb[0] - vx0) * k - 3, (bb[1] - vy0) * k - 3, (bb[2] - vx0) * k + 3,
                                      (bb[3] - vy0) * k + 3], outline=(230, 0, 0), width=3)
        if im.width > 1500:
            im = im.resize((1500, int(im.height * 1500 / im.width)))
        if im.height > 420:
            im = im.resize((int(im.width * 420 / im.height), 420))
        crops.append((i, im))
        ctxl = []
        for d, ob, t in near(bb, rap, 60 if big else 30):
            zz = [z for pb, z in placed if min(pb[2], ob[2]) > max(pb[0], ob[0]) and min(pb[3], ob[3]) > max(pb[1], ob[1])]
            ctxl.append(f"「{t}」" + (f"→{zz[0]}" if zz else ""))
        md.append(f"#{i} [{c['kind']}] 识别:「{c['text']}」  box={bb}")
        if ctxl:
            md.append("   邻近: " + " | ".join(ctxl))
    sheets, cur, hh = [], [], 0
    for i, im in crops:
        if cur and hh + im.height + 40 > 1900:
            sheets.append(cur); cur, hh = [], 0
        cur.append((i, im)); hh += im.height + 40
    if cur:
        sheets.append(cur)
    for k, sh in enumerate(sheets, 1):
        S = Image.new("RGB", (max(im.width for _, im in sh) + 10, sum(im.height + 40 for _, im in sh)), "white")
        d = ImageDraw.Draw(S); y = 0
        for i, im in sh:
            d.text((4, y + 4), f"#{i}", fill=(230, 0, 0), font=fnt); S.paste(im, (6, y + 36)); y += im.height + 40
        fn = P / f"p{pg:02d}_s{k}.png"; S.save(fn)
        md.append(f"裁图联排: {fn}  (含 #{sh[0][0]}–#{sh[-1][0]})")
    snap = {f"#{i}": c for i, c in enumerate(cands, 1)}
    snap.update({f"C{j}": c for j, c in enumerate(conts, 1)})
    (P / f"p{pg:02d}.cands.json").write_text(json.dumps(dict(PW=PW, PH=PH, cands=snap), ensure_ascii=False), encoding="utf-8")
    if conts:
        md += ["", "### 续行核对(纯文字,无需看图):判断该俄文是否已包含在紧邻行的中文里"]
        for j, c in enumerate(conts, 1):
            md.append(f"C{j} 「{c['text']}」 box={c['box']}  紧邻行「{c.get('nbru', '')}」→已译「{c.get('nbz', '')}」")
    (P / f"p{pg:02d}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return len(cands) + len(conts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--pages")
    ap.add_argument("--no-packets", action="store_true")
    a = ap.parse_args()
    cfg = C.load(a.config)
    ctx = C.Ctx(cfg); ctx.mkdirs(); engine.init(ctx)
    n = pymupdf.open(cfg.pdf).page_count
    pages = list(range(1, n + 1))
    if a.pages:
        pages = []
        for part in a.pages.split(","):
            x, _, y = part.partition("-"); pages += list(range(int(x), int(y or x) + 1))
    if not a.pages:
        (ctx.w / "packets" / "_autoskip.log").write_text("", encoding="utf-8")
    auto_skip = make_auto_skip(cfg)
    fnt = ImageFont.truetype(C.font_file(cfg), 26)
    total, per = 0, []
    for pg in pages:
        if not ctx.lines(pg).exists():
            continue
        res = audit(ctx, pg)
        nc = 0 if a.no_packets else packet(ctx, pg, res, auto_skip, fnt)
        raw = len(res["residual"]) + len(res["blank_claimed"]) + len(res["lost"]) + len(res["cont"])
        if raw:
            print(f"p{pg:02d}: 未覆盖 {len(res['residual'])}  空映射 {len(res['blank_claimed'])}  "
                  f"放置失败 {len(res['lost'])}  续行 {len(res['cont'])}  -> 待复核 {nc}", flush=True)
        total += nc
        if nc:
            per.append(pg)
    print(f"remaining {total}  pages {per}")
    if not any(ctx.rapid(p).exists() for p in pages):
        print("note: no work/rapid/*.tsv — the audit only sees Tesseract lines; run 11_rapid.py for a real check")


if __name__ == "__main__":
    main()
