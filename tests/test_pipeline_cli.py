"""rudraw orchestration: job state / resume, --from/--only, cost report, doctor, init.  Synthetic only."""
import json
import subprocess
import sys

import pymupdf
import pytest
from conftest import ROOT

from pipeline import cli, cost, doctor, runner, summary  # noqa: E402  (conftest puts scripts/ on sys.path)
from pipeline.config import JobConfig
from pipeline.core import Job, Stage, StageError, StageUnavailable

DEMO_PDF = ROOT / "demo" / "demo_drawing.pdf"


def make_job(tmp_path, stages):
    (tmp_path / "project.toml").write_text('[project]\nname="t"\n[scan]\ncode="T"\nwork="work"\n', encoding="utf-8")
    job = Job(JobConfig.load(tmp_path / "project.toml"), out=lambda *_: None)
    job.stages = stages
    return job


def chain(tmp_path, calls, fail=None):
    """a -> b -> c; a writes in/x.txt-derived file, b copies it, c summarises.  `calls` records executions."""
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    if not (d / "in.txt").exists():
        (d / "in.txt").write_text("one", encoding="utf-8")

    def a(sc):
        calls.append("a"); (d / "a.out").write_text((d / "in.txt").read_text() + "!", encoding="utf-8"); return {"n": 1}

    def b(sc):
        calls.append("b")
        if fail == "b":
            raise StageError("boom")
        (d / "b.out").write_text((d / "a.out").read_text(), encoding="utf-8"); return {}

    def c(sc):
        calls.append("c"); (d / "c.out").write_text("c", encoding="utf-8"); return {"_skip": "nothing to do"} if fail == "skipc" else {}

    return [
        Stage("a", "local", a, inputs=lambda j: [str(d / "in.txt")], outputs=lambda j: [str(d / "a.out")]),
        Stage("b", "local", b, deps=("a",), outputs=lambda j: [str(d / "b.out")]),
        Stage("c", "local", c, deps=("b",), outputs=lambda j: [str(d / "c.out")]),
    ]


def run(job, **kw):
    return runner.run_job(job, echo=False, **kw)


def test_resume_skips_done_stages(tmp_path):
    calls = []
    job = make_job(tmp_path, chain(tmp_path, calls))
    assert run(job) == 0 and calls == ["a", "b", "c"]
    assert json.loads((job.job_dir / "state.json").read_text())["stages"]["a"]["status"] == "done"
    assert (job.log_dir / "a.log").exists()
    calls.clear()
    job2 = make_job(tmp_path, chain(tmp_path, calls))      # fresh process: state comes from disk
    assert run(job2) == 0 and calls == []


def test_input_change_reruns_downstream_only_when_outputs_change(tmp_path):
    calls = []
    job = make_job(tmp_path, chain(tmp_path, calls))
    run(job); calls.clear()
    (tmp_path / "data" / "in.txt").write_text("two", encoding="utf-8")
    run(job)
    assert calls == ["a", "b", "c"]
    calls.clear()
    (tmp_path / "data" / "in.txt").write_text("two", encoding="utf-8")   # same content, new mtime -> a reruns
    import os, time
    os.utime(tmp_path / "data" / "in.txt", ns=(time.time_ns() + 10 ** 9,) * 2)
    run(job)
    assert calls[0] == "a"


def test_deleted_output_reruns_stage_and_force(tmp_path):
    calls = []
    job = make_job(tmp_path, chain(tmp_path, calls))
    run(job); calls.clear()
    (tmp_path / "data" / "b.out").unlink()
    run(job)
    assert calls[0] == "b"
    calls.clear()
    run(job, force=True, only="a")
    assert calls == ["a"]


def test_failure_stops_run_and_resume_retries_only_failed(tmp_path):
    calls = []
    job = make_job(tmp_path, chain(tmp_path, calls, fail="b"))
    assert run(job) == 1 and calls == ["a", "b"]
    st = job.state.get("b")
    assert st["status"] == "failed" and "boom" in st["error"]
    assert (job.job_dir / "run_summary.json").exists()
    calls.clear()
    job2 = make_job(tmp_path, chain(tmp_path, calls))
    assert run(job2) == 0 and calls == ["b", "c"]


