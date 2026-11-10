"""Code-side line mapping for the scanned route: TM + page_notes -> work/map + work/notes.

    automap.write(ctx, tm, notes_results)      # notes_results: {page: page_notes output | None}

Per page:
  work/notes/pNN.json      from the page_notes answer (same format the engine / TOC read today):
                           sheet_code, format, title_ru, title_zh, blocks [notes per region, pairs]
  work/map/pNN.json        every Cyrillic Tesseract line -> TM Chinese (numbers substituted from the
                           line) or "" (keep / skip / notes clause / count label / no TM entry);
                           the notes heading (or clause 1) -> 说明(中文译文见右侧), appendix true;
                           Russian read only by RapidOCR -> extra {box, zh, ru}
  work/map/pNN.meta.json   provenance and confidence per line / extra (low = fuzzy TM hit, LLM conf
                           low, or a number dropped by the digit guard)

Digit guard: a number reaches the Chinese only if Tesseract and RapidOCR read the same number on
that box (Ø/@/2 confusion reconciled), or page_notes read it from the image (labels / notes);
otherwise it is dropped from the Chinese (the Russian stays visible) and the line is low-confidence.
Count labels (4根 / 间距) are never written: the engine labels шт./pcs and ш./sp per OCR word.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from scan import packets as K
from scan import segments as S

APPX_ZH = "说明(中文译文见右侧)"
CJK = re.compile(r"[一-鿿]")
COUNT_ZH = re.compile(r"^/?\s*(\d[\d+()]*\s*)?(根|件|个|间距\s*\d*)$")
MARK = re.compile(r"[БПРКМбпркм][мmM]\s?\d[\d.,]*|[МM][НH]\d")
UNIT = r"(?:\s*(?:mm|毫米|мм|m|米|kg|кг|t|吨|°|%|кПа|kPa))?"


def in_tb(box, tb):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return bool(tb) and tb[0] <= cx <= tb[2] and tb[1] <= cy <= tb[3]


def render(hit, nums, ok, key_used):
    """TM value -> Chinese for this occurrence.  Returns (zh, dropped_numbers)."""
    zh = hit.zh or ""
    if hit.action != "translate" or not zh:
        return "", 0
    dropped = 0
    n_key = len(S.T.PH.findall(key_used))
    if len(nums) != n_key:                      # fuzzy hit with another number layout: no numbers
        ok = [False] * n_key
        nums = nums + [""] * (n_key - len(nums))

    def rep(m):
        nonlocal dropped
        k = int(m.group(2)) - 1
        if k < len(nums) and k < len(ok) and ok[k] and nums[k]:
            ph = f"⟨{k + 1}⟩"
            pos = key_used.find(ph)
            after = key_used[pos + len(ph):pos + len(ph) + 2] if pos >= 0 else ""
            return m.group(1) + S.T.fmt_num(nums[k], after) + m.group(3)
        dropped += 1
        return ""
    # a dropped number takes its sign (Ø, №, ×, +) and unit with it, and a member-mark prefix
    # glued to it (МН⟨1⟩, Км⟨1⟩.⟨2⟩): a mark without its number means nothing
    zh = re.sub(r"((?:(?<![A-Za-zА-Яа-яЁё])[А-ЯЁA-Z][а-яёa-zА-ЯA-Z]?(?=⟨))?[Ø№#×x*+±]?\s?)⟨(\d+)⟩(" + UNIT + ")", rep, zh)
    if hit.bare:                                 # digits in the TM value itself (not placeholders)
        zh2 = re.sub(r"[Ø№#×x*+]?\s?(?<![\w.])\d+(?:[.,]\d+)*(?![\d.,]|月)" + UNIT, "", zh)
        if zh2 != zh:
            dropped += 1; zh = zh2
    if dropped:      # separators left without neighbours: 「柱 、」「Км1.1…」
        for _ in range(2):
            zh = re.sub(r"(^|[\s(（])[、,，;；…~\-]+", lambda m: m.group(1), zh)
            zh = re.sub(r"[、,，;；…~\-]+(?=$|[\s)）、,，;；])", "", zh)
    zh = re.sub(r"\s{2,}", " ", zh)
    zh = re.sub(r"\s*([,，、;；:：])\s*(?=[,，、;；:：)）]|$)", "", zh)
    zh = re.sub(r"[(（]\s*[)）]", "", zh).strip(" ,，、;；:：-—/")
    # RU/EN pair that repeats the same number (Поз.2 / Item 2 -> 件号2 / 2): keep it once
    zh = re.sub(r"^(\D*?)\s?(\d+(?:\.\d+)?)\s*/\s*\2$", r"\1 \2", zh).strip()
    return zh, dropped


def _notes_file(pg, res, groups):
    """page_notes answer -> notes/pNN.json (engine / TOC format)."""
    blocks = []
    for g in groups:
        if g["items"]:
            blocks.append(dict(kind="notes", heading_zh="说明", items=[
                dict(no=str(i.get("no") or ""), ru=i.get("ru") or "", zh=i.get("zh") or "") for i in g["items"]]))
    labels = [dict(ru=x.get("ru") or "", zh=x.get("zh") or "") for x in (res.get("labels") or []) if x.get("zh")]
    if labels:
        blocks.append(dict(kind="pairs", heading_zh="图面标注", items=labels))
    return {"page": pg, "sheet_code": res.get("sheet_code") or "", "format": res.get("format") or "",
            "title_ru": res.get("title_ru") or "", "title_zh": res.get("title_zh") or "",
            "blocks": blocks, "translator_notes": [], "issues": list(res.get("issues") or []),
            "_src": "page_notes"}


def _assign_notes(P, regions, notes):
    """Split page_notes clauses over the detected regions (by shared words)."""
    def words(t):
        return {w for w in re.findall(r"[a-zа-я]{4,}", S.fold(t))}
    byid = {d["id"]: d for d in P.lines}
    rw = [set().union(*[words(byid[i]["text"]) for i in r["lines"] if i in byid]) for r in regions]
    groups = [dict(region=r, items=[]) for r in regions]
    if not groups:
        return []
    for it in notes:
        w = words(it.get("ru") or "")
        sc = [len(w & x) / max(1, len(w)) for x in rw]
        best = max(range(len(sc)), key=lambda i: (sc[i], regions[i]["kind"] == "ru"))
        if sc[best] < 0.25:
            ru_regs = [i for i, r in enumerate(regions) if r["kind"] == "ru"] or [0]
            best = ru_regs[0]
        groups[best]["items"].append(it)
    # English duplicate of a Russian block: Chinese goes with the Russian region only
    if any(g["items"] and g["region"]["kind"] == "ru" for g in groups):
        ru0 = next(g for g in groups if g["items"] and g["region"]["kind"] == "ru")
        for g in groups:
            if g["region"]["kind"] == "en" and g["items"]:
                if not any(_same_clause(i, j) for i in g["items"] for j in ru0["items"]):
                    ru0["items"] += g["items"]
                g["items"] = []
    return groups


def _same_clause(a, b):
    return (a.get("zh") or "").strip() == (b.get("zh") or "").strip()


MARK_RE = re.compile(r"([А-ЯЁ]м|МН)\s?(\d+(?:\.\d+)?(?:-\d+)?)")
# letters OCR confuses inside member marks (Бм <-> Вм, Пм <-> Рм <-> Пм)
MARK_CONF = [set("БВ"), set("ПР")]


def known_marks(ctx, notes_results: dict) -> set:
    """Member marks of the volume (sheet titles / notes read from the image) + related volumes."""
    txt = json.dumps(notes_results or {}, ensure_ascii=False)
    for f in sorted((ctx.w / "notes").glob("p??.json")):
        txt += f.read_text(encoding="utf-8")
    for src in getattr(ctx.cfg, "mark_sources", None) or []:
        for f in sorted(C_p(ctx.cfg.root, src).glob("p??.json")):
            txt += f.read_text(encoding="utf-8")
    return {a + b for a, b in MARK_RE.findall(S.norm_marks(txt))}


def C_p(root, v):
    p = Path(v)
    return p if p.is_absolute() else Path(root) / p


def fix_marks(zh: str, known: set):
    """Marks in the Chinese that are not known but match exactly one known mark up to an
    OCR-confusable letter are corrected.  -> (zh, n_fixed, n_unknown)"""
    if not known:
        return zh, 0, 0
    fixed = unknown = 0

    def rep(m):
        nonlocal fixed, unknown
        mk = m.group(1) + m.group(2)
        if mk in known:
            return m.group(0)
        L = m.group(1)[0]
        grp = next((g for g in MARK_CONF if L in g), {L})
        cands = [k for k in known if k[0] in grp and k[1:] == mk[1:]]
        if not cands:                     # OCR dropped the dot: Км12 -> Км1.2
            cands = [k for k in known if k[0] in grp and k[1] == mk[1] and "." in k and k[2:].replace(".", "") == mk[2:]]
        if len(cands) == 1:
            fixed += 1
            return cands[0]
        unknown += 1
        return m.group(0)
    return MARK_RE.sub(rep, zh), fixed, unknown


def stamp_box(ctx, pg: int):
    """Raster approval stamp ([scan] stamp_image_size + stamp_zh) as page fractions, or None.
    Same detection as engine.annotate: its lines are covered by the one stamp_zh line."""
    ssz = list(getattr(ctx.cfg, "stamp_image_size", None) or [])
    if len(ssz) != 2 or not getattr(ctx.cfg, "stamp_zh", ""):
        return None
    try:
        import pymupdf
        sp = pymupdf.open(ctx.cfg.pdf)[pg - 1]
        for im in sp.get_images(full=True):
            if im[2] == ssz[0] and im[3] == ssz[1]:
                r = pymupdf.Rect(sp.get_image_bbox(im))
                if sp.rotation:
                    r = r * sp.rotation_matrix
                W, H = sp.rect.width, sp.rect.height
                return (r.x0 / W, r.y0 / H, r.x1 / W, r.y1 / H)
    except Exception:
        return None
    return None


def _in_box(b, s):
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return s is not None and s[0] - 0.002 <= cx <= s[2] + 0.002 and s[1] - 0.002 <= cy <= s[3] + 0.002


def map_page(ctx, pg: int, tm, notes_res: dict | None = None, known: set | None = None):
    """-> (map, meta, notes_json or None) for one page (pure: writes nothing)."""
    P = S.load_page(ctx, pg)
    tb = K.title_block(P, getattr(ctx.cfg, "title_code_regex", ""))
    regions = K.notes_regions(P, tb)
    stamp = stamp_box(ctx, pg)
    notes_json, groups = None, []
    labels_txt = []
    if notes_res:
        groups = _assign_notes(P, regions, notes_res.get("notes") or [])
        notes_json = _notes_file(pg, notes_res, [g for g in groups if g["items"]] or
                                 ([dict(items=notes_res.get("notes") or [])] if notes_res.get("notes") else []))
        labels_txt = [x.get("ru") or "" for x in (notes_res.get("labels") or [])]
        labels_txt += [notes_res.get("title_ru") or "", notes_res.get("sheet_code") or ""]
    used = [g for g in groups if g["items"]]
    if notes_res and notes_res.get("notes") and not used and regions:
        used = [dict(region=regions[0], items=notes_res["notes"])]
    notes_lines, heads = set(), {}
    for g in used:
        r = g["region"]
        notes_lines |= set(r["lines"])
        heads[r["head"] if r["head"] is not None else r["first"]] = True
    lines, meta, extra, xmeta = {}, {}, [], []

    def confirm_pool(text, others):
        """the other engine's reading + labels page_notes read from the image for this text"""
        sk = S.skeleton(S.make_key(text)[0])
        lab = []
        for t in labels_txt:
            sk2 = S.skeleton(S.make_key(t)[0])
            if not t or not sk2:
                continue
            if (len(sk) >= 3 and sk in sk2) or (len(sk) < 3 and len(sk2) < 6) or                     S.difflib.SequenceMatcher(None, sk, sk2).ratio() >= 0.75:
                lab.append(t)
        return others + lab

    tl_cyr = [d for d in P.lines if d["cyr"] >= 2]
    for d in P.lines:
        rid = d["id"]
        text, src = d["text"], "tess"
        if d["cyr"] < 2:
            # a Latin-looking Tesseract line where RapidOCR read Russian (Tp./pipe -> Тр./pipe)
            rr = [r for r in P.rapid if S.cyr_count(r["text"]) >= 2 and S.ov(r["box"], d["box"]) >= 0.5
                  and sum(S.ov(r["box"], o["box"]) for o in tl_cyr) < 0.3]
            if rid in heads:
                lines[str(rid)] = APPX_ZH; meta[str(rid)] = dict(conf="high", src="notes", why="notes heading")
                continue
            if rid in notes_lines:
                continue
            if rr:
                text, src = rr[0]["text"], "rapid-on-latin"
            else:
                # Macca / PABDT: all letters are Cyrillic look-alikes -> exact TM entry only
                cy = S.lookalike_cyr(d["text"], per_word=True)
                if not cy:
                    continue
                key, nums, _, _ = S.make_key(cy)
                hit = tm.lookup(key, "tb" if in_tb(d["box"], tb) else "body", fuzzy=False)
                if hit is None or hit.action != "translate" or nums:
                    continue
                zh, _ = render(hit, nums, [], key)
                if zh and CJK.search(zh) and not COUNT_ZH.match(zh):
                    lines[str(rid)] = zh
                    meta[str(rid)] = dict(conf="low", src=f"{hit.layer}:{hit.src}", key=key,
                                          why="Latin look-alike line read as Cyrillic")
                continue
        k = str(rid)
        if rid in heads:
            lines[k] = APPX_ZH; meta[k] = dict(conf="high", src="notes", why="notes heading"); continue
        if rid in notes_lines:
            lines[k] = ""; meta[k] = dict(conf="high", src="notes", why="notes clause"); continue
        if S.count_only(text):
            lines[k] = ""; meta[k] = dict(conf="high", src="engine", why="count label (engine)"); continue
        if _in_box(d["box"], stamp):
            lines[k] = ""; meta[k] = dict(conf="high", src="engine", why="approval stamp (stamp_zh)"); continue
        others = S.rapid_over(P, d["box"]) if src == "tess" else S.words_over(P, d["box"], 0.3) + [d["text"]]
        txt, ok = S.reconcile(text, confirm_pool(text, others))
        key, nums, _, _ = S.make_key(txt)
        hit = tm.lookup(key, "tb" if in_tb(d["box"], tb) else "body")
        if hit is None:
            lines[k] = ""; meta[k] = dict(conf="miss", src="none", key=key, why="no TM entry"); continue
        zh, dropped = render(hit, nums, ok, hit.key if hit.fuzzy else key)
        why = []
        if hit.fuzzy:
            why.append("fuzzy TM hit")
        if hit.conf == "low":
            why.append("TM conf low")
        if dropped:
            why.append(f"digit guard dropped {dropped}" + (" (digits in TM value)" if hit.bare else ""))
        if zh and COUNT_ZH.match(zh):
            zh = ""; why.append("count label (engine)")
        if zh and not CJK.search(zh):
            zh = ""; why.append("no Chinese")
        lines[k] = zh
        meta[k] = dict(conf="low" if (hit.fuzzy or hit.conf == "low" or dropped) else "high",
                       src=f"{hit.layer}:{hit.src}", action=hit.action, key=key, why="; ".join(why))
    # Russian only RapidOCR found (no Tesseract line on it) -> extra with the exact box
    for r in P.rapid:
        t = r["text"]
        if S.cyr_count(MARK.sub("", t)) < 2 or S.count_only(t):
            continue
        if any(S.ov(r["box"], d["box"]) >= 0.3 or S.ov(d["box"], r["box"]) >= 0.5 for d in P.lines):
            continue
        if any(S.ov(r["box"], g["region"]["box"]) >= 0.5 for g in used) or _in_box(r["box"], stamp):
            continue
        txt, ok = S.reconcile(t, confirm_pool(t, [" ".join(S.words_over(P, r["box"], 0.3))]))
        key, nums, _, _ = S.make_key(txt)
        hit = tm.lookup(key, "tb" if in_tb(r["box"], tb) else "body")
        if hit is None:
            continue
        zh, dropped = render(hit, nums, ok, hit.key if hit.fuzzy else key)
        if not zh or COUNT_ZH.match(zh) or not CJK.search(zh):
            continue
        extra.append(dict(box=[round(v, 4) for v in r["box"]], zh=zh, ru=t, src="automap"))
        why = (["fuzzy TM hit"] if hit.fuzzy else []) + ([f"digit guard dropped {dropped}"] if dropped else [])
        xmeta.append(dict(conf="low" if (hit.fuzzy or hit.conf == "low" or dropped) else "high",
                          src=f"{hit.layer}:{hit.src}", key=key, why="; ".join(why)))
    if known:
        for k, z in list(lines.items()):
            z2, nf, nu = fix_marks(z, known)
            if nf:
                lines[k] = z2
                meta.setdefault(k, {}).update(conf="low", why=(meta.get(k, {}).get("why", "") + "; member mark corrected").strip("; "))
        for e, xm in zip(extra, xmeta):
            z2, nf, nu = fix_marks(e["zh"], known)
            if nf:
                e["zh"] = z2; xm.update(conf="low", why=(xm.get("why", "") + "; member mark corrected").strip("; "))
    m = {"lines": lines, "extra": extra, "appendix": bool(used)}
    low_l = [k for k, v in meta.items() if v["conf"] == "low"]
    meta_j = {"lines": meta, "extra": xmeta, "low_lines": low_l,
              "low_extra": [i for i, v in enumerate(xmeta) if v["conf"] == "low"],
              "stats": dict(lines=len(lines), translated=sum(1 for v in lines.values() if v and v != APPX_ZH),
                            miss=sum(1 for v in meta.values() if v["conf"] == "miss"), low=len(low_l),
                            extra=len(extra), notes_regions=len(used))}
    return m, meta_j, notes_json


