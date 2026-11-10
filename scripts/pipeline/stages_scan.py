"""Scan-route stage registry (ARCH.md):

ocr -> rapid -> segments -> tm -> translate[LLM] -> notes[LLM] -> automap -> build -> audit
    -> audit_review[LLM, only if remaining>0, <=2 loops] -> check -> qa -> visual[LLM, flagged tiles] -> report

Local steps run the existing scripts/scan/NN_*.py files in a subprocess (same python) and parse
their output; their file names start with digits, so they are not importable as modules.
LLM/TM/automap stages import scripts/llm and scripts/scan/{segments,tmstore,packets,automap}.py lazily.
"""
from __future__ import annotations
import importlib.util
import json
import math
import re
import shutil
from pathlib import Path

from .core import Stage, StageCtx, StageError, fail_on_bad, lazy, summarize_results


def _g(job, sub, pat="*"):
    return str(job.ctx.w / sub / pat)


def _jf(job, name):
    return job.job_dir / name


def _out_pdf(job):
    return Path(job.cfg.scan.output) / f"{job.cfg.scan.code}_中文对照版.pdf"


def _rangestr(pages):
    return ",".join(str(p) for p in sorted(pages))


# ------------------------------------------------------------------ local scan steps
def run_ocr(sc: StageCtx):
    sc.job.ctx.mkdirs()
    rc, out = sc.run_script("scan/10_ocr.py", sc.config_arg() + sc.pages_args(), check=True)
    bad = [ln for ln in out.splitlines() if "no TSV" in ln]
    if bad:
        raise StageError("; ".join(bad[:3]))
    m = re.findall(r"p(\d+): (\d+) lines, (\d+) Russian", out)
    return {"pages": len(m), "lines": sum(int(x[1]) for x in m), "russian_lines": sum(int(x[2]) for x in m)}


def run_rapid(sc: StageCtx):
    if importlib.util.find_spec("rapidocr") is None or importlib.util.find_spec("onnxruntime") is None:
        return {"_skip": "rapidocr/onnxruntime not installed (pip install rapidocr onnxruntime); the audit then only sees Tesseract"}
    sc.run_script("scan/11_rapid.py", sc.config_arg() + sc.pages_args(), check=True)
    n = len(list(sc.job.ctx.w.joinpath("rapid").glob("p*.tsv")))
    return {"pages_with_rapid": n}


def _seg_file(job):
    return job.ctx.w / "seg" / "segments.json"