def test_skip_status_and_optional_unavailable(tmp_path):
    calls = []
    stages = chain(tmp_path, calls, fail="skipc")
    stages.append(Stage("d", "llm", lambda sc: (_ for _ in ()).throw(StageUnavailable("stage d not available yet")),
                        optional=True))
    stages.append(Stage("e", "llm", lambda sc: (_ for _ in ()).throw(StageUnavailable("stage e not available yet"))))
    job = make_job(tmp_path, stages)
    assert run(job) == 1
    assert job.state.get("c")["status"] == "skipped"
    assert job.state.get("d")["status"] == "skipped"
    assert job.state.get("e")["status"] == "failed" and "not available yet" in job.state.get("e")["error"]


def test_from_only_until_selection(tmp_path):
    calls = []
    job = make_job(tmp_path, chain(tmp_path, calls))
    assert runner.select_range(job, frm="b") == ["b", "c"]
    assert runner.select_range(job, only="b") == ["b"]
    assert runner.select_range(job, until="b") == ["a", "b"]
    with pytest.raises(ValueError):
        runner.select_range(job, only="nope")
    with pytest.raises(ValueError):
        runner.select_range(job, frm="c", until="a")
    run(job, until="b")
    assert calls == ["a", "b"] and job.state.get("c") == {}
    calls.clear()
    run(job, frm="b")                      # b is done and unchanged -> only c runs
    assert calls == ["c"]
    calls.clear()
    run(job, only="b", force=True)
    assert calls == ["b"]
    plan = runner.make_plan(job, frm="b")
    # b was re-forced above, so its output changed and c is stale; b itself is current
    assert [(e.name, e.action) for e in plan] == [("a", "OUT"), ("b", "SKIP"), ("c", "RUN")]


def test_dry_run_changes_nothing_and_prints_plan(tmp_path):
    calls, lines = [], []
    job = make_job(tmp_path, chain(tmp_path, calls))
    job.out = lines.append
    assert runner.run_job(job, dry_run=True) == 0
    assert calls == [] and not (job.job_dir / "state.json").exists()
    assert "estimated LLM calls" in "\n".join(lines) and "RUN" in "\n".join(lines)


def test_real_registry_matches_architecture(tmp_path):
    job = make_job(tmp_path, [])
    from pipeline import stages_scan, stages_text
    assert [s.name for s in stages_scan.STAGES] == [
        "ocr", "rapid", "segments", "tm", "translate", "notes", "automap", "build", "audit", "audit_review",
        "check", "qa", "visual", "report"]
    assert [s.name for s in stages_text.STAGES][:3] == ["extract", "build_dry", "export_todo"]
    assert {s.name: s.kind for s in stages_scan.STAGES}["translate"] == "llm"
    # every dep exists and precedes its stage
    for reg in (stages_scan.STAGES, stages_text.STAGES):
        seen = set()
        for s in reg:
            assert set(s.deps) <= seen, s.name
            seen.add(s.name)


def test_missing_llm_module_is_a_clear_error(tmp_path):
    from pipeline.core import lazy
    with pytest.raises(StageUnavailable, match="not available yet"):
        lazy("scan.no_such_module_yet", "tm")