def write(ctx, tm, notes_results: dict | None = None, pages=None) -> dict:
    """Write work/map/pNN.json (+ .meta.json) and work/notes/pNN.json for the volume.
    ``notes_results``: {page: page_notes output dict}; pages without one keep their notes file."""
    notes_results = notes_results or {}
    tot = {}
    (ctx.w / "map").mkdir(parents=True, exist_ok=True); (ctx.w / "notes").mkdir(parents=True, exist_ok=True)
    known = known_marks(ctx, notes_results)
    for pg in pages or S.pages_of(ctx):
        res = notes_results.get(pg)
        if res is None and ctx.notes(pg).exists():
            res = notes_from_file(ctx, pg)
        m, meta, nj = map_page(ctx, pg, tm, res, known)
        ctx.map(pg).write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        ctx.f("map", pg, ".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        if nj is not None and notes_results.get(pg) is not None:
            ctx.notes(pg).write_text(json.dumps(nj, ensure_ascii=False, indent=1), encoding="utf-8")
        for k, v in meta["stats"].items():
            tot[k] = tot.get(k, 0) + v
    return tot


def notes_from_file(ctx, pg):
    """An existing notes/pNN.json read back as a page_notes answer (for re-runs)."""
    j = json.loads(ctx.notes(pg).read_text(encoding="utf-8"))
    notes = [i for b in j.get("blocks", []) if b.get("kind") == "notes" for i in (b.get("items") or [])]
    labels = [i for b in j.get("blocks", []) if b.get("kind") == "pairs" for i in (b.get("items") or [])]
    return dict(sheet_code=j.get("sheet_code"), format=j.get("format"), title_ru=j.get("title_ru"),
                title_zh=j.get("title_zh"), notes=notes, labels=labels, issues=j.get("issues") or [])