def run_segments(sc: StageCtx):
    seg = lazy("scan.segments", "segments")
    res = seg.collect(sc.job.ctx)
    st = dict(getattr(res, "stats", {}) or {})
    out = {"segments": len(res.segs), **{k: v for k, v in st.items() if isinstance(v, (int, float))}}
    _jf(sc.job, "segments.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def _tm_store(sc):
    tms = lazy("scan.tmstore", sc.stage.name)
    scan = sc.job.ctx.cfg
    return tms.TMStore(sc.cfg.tm_cfg(), root=sc.cfg.root, names=list(scan.names or []),
                       skip_regex=scan.skip_regex or "")


def _segments(sc):
    seg = lazy("scan.segments", sc.stage.name)
    f = _seg_file(sc.job)
    return seg.Segments.load(f) if f.exists() else seg.collect(sc.job.ctx)


def run_tm(sc: StageCtx):
    segs = _segments(sc)
    store = _tm_store(sc)
    misses = list(store.misses(segs) or [])
    store.save()                                    # rule-based keep/skip entries found by misses()
    sc.job.job_dir.mkdir(parents=True, exist_ok=True)
    _jf(sc.job, "tm_misses.json").write_text(json.dumps(misses, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"misses": len(misses), "segments": len(segs.segs), "tm_hits": len(segs.segs) - len(misses)}


def run_translate(sc: StageCtx):
    fn = _jf(sc.job, "tm_misses.json")
    if not fn.exists():
        raise StageError("tm_misses.json missing: run the tm stage first")
    misses = json.loads(fn.read_text(encoding="utf-8"))
    if not misses:
        return {"misses": 0, "calls": 0}
    store = _tm_store(sc)
    still = [it for it in misses if not store.lookup(it["key"], fuzzy=False)]     # resume: already stored
    if not still:
        return {"misses": len(misses), "calls": 0, "already_in_tm": len(misses)}
    payloads, ids = store.batches(still, int(sc.cfg.job["batch_size"]), "")       # background: system prompt
    sess = sc.session()
    results = sess.run("phrase_translate", payloads, ids)
    vmod = lazy("llm.validate", "translate")
    salvage = []
    for r in results:                          # keep the good items of a batch that failed on a few ids
        if not r.ok and r.output and isinstance(r.output, dict):
            bad = vmod.bad_ids(r.errors)
            if bad is not None:
                salvage.append({"items": [a for a in r.output.get("items") or [] if str(a.get("id")) not in bad]})
    model = sess.model_for("phrase_translate")
    errs = store.ingest([r for r in results if r.ok] + salvage, model=model, items=still)
    store.save()
    left = [it for it in still if not store.lookup(it["key"], fuzzy=False)]
    m = {"misses": len(misses), "asked": len(still), "stored": len(still) - len(left), "unresolved": len(left),
         "ingest_errors": len(errs), **summarize_results(results)}
    for e in errs[:20]:
        sc.log("ingest: " + e)
    sc.log(f"translate: {m}")
    if left and len(left) > 0.1 * len(still):
        raise StageError(f"{len(left)} of {len(still)} phrases unresolved after retries; re-run translate")
    return m


def run_notes(sc: StageCtx):
    packets = lazy("scan.packets", "notes")
    store = _tm_store(sc)
    pages, payloads, ids = [], [], []
    for pg in sc.page_list():
        p = packets.page_notes(sc.job.ctx, pg, tm=store)
        if p is None:
            continue
        pages.append(pg); payloads.append(p); ids.append(f"p{pg:02d}")
    fn = _jf(sc.job, "notes_results.json")
    data = json.loads(fn.read_text(encoding="utf-8")) if fn.exists() else {}
    results = sc.session().run("page_notes", payloads, ids) if payloads else []
    for pg, r in zip(pages, results):
        if r.ok and r.output is not None:
            data[str(pg)] = r.output
    sc.job.job_dir.mkdir(parents=True, exist_ok=True)
    fn.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    m = {"pages": len(pages), "have": len(data), **summarize_results(results)}
    fail_on_bad(results, "page_notes")
    return m


def run_automap(sc: StageCtx):
    am = lazy("scan.automap", "automap")
    fn = _jf(sc.job, "notes_results.json")
    notes = {int(k): v for k, v in json.loads(fn.read_text(encoding="utf-8")).items()} if fn.exists() else {}
    ret = am.write(sc.job.ctx, _tm_store(sc), notes)
    return dict(ret) if isinstance(ret, dict) else {"maps": len(list(sc.job.ctx.w.joinpath("map").glob("p??.json")))}


def _build(sc: StageCtx) -> dict:
    sc.run_script("scan/20_build.py", sc.config_arg() + sc.pages_args(), check=True)
    rep = sc.job.ctx.w / "review" / ("build_report_test.tsv" if sc.pages else "build_report.tsv")
    rows = [ln.split("\t") for ln in rep.read_text(encoding="utf-8").splitlines()] if rep.exists() else []
    unplaced = sum(1 for r in rows if len(r) > 2 and r[2] == "未放置")
    return {"unplaced": unplaced, "report_rows": len(rows), "pdf": str(_out_pdf(sc.job)) if not sc.pages else None}


def _audit(sc: StageCtx) -> dict:
    rc, out = sc.run_script("scan/30_audit.py", sc.config_arg() + sc.pages_args())
    m = re.search(r"remaining (\d+)\s+pages\s+\[([^\]]*)\]", out)
    if rc or not m:
        raise StageError(f"30_audit.py failed (exit {rc}, no 'remaining N' line); see log")
    pages = [int(x) for x in re.findall(r"\d+", m.group(2))]
    return {"remaining": int(m.group(1)), "pages": pages, "second_detector": "no work/rapid" not in out}


def _record(job, name: str, metrics: dict):
    """Update metrics of a stage another stage re-ran internally and keep its hashes consistent."""
    job.state.set(name, metrics=metrics)
    job.settle(name)


def run_build(sc):
    return _build(sc)


def run_audit(sc):
    return _audit(sc)


def run_audit_review(sc: StageCtx):
    job, ctx = sc.job, sc.job.ctx
    rem = job.metrics("audit").get("remaining")
    if rem is None:
        raise StageError("audit has no result yet: run the audit stage first")
    if rem == 0:
        return {"_skip": "audit remaining 0", "remaining": 0}
    tot = {"loops": 0, "calls": 0, "cached": 0, "cost_usd": 0.0, "apply_problems": 0}
    for loop in range(1, int(sc.cfg.job["max_audit_loops"]) + 1):
        am = job.metrics("audit")
        pages = am.get("pages") or []
        if not am.get("remaining"):
            break
        payloads, ids, pgs = [], [], []
        for pg in pages:
            md = ctx.w / "packets" / f"p{pg:02d}.md"
            cj = ctx.w / "packets" / f"p{pg:02d}.cands.json"
            if not (md.exists() and cj.exists()):
                continue
            cands = json.loads(cj.read_text(encoding="utf-8")).get("cands", {})
            imgs = sorted((ctx.w / "packets").glob(f"p{pg:02d}_s*.png"))
            # no absolute paths in the prompt: they would change the cache key between job folders
            pkt = re.sub(r"(?:[A-Za-z]:)?[\\/][^\s「」]*[\\/]([^\\/\s]+\.png)", r"\1", md.read_text(encoding="utf-8"))
            payloads.append({"data": {"page": pg, "packet": pkt, "refs": list(cands)},
                             "images": imgs})
            ids.append(f"p{pg:02d}-L{loop}"); pgs.append(pg)
        if not payloads:
            raise StageError("audit reports remaining items but no packets exist (packets/pNN.md)")
        sc.log(f"audit_review loop {loop}: {len(pgs)} page(s)")
        results = sc.session().run("audit_review", payloads, ids)
        adir = ctx.w / "answers" / "audit"
        adir.mkdir(parents=True, exist_ok=True)
        stale = adir / "_stale"
        for old in adir.glob("p*.json"):                       # answers of pages that no longer have candidates
            if int(old.stem[1:]) not in pgs:
                stale.mkdir(exist_ok=True)
                shutil.move(str(old), str(stale / old.name))
        human = []
        for pg, r in zip(pgs, results):
            if r.ok and r.output is not None:
                out = json.loads(json.dumps(r.output))
                for it in out.get("items") or []:     # "[?]" = reviewer could not read it: a person decides,
                    if it.get("act") == "tr" and "[?]" in (it.get("zh") or ""):   # nothing is drawn
                        human.append("\t".join([f"p{pg:02d}", it.get("ref", ""), it.get("ru", ""), it.get("why", "")]))
                        it.update(act="skip", zh="", why="待人工核对: " + (it.get("why") or ""))
                (adir / f"p{pg:02d}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        if human:
            with open(_jf(job, "human_todo.tsv"), "a", encoding="utf-8") as fh:
                fh.write("".join(h + "\n" for h in human))
            tot["human_todo"] = tot.get("human_todo", 0) + len(human)
        s = summarize_results(results)
        for k in ("calls", "cached", "cost_usd"):
            tot[k] = round(tot[k] + s[k], 4)
        tot["loops"] = loop
        if not any(r.ok for r in results):
            fail_on_bad(results, "audit_review")
        for r in results:
            if not r.ok:
                sc.log(f"audit_review {r.id} not applied: {r.errors[:2]}")
        tot["failed"] = tot.get("failed", 0) + s["failed"]
        rc, _ = sc.run_script("scan/31_apply_audit.py", sc.config_arg())
        tot["apply_problems"] += int(rc != 0)
        _record(job, "build", _build(sc))
        _record(job, "audit", _audit(sc))
    tot["remaining"] = job.metrics("audit").get("remaining")
    return tot


def run_check(sc: StageCtx):
    rc, out = sc.run_script("scan/50_check.py", sc.config_arg())
    m = re.search(r"mark problems: (\d+)\s+digit notes: (\d+)", out)
    if rc not in (0, 1) or not m:
        raise StageError(f"50_check.py failed (exit {rc}); see log")
    return {"mark_problems": int(m.group(1)), "digit_notes": int(m.group(2)), "gate_ok": rc == 0}


def run_qa(sc: StageCtx):
    """Metrics of the output PDF + occlusion detector (tiny font, Chinese on ink / across rules).
    Flagged tiles -> job/qa_tiles.json (input of the visual stage)."""
    if not sc.cfg.job["qa"]:
        return {"_skip": "[job] qa = false"}
    lazy("qa", "qa")
    from qa import metrics as QM, occlusion as QO
    out_pdf = _out_pdf(sc.job)
    if not out_pdf.exists():
        raise StageError(f"{out_pdf} missing: build first")
    src = sc.job.ctx.cfg.pdf
    rows, flagged = QO.run(str(out_pdf), str(src))
    tiles = QO.tiles(flagged)
    m = QM.compute(str(out_pdf), work=sc.job.ctx.w, config=None, src=None,
                   count_word=sc.job.ctx.cfg.count_word, run_check=False)
    m.update({"occ_" + k: v for k, v in QO.summary(rows, flagged).items()})
    m["occ_tiles"] = len(tiles)
    _jf(sc.job, "qa_tiles.json").write_text(json.dumps(tiles, ensure_ascii=False, indent=1), encoding="utf-8")
    _jf(sc.job, "metrics.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    flag_pages = sorted({t["src_page"] for t in tiles})
    return {"qa_flags": len(flagged), "flag_pages": flag_pages, "tiles": len(tiles),
            **{f"m_{k}": v for k, v in m.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}}


def run_visual(sc: StageCtx):
    """LLM look at the QA-flagged tiles only (not every page).  Findings -> work/visual/qa/_todo.tsv for
    a human (遮挡 / 错译) — report-only; 漏译 is counted as a gate warning."""
    job, ctx = sc.job, sc.job.ctx
    if not sc.cfg.job["visual"]:
        return {"_skip": "[job] visual = false"}
    tf = _jf(job, "qa_tiles.json")
    tiles = json.loads(tf.read_text(encoding="utf-8")) if tf.exists() else []
    cap = int(sc.cfg.job.get("visual_max_tiles", 60) or 60)
    tiles = sorted(tiles, key=lambda t: (-len(t["flags"]), -t.get("n", 1), t["src_page"]))[:cap]
    if not tiles:
        return {"_skip": "no tiles flagged by qa"}
    import pymupdf
    from scan import ctx as C
    out = pymupdf.open(str(_out_pdf(job)))
    vdir = ctx.w / "visual" / "qa"
    vdir.mkdir(parents=True, exist_ok=True)
    by_pg = {}
    for t in tiles:
        by_pg.setdefault(t["src_page"], []).append(t)
    payloads, ids = [], []
    for spg, ts in sorted(by_pg.items()):
        page = out[ts[0]["page"] - 1]
        PW, PH = page.rect.width, page.rect.height
        rapid = C.load_rapid(ctx, spg, PW, PH)
        items, imgs = [], []
        for k, t in enumerate(ts, 1):
            x0, y0, x1, y1 = t["bbox"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            w, h = max(x1 - x0 + 40, 160), max(y1 - y0 + 30, 70)
            r = pymupdf.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2) & page.rect
            fn = vdir / f"p{spg:02d}_t{k}.png"
            zoom = min(200 / 72, 1400 / max(r.width, 1))
            page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=r).save(fn)
            ocr = [txt for bb, txt in rapid if bb[0] < r.x1 and bb[2] > r.x0 and bb[1] < r.y1 and bb[3] > r.y0]
            items.append({"id": f"t{k}", "ocr": ocr[:30], "flag": ",".join(t["flags"]), "_rect": [r.x0, r.y0, r.x1, r.y1]})
            imgs.append(fn)
        payloads.append({"data": {"page": spg, "tiles": [{k: v for k, v in i.items() if k != "_rect"} for i in items],
                                  "count_word": ctx.cfg.count_word}, "images": imgs})
        ids.append(f"v{spg:02d}")
    results = sc.session().run("visual_review", payloads, ids)
    rows, kinds = [], {}
    for (spg, _), r in zip(sorted(by_pg.items()), results):
        if not (r.ok and r.output):
            continue
        (vdir / f"p{spg:02d}.json").write_text(json.dumps(r.output, ensure_ascii=False, indent=1), encoding="utf-8")
        for it in r.output.get("issues") or []:
            kinds[it["type"]] = kinds.get(it["type"], 0) + 1
            rows.append("\t".join([f"{ctx.cfg.code}_p{spg:02d}", it["type"], it["text"], it.get("zh", ""), it["tile"]]))
    (vdir / "_todo.tsv").write_text("".join(r + "\n" for r in rows), encoding="utf-8")
    m = {"tiles": len(tiles), "pages": len(by_pg), "issues": len(rows), **{f"n_{k}": v for k, v in kinds.items()},
         **summarize_results(results)}
    fail_on_bad(results, "visual_review")
    return m


def run_report(sc: StageCtx):
    from . import summary
    return summary.write(sc.job)


# ------------------------------------------------------------------ estimates
def _est_translate(job):
    m = job.metrics("tm").get("misses")
    if m is None:
        return 0, "unknown until tm has run"
    return math.ceil(m / int(job.cfg.job["batch_size"])), f"{m} phrases / {job.cfg.job['batch_size']} per call"


def _est_notes(job):
    n = job.n_pages
    return n, f"1 call per page ({n} pages), cached replays are free"


def _est_audit(job):
    am = job.metrics("audit")
    if am.get("remaining") is None:
        return 0, f"unknown until audit has run (<= pages with remaining x {job.cfg.job['max_audit_loops']} loops)"
    return len(am.get("pages") or []) * int(job.cfg.job["max_audit_loops"]) if am["remaining"] else 0, "upper bound"


def _est_visual(job):
    if not job.cfg.job["visual"]:
        return 0, "disabled ([job] visual = false)"
    fp = job.metrics("qa").get("flag_pages")
    return (len(fp), "flagged pages") if fp is not None else (0, "unknown until qa has run")


_W = lambda sub, pat="*": (lambda job: [_g(job, sub, pat)])        # noqa: E731
_MAPS = lambda job: [_g(job, "notes", "*.json"), _g(job, "map", "*.json"), _g(job, "lines", "*.tsv")]   # noqa: E731

STAGES = [
    Stage("ocr", "local", run_ocr, "render 300 dpi + Tesseract (10_ocr.py)", cfg_keys=("scan.lang", "scan.tessdata"),
          pages_aware=True, outputs=lambda j: [_g(j, "ocr", "*.tsv"), _g(j, "lines", "*.tsv")]),
    Stage("rapid", "local", run_rapid, "RapidOCR second detector (11_rapid.py)", deps=("ocr",), pages_aware=True,
          optional=True, outputs=_W("rapid", "*.tsv")),
    Stage("segments", "local", run_segments, "collect unique phrases (scan/segments.py)", deps=("ocr", "rapid"),
          cfg_keys=("scan.names", "scan.skip_regex", "keep"), outputs=lambda j: [str(_jf(j, "segments.json"))]),
    Stage("tm", "tm", run_tm, "layered TM lookup -> misses (scan/tmstore.py)", deps=("segments",),
          cfg_keys=("tm",), outputs=lambda j: [str(_jf(j, "tm_misses.json"))]),
    Stage("translate", "llm", run_translate, "LLM phrase_translate on TM misses", deps=("tm",),
          cfg_keys=("llm.models", "llm.background", "llm.backend", "tm", "job.batch_size"),
          outputs=lambda j: [j.cfg.tm_cfg()["project"]], est=_est_translate),
    Stage("notes", "llm", run_notes, "LLM page_notes (title block + notes crops)", deps=("ocr",), pages_aware=True,
          cfg_keys=("llm.models", "llm.background", "llm.backend"),
          outputs=lambda j: [str(_jf(j, "notes_results.json"))], est=_est_notes),
    Stage("automap", "local", run_automap, "deterministic line->Chinese map + notes (scan/automap.py)",
          deps=("translate", "notes", "rapid"), cfg_keys=("scan.count_word", "scan.names", "tm"),
          outputs=lambda j: [_g(j, "map", "p??.json"), _g(j, "notes", "p??.json")]),
    Stage("build", "local", run_build, "place Chinese, notes, legend, TOC (20_build.py)", deps=("automap",),
          cfg_keys=("scan", "keep"), pages_aware=True, inputs=_MAPS,
          outputs=lambda j: [str(Path(j.cfg.scan.output) / "*.pdf"), _g(j, "review", "build_report*.tsv")]),
    Stage("audit", "local", run_audit, "coverage audit + packets (30_audit.py)", deps=("build",),
          cfg_keys=("scan", "keep"), pages_aware=True, inputs=lambda j: [_g(j, "rapid", "*.tsv")] + _MAPS(j),
          outputs=lambda j: [_g(j, "audit", "*.json"), _g(j, "packets", "*")]),
    Stage("audit_review", "llm", run_audit_review, "LLM audit_review of packets, apply, rebuild (<=2 loops)",
          deps=("audit",), cfg_keys=("llm.models", "llm.backend", "job.max_audit_loops"), settle=True,
          outputs=lambda j: [_g(j, "map", "*.fix.json"), _g(j, "answers/audit", "*.json")], est=_est_audit),
    Stage("check", "local", run_check, "member-mark gate (50_check.py)", deps=("audit_review",), cfg_keys=("scan",),
          inputs=_MAPS, outputs=lambda j: []),
    Stage("qa", "local", run_qa, "metrics / occlusion flags (scripts/qa)", deps=("build",), cfg_keys=("job.qa",),
          optional=True, always=True),
    Stage("visual", "llm", run_visual, "LLM visual_review of flagged tiles (optional)", deps=("qa", "audit_review"),
          cfg_keys=("llm.models", "llm.backend", "job.visual"), optional=True, est=_est_visual,
          outputs=lambda j: [_g(j, "visual/qa", "*")]),
    Stage("report", "local", run_report, "run_summary.json + cost report", always=True,
          deps=("check", "qa", "visual")),
]