# ------------------------------------------------------------------ cost
def _ledger(tmp_path):
    rows = [
        dict(stage="translate", task="phrase_translate", item="b1", cached=False, attempt=1, ok=True, input_tokens=1000,
             output_tokens=200, cache_read_tokens=10, cache_write_tokens=5, images=0, cost_usd=0.10, orig_cost_usd=0.10),
        dict(stage="translate", task="phrase_translate", item="b1", cached=False, attempt=2, ok=False, input_tokens=1100,
             output_tokens=100, cache_read_tokens=0, cache_write_tokens=0, images=0, cost_usd=0.05, orig_cost_usd=0.05),
        dict(stage="translate", task="phrase_translate", item="b1", cached=True, attempt=1, ok=True, input_tokens=0,
             output_tokens=0, images=0, cost_usd=0.0, orig_cost_usd=0.10),
        dict(stage="notes", task="page_notes", item="p01", cached=False, attempt=1, ok=True, input_tokens=3000,
             output_tokens=800, images=3, cost_usd=0.30, orig_cost_usd=0.30),
    ]
    jd = tmp_path / "work" / "job"
    jd.mkdir(parents=True)
    (jd / "ledger.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    return jd, rows


def test_cost_report_aggregation(tmp_path):
    jd, _ = _ledger(tmp_path)
    rep = cost.write_report(jd, pages=2)
    t = rep["totals"]
    assert (t["calls"], t["cached"], t["failed"], t["retries"]) == (4, 1, 1, 1)
    assert t["cost_usd"] == pytest.approx(0.45) and t["orig_cost_usd"] == pytest.approx(0.10)
    assert t["input_tokens"] == 5100 and t["images"] == 3
    assert rep["per_page_usd"] == pytest.approx(0.225)
    by = {(r["stage"], r["task"]): r for r in rep["rows"]}
    assert by[("translate", "phrase_translate")]["calls"] == 3 and by[("notes", "page_notes")]["images"] == 3
    md = (jd / "cost_report.md").read_text(encoding="utf-8")
    assert "# 成本报告" in md and "每页平均" in md
    assert json.loads((jd / "cost_report.json").read_text(encoding="utf-8"))["pages"] == 2


def test_cost_report_empty_ledger(tmp_path):
    rep = cost.write_report(tmp_path / "job", pages=0)
    assert rep["totals"]["calls"] == 0 and rep["per_page_usd"] is None


def test_summary_gates_and_cost(tmp_path):
    job = make_job(tmp_path, [Stage("report", "local", lambda sc: {}, always=True)])
    job.state.set("build", status="done", metrics={"unplaced": 0})
    job.state.set("audit", status="done", metrics={"remaining": 3, "second_detector": True})
    job.state.set("check", status="done", metrics={"mark_problems": 0, "gate_ok": True})
    r = summary.write(job)
    assert r["delivery_ready"] is False and r["gates_failed"] == ["audit_remaining"]
    s = json.loads((job.job_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert s["gates"]["unplaced"]["ok"] is True and "cost" in s
    job.state.set("audit", metrics={"remaining": 0, "second_detector": True})
    job.state.set("qa", status="done", metrics={"qa_flags": 5})        # advisory only
    assert summary.write(job)["delivery_ready"] is True


# ------------------------------------------------------------------ doctor / init
def test_doctor_returns_table():
    rows = doctor.run_checks(None)
    names = [r[0] for r in rows]
    assert "python" in names and "tesseract" in names and "write access" in names
    assert all(r[1] in ("OK", "WARN", "FAIL") for r in rows)
    lines = []
    rc = doctor.doctor(None, out=lines.append)
    assert "OK" in lines[0] and rc in (0, 1)
    assert (rc == 1) == any(r[1] == "FAIL" for r in doctor.run_checks(None))


def test_doctor_with_config_fails_on_missing_pdf(tmp_path):
    (tmp_path / "project.toml").write_text('[project]\nname="x"\npdf="nope.pdf"\n', encoding="utf-8")
    rows = doctor.run_checks(JobConfig.load(tmp_path / "project.toml"))
    pdf = [r for r in rows if r[0] == "input pdf"][0]
    assert pdf[1] == "FAIL"


def test_tessdata_variant():
    assert doctor.tessdata_variant(15 * 1024 * 1024) == "best" and doctor.tessdata_variant(3 * 1024 * 1024) == "fast"


def test_init_detects_text_layer(tmp_path):
    assert cli.detect_route(DEMO_PDF)["route"] == "text"
    folder = tmp_path / "job"
    assert cli.main(["init", str(folder), "--pdf", str(DEMO_PDF)]) == 0
    cfg = JobConfig.load(folder / "project.toml")
    assert cfg.route == "text" and "scan" not in cfg.raw
    assert cfg.llm["models"]["audit_review"] == "haiku" and cfg.job["max_audit_loops"] == 2
    assert cli.main(["init", str(folder), "--pdf", str(DEMO_PDF)]) == 1          # refuses to overwrite


def test_init_detects_scan(tmp_path):
    src = pymupdf.open(DEMO_PDF)
    out = pymupdf.open()
    for i in range(min(2, src.page_count)):
        pix = src[i].get_pixmap(dpi=50)
        pg = out.new_page(width=src[i].rect.width, height=src[i].rect.height)
        pg.insert_image(pg.rect, pixmap=pix)
    scan_pdf = tmp_path / "scan.pdf"
    out.save(scan_pdf)
    assert cli.detect_route(scan_pdf)["route"] == "scan"
    folder = tmp_path / "sj"
    assert cli.main(["init", str(folder), "--pdf", str(scan_pdf), "--code", "AB1"]) == 0
    cfg = JobConfig.load(folder / "project.toml")
    assert cfg.route == "scan" and cfg.scan.code == "AB1" and cfg.pdf == scan_pdf.resolve()
    assert cfg.job_dir == (folder / "work" / "job").resolve() or cfg.job_dir == folder.resolve() / "work" / "job"


def test_config_defaults_and_tm_cfg(tmp_path):
    (tmp_path / "project.toml").write_text('[scan]\ncode="X"\n[llm]\nbudget_usd=5\n[llm.models]\npage_notes="opus"\n',
                                           encoding="utf-8")
    cfg = JobConfig.load(tmp_path / "project.toml")
    assert cfg.llm["budget_usd"] == 5 and cfg.llm["concurrency"] == 4
    assert cfg.llm["models"]["page_notes"] == "opus" and cfg.llm["models"]["phrase_translate"] == "sonnet"
    tm = cfg.tm_cfg()
    assert tm["project"].endswith("project.json") and any(g.endswith("gost_general.json") for g in tm["glossaries"])


def test_shim_dry_run_both_routes(tmp_path):
    for name, pdf in (("t", DEMO_PDF),):
        folder = tmp_path / name
        assert cli.main(["init", str(folder), "--pdf", str(pdf)]) == 0
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "rudraw.py"), "run", "--dry-run",
                            "--config", str(folder / "project.toml")], capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, r.stderr
        assert "extract" in r.stdout and "merge_tm" in r.stdout and "estimated LLM calls" in r.stdout
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "project.toml").write_text('[scan]\ncode="S"\nwork="work"\n', encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "rudraw.py"), "run", "--dry-run",
                        "--config", str(tmp_path / "s" / "project.toml")], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert "audit_review" in r.stdout and "page_notes" not in r.stdout
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "rudraw.py"), "status",
                        "--config", str(tmp_path / "s" / "project.toml")], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0 and "pending" in r.stdout


