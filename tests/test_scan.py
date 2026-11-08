"""Scanned-PDF route, end to end without Tesseract: the synthetic demo sheet is rasterised into
an image-only PDF, and the "OCR" files are generated from the demo's own text layer.  Checks:
every mapped line gets its Chinese, шт./pcs labels are attached, a line the map wrongly left
blank is caught by the coverage audit and cleared by a review answer, and the mark gate fails
on a dropped decimal point (Фм11 for Фм1.1)."""
import importlib.util
import json
import subprocess
import sys

import pymupdf
import pytest
from conftest import ROOT

SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
from scan import ctx as C, engine  # noqa: E402


def load_step(name):
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), SCRIPTS / "scan" / name)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


TSV_HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
EXTRA_WORD = ((900.0, 300.0, 935.0, 309.0), "шт./pcs.4")   # a count label in free space


@pytest.fixture(scope="module")
def proj(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("scanproj")
    demo = pymupdf.open(ROOT / "demo" / "demo_drawing.pdf")
    src = demo[0]
    scan = pymupdf.open()
    pg = scan.new_page(width=src.rect.width, height=src.rect.height)
    pg.insert_image(pg.rect, pixmap=src.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY))
    scan.save(tmp / "scan.pdf")
    (tmp / "project.toml").write_text(
        '[project]\nname = "demo scan"\n[scan]\npdf = "scan.pdf"\ncode = "DEMO"\nwork = "work"\noutput = "out"\n'
        'toc_title_ru = "Фундамент Фм1"\ntoc_title_zh = "基础 Фм1"\n[keep]\nnames = ["Иванов", "Петров"]\n',
        encoding="utf-8")
    cfg = C.load(tmp / "project.toml"); ctx = C.Ctx(cfg); ctx.mkdirs()
    k = 300 / 72
    rows, rapid = [TSV_HEAD], []
    words = src.get_text("words") + [(*EXTRA_WORD[0], EXTRA_WORD[1], 999, 0, 0)]
    for i, w in enumerate(words):
        x0, y0, x1, y1, t, b, ln = w[0], w[1], w[2], w[3], w[4], w[5], w[6]
        rows.append(f"5\t1\t{b}\t1\t{ln}\t{i}\t{round(x0*k)}\t{round(y0*k)}\t{round((x1-x0)*k)}\t{round((y1-y0)*k)}\t95\t{t}\n")
    ctx.ocr(1).write_text("".join(rows), encoding="utf-8")
    lines = C.build_lines(ctx.ocr(1))
    C.write_lines(ctx.lines(1), lines, src.rect.width, src.rect.height)
    Wd, Ht = round(src.rect.width * k), round(src.rect.height * k)
    with open(ctx.rapid(1), "w", encoding="utf-8") as fh:      # second detector = the same lines
        for d in lines:
            fh.write(f"{round(d['x0']*k)}\t{round(d['y0']*k)}\t{round(d['x1']*k)}\t{round(d['y1']*k)}\t{d['text']}\t0.99\t0\t{Wd}\t{Ht}\n")
    ru = [d for d in lines if len(C.CYR.findall(d["text"])) >= 3 and "pcs" not in d["text"]]
    blank = ru[3]                                             # pretend the translator wrongly blanked it
    mp = {str(d["id"]): ("" if d is blank else f"译{d['id']}") for d in ru}
    ctx.map(1).write_text(json.dumps({"lines": mp}, ensure_ascii=False), encoding="utf-8")
    ctx.notes(1).write_text(json.dumps({"sheet_code": "DEMO-1", "title_ru": "Фундамент Фм1.1",
                                        "title_zh": "基础 Фм1.1", "format": "А3"}, ensure_ascii=False), encoding="utf-8")
    engine.init(ctx)
    return dict(tmp=tmp, cfg=cfg, ctx=ctx, ru=ru, blank=blank)


def test_build_places_every_mapped_line(proj):
    build = load_step("20_build.py")
    out = proj["tmp"] / "out" / "t.pdf"
    rep = build.build(proj["ctx"], [1], out, verbose=False)
    assert not [r for r in rep if r[2] == "未放置"], rep
    text = pymupdf.open(out)[0].get_text()
    for d in proj["ru"]:
        if d is not proj["blank"]:
            assert f"译{d['id']}" in text, d["text"]
    assert "根" in text                                       # шт./pcs.4 got its label
    assert "шт./pcs.=根数" in text.replace("\n", "")          # ... and the abbreviation key


def test_toc_pages_and_links(proj):
    build = load_step("20_build.py")
    body = proj["tmp"] / "out" / "body.pdf"
    build.build(proj["ctx"], [1], body, verbose=False)
    final = proj["tmp"] / "out" / "final.pdf"
    k = build.add_toc(proj["ctx"], body, final)
    d = pymupdf.open(final)
    assert d.page_count == k + 1 and d[0].get_links()[0]["page"] == k


def test_audit_catches_blank_line_until_answered(proj):
    audit = load_step("30_audit.py")
    ctx = proj["ctx"]
    res = audit.audit(ctx, 1)
    hits = [r for r in res["blank_claimed"] + res["residual"] if proj["blank"]["text"][:8] in r["text"]]
    assert hits, res
    from PIL import ImageFont
    n = audit.packet(ctx, 1, res, audit.make_auto_skip(ctx.cfg), ImageFont.load_default())
    assert n >= 1
    snap = json.loads((ctx.w / "packets" / "p01.cands.json").read_text(encoding="utf-8"))
    ans = []
    for ref, c in snap["cands"].items():
        if proj["blank"]["text"][:8] in c["text"]:
            ans.append({"ref": ref, "act": "tr", "ru": c["text"], "zh": "补译"})
        else:
            ans.append({"ref": ref, "act": "ok", "why": "test"})
    (ctx.w / "answers" / "audit").mkdir(parents=True, exist_ok=True)
    (ctx.w / "answers" / "audit" / "p01.json").write_text(json.dumps({"items": ans}, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPTS / "scan" / "31_apply_audit.py"), "--config",
                        str(proj["tmp"] / "project.toml")], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    res2 = audit.audit(ctx, 1)
    assert not res2["residual"] and not res2["blank_claimed"] and not res2["lost"], res2


def test_mark_gate(proj):
    ctx = proj["ctx"]
    m = json.loads(ctx.map(1).read_text(encoding="utf-8"))
    key = next(k for k, v in m["lines"].items() if v)
    run = lambda: subprocess.run([sys.executable, str(SCRIPTS / "scan" / "50_check.py"), "--config",  # noqa: E731
                                  str(proj["tmp"] / "project.toml")], capture_output=True, text=True, encoding="utf-8")
    good = dict(m); good["lines"] = dict(m["lines"], **{key: "基础 Фм1.1"})
    ctx.map(1).write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
    assert run().returncode == 0
    bad = dict(m); bad["lines"] = dict(m["lines"], **{key: "基础 Фм11"})     # OCR dropped the dot
    ctx.map(1).write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    r = run()
    ctx.map(1).write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    assert r.returncode == 1 and "Фм11" in r.stdout


def test_wrap_never_splits_codes(proj):
    for width in (40, 60, 80, 120):
        lines = engine.wrap("B25 级混凝土,抗渗 W8,抗冻 F200,钢筋 А500С Ø16 见 Фм1.1", 6, width)
        joined = "|".join(lines)
        for code in ("B25", "F200", "А500С", "Ø16", "Фм1.1"):
            assert code in joined.replace("|", "") and not any(code not in ln and code[:2] in ln[-2:] for ln in lines)