def test_text_route_translate_writes_validated_batch(tmp_path):
    """The text route's translate stage runs phrase_translate (fake backend) over tm_todo.json and
    writes a batch.tsv that merge_tm accepts; afterwards no TM key is missing."""
    folder = tmp_path / "t"
    assert cli.main(["init", str(folder), "--pdf", str(DEMO_PDF)]) == 0
    toml = folder / "project.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace('backend = "claude-cli"', 'backend = "fake"'), encoding="utf-8")
    job = Job(JobConfig.load(toml), out=lambda *_: None)
    assert runner.run_job(job, until="export_todo", echo=False) == 0
    todo = {"Бетон класса B⟨1⟩ по ГОСТ ⟨2⟩": {"ru": "Бетон класса B25 по ГОСТ 26633", "pages": [1]},
            "Схема производства работ": {"ru": "Схема производства работ", "pages": [1, 2]}}
    (folder / "review" / "tm_todo.json").write_text(json.dumps(todo, ensure_ascii=False), encoding="utf-8")
    assert runner.run_job(job, only="translate", echo=False) == 0
    rows = (folder / "review" / "batch.tsv").read_text(encoding="utf-8").splitlines()
    assert len(rows) == len(todo)
    for row in rows:
        i, guard, zh = row.split("	")
        assert list(todo)[int(i)].startswith(guard) and zh == "∅"      # fake answers 'keep'
    assert job.metrics("translate")["unresolved"] == 0
    assert runner.run_job(job, only="merge_tm", echo=False) == 0
    assert job.metrics("merge_tm")["merged"] == 2


def test_split_paragraphs_at_first_line_indent():
    from engine.pdf_build import split_paragraphs
    R = pymupdf.Rect
    items = [(R(90, 0, 500, 10), ["a"], None), (R(70, 12, 500, 22), ["b"], None),
             (R(90, 24, 500, 34), ["c"], None), (R(70, 36, 300, 46), ["d"], None), (R(72, 48, 300, 58), ["e"], None)]
    out = split_paragraphs("3", items)
    assert [n for n, _ in out] == ["3", ""]
    assert [[it[1][0] for it in its] for _, its in out] == [["a", "b"], ["c", "d", "e"]]
    assert split_paragraphs("", items[:1]) == [("", items[:1])]


def test_extension_page_widens_and_keeps_original(tmp_path):
    """Running-text page: the original stays, the page grows to the right, Chinese only in the new column."""
    from engine import extend
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((80, 100), "Общие требования к производству работ", fontname="china-s", fontsize=11)
    before = page.get_text()
    class FakeTM:
        def translate(self, text, ctx="body"):
            return "施工作业一般要求", text
    st = extend.render(page, FakeTM(), [pymupdf.Rect(0, 0, 0, 0)])
    assert page.rect.width > 595 * 1.5 and page.rect.height == 842
    assert st["paras"] == 1 and st["missing"] == 0 and st["fs"] >= 8
    after = page.get_text("words")
    assert all(w[0] >= 595 for w in after if "施工" in w[4])
    assert "Общие требования" in page.get_text() and before.strip() in page.get_text()


def test_scan_prose_paragraphs_join_lines_of_one_paragraph():
    from scan import prose
    L = [dict(id=1, box=(0.12, 0.30, 0.80, 0.32), text="ПАО и Общество с ограниченной ответственностью обсудили"),
         dict(id=2, box=(0.08, 0.32, 0.80, 0.34), text="график выполнения строительно-монтажных работ и согласовывают"),
         dict(id=3, box=(0.08, 0.34, 0.50, 0.36), text="его в редакции, приложенной к протоколу."),
         dict(id=4, box=(0.08, 0.50, 0.20, 0.52), text="Руководитель проекта")]
    pars = prose.paragraphs(L)
    assert [p["ids"] for p in pars] == [[1, 2, 3]]
    assert prose.classify(pars, lambda b: False)[0]["kind"] == "prose"
    assert prose.join(["строительно-мон-", "тажных работ"]) == "строительно-монтажных работ"


def test_extension_table_rows_keep_row_numbers_and_rules():
    """A ruled table: one entry per row (row number + cells in column order), the row rules are
    continued into the column."""
    from engine import extend
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    xs, ys = [70, 100, 300, 500], [100, 130, 160]
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    page.insert_text((75, 120), "№", fontname="china-s", fontsize=10)
    page.insert_text((105, 120), "Наименование", fontname="china-s", fontsize=10)
    page.insert_text((305, 120), "Назначение", fontname="china-s", fontsize=10)
    page.insert_text((80, 150), "7", fontname="china-s", fontsize=10)
    page.insert_text((105, 150), "Защитные очки", fontname="china-s", fontsize=10)
    page.insert_text((305, 150), "Защита глаз", fontname="china-s", fontsize=10)
    zh = {"Наименование": "名称", "Назначение": "用途", "Защитные очки": "防护眼镜", "Защита глаз": "眼部防护"}
    class FakeTM:
        def translate(self, text, ctx="body"):
            return zh.get(text, ""), text
    entries, legend, st = extend.collect(page, FakeTM(), [pymupdf.Rect()])
    rows = [(round(e["y"]), e["zh"], round(e["bot"])) for e in entries if e.get("bot")]
    assert rows == [(100, "名称 | 用途", 130), (130, "7. 防护眼镜 | 眼部防护", 160)]
    assert st["rules"] == [100.0, 130.0, 160.0]
