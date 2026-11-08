"""Plan and execute a job: resume (skip unchanged done stages), --from/--only/--until, state + logs."""
from __future__ import annotations
import time
import traceback
from dataclasses import dataclass

from . import cost
from .core import Job, StageCtx, StageError, StageUnavailable
from .state import now

PRIVATE = ("_skip",)


@dataclass
class Entry:
    name: str
    action: str          # RUN | SKIP (up to date) | OUT (outside --from/--until/--only)
    why: str = ""


def select_range(job: Job, frm=None, only=None, until=None) -> list[str]:
    names = job.names()
    for label, v in (("--from", frm), ("--only", only), ("--until", until)):
        if v and v not in names:
            raise ValueError(f"{label} {v!r}: unknown stage; this route has: {', '.join(names)}")
    if only:
        return [only]
    i = names.index(frm) if frm else 0
    j = names.index(until) if until else len(names) - 1
    if i > j:
        raise ValueError("--from is after --until")
    return names[i:j + 1]


def why_stale(job: Job, st, pages) -> str:
    rec = job.state.get(st.name)
    s = rec.get("status")
    if s != "done":
        return "not run yet" if not s else f"previous status: {s}"
    if st.always:
        return "always runs"
    if rec.get("inputs_hash") != job.inputs_hash(st, pages):
        return "inputs changed"
    return "outputs changed or missing"


def make_plan(job: Job, frm=None, only=None, until=None, force=False, pages=None) -> list[Entry]:
    sel = set(select_range(job, frm, only, until))
    will, plan = set(), []
    for st in job.stages:
        if st.name not in sel:
            plan.append(Entry(st.name, "OUT", "outside the selected range"))
            continue
        if force:
            will.add(st.name); plan.append(Entry(st.name, "RUN", "--force")); continue
        up = [d for d in st.deps if d in will]
        if up:
            will.add(st.name); plan.append(Entry(st.name, "RUN", f"upstream re-runs ({up[0]})")); continue
        if job.up_to_date(st, pages):
            plan.append(Entry(st.name, "SKIP", "done, inputs unchanged"))
        else:
            will.add(st.name); plan.append(Entry(st.name, "RUN", why_stale(job, st, pages)))
    return plan


def estimate_calls(job: Job, plan: list[Entry]) -> tuple[int, list[str]]:
    total, notes = 0, []
    for e in plan:
        st = job.stage(e.name)
        if e.action != "RUN" or not st.est:
            continue
        n, note = st.est(job)
        total += n
        notes.append(f"{st.name}: {n if not note.startswith('unknown') else '?'}  ({note})")
    return total, notes


def render_plan(job: Job, plan: list[Entry]) -> str:
    w = max(len(s.name) for s in job.stages)
    L = [f"route {job.cfg.route}, {job.n_pages} page(s), config {job.cfg.path}", "",
         f"{'stage'.ljust(w)}  kind   action  why"]
    for e in plan:
        L.append(f"{e.name.ljust(w)}  {job.stage(e.name).kind.ljust(5)}  {e.action.ljust(6)}  {e.why}")
    total, notes = estimate_calls(job, plan)
    L += ["", f"estimated LLM calls (lower bound, replayed cache hits are free): {total}"] + [f"  {n}" for n in notes]
    return "\n".join(L)


def _execute(job: Job, st, pages, echo=True) -> str:
    """Run one stage; returns done | skipped | failed.  State and log are always updated."""
    job.log_dir.mkdir(parents=True, exist_ok=True)
    ih = job.inputs_hash(st, pages)
    job.state.set(st.name, status="running", started=now(), finished=None, error=None, note=None, pages=pages,
                  inputs_hash=None, outputs_fp=None)
    t0 = time.time()
    status, metrics, err, note = "done", {}, None, None
    with open(job.log_dir / f"{st.name}.log", "w", encoding="utf-8") as lf:
        lf.write(f"# {st.name}  {now()}  pages={pages}\n")
        sc = StageCtx(job, st, pages, lf, echo)
        try:
            metrics = st.run(sc) or {}
            if "_skip" in metrics:
                status, note = "skipped", metrics.get("_skip")
        except StageUnavailable as e:
            status, err = ("skipped", None) if st.optional else ("failed", str(e))
            note = str(e) if st.optional else None
            lf.write(f"UNAVAILABLE: {e}\n")
        except StageError as e:
            status, err = "failed", str(e)
            lf.write(f"FAILED: {e}\n")
        except KeyboardInterrupt:
            job.state.set(st.name, status="failed", error="interrupted", finished=now())
            raise
        except Exception as e:  # noqa: BLE001
            status, err = "failed", f"{type(e).__name__}: {e}"
            lf.write(traceback.format_exc())
    rec = dict(status=status, finished=now(), duration_s=round(time.time() - t0, 1), error=err, note=note,
               metrics={k: v for k, v in metrics.items() if k not in PRIVATE})
    if status != "failed":
        rec["outputs"] = [str(p) for p in st.outputs(job)]
        rec["outputs_fp"] = job.outputs_fp(st)
        rec["inputs_hash"] = job.inputs_hash(st, pages) if st.settle else ih
    job.state.set(st.name, **rec)
    return status


def run_job(job: Job, frm=None, only=None, until=None, force=False, pages=None, dry_run=False, echo=True) -> int:
    out = job.out
    plan = make_plan(job, frm, only, until, force, pages)
    if dry_run:
        out(render_plan(job, plan))
        return 0
    job.state.meta(route=job.cfg.route, pages=job.n_pages, config=str(job.cfg.path), last_run=now())
    ran_report, rc = False, 0
    out(render_plan(job, plan).split("\n\nestimated")[0] + "\n")
    for e in plan:
        if e.action == "OUT":
            continue
        st = job.stage(e.name)
        # decide on the *current* state: an upstream stage that re-ran but produced identical files does not force us
        if not force and job.up_to_date(st, pages):
            out(f"[skip] {st.name}: done, inputs unchanged")
            continue
        out(f"[run ] {st.name}: {st.desc}")
        s = _execute(job, st, pages, echo)
        rec = job.state.get(st.name)
        if s == "done":
            out(f"[done] {st.name} in {rec['duration_s']}s  {_compact(rec.get('metrics'))}")
            ran_report |= st.name == "report"
        elif s == "skipped":
            out(f"[skip] {st.name}: {rec.get('note')}")
        else:
            out(f"[FAIL] {st.name}: {rec.get('error')}\n       log: {job.log_dir / (st.name + '.log')}")
            rc = 1
            break
    if rc:                                   # still leave a summary + cost report behind
        from . import summary
        summary.write(job)
    out("")
    out(status_table(job))
    rep = cost.read_ledger(job.job_dir / "ledger.jsonl")
    if rep:
        t = cost.build_report(rep, job.n_pages)
        out(f"\ncost: {t['totals']['cost_usd']:.4f} USD, {t['totals']['calls']} calls "
            f"({t['totals']['cached']} cached)  -> {job.job_dir / 'cost_report.md'}")
    if not rc and ran_report:
        import json
        s = json.loads((job.job_dir / "run_summary.json").read_text(encoding="utf-8"))
        bad = [k for k, v in s["gates"].items() if v["ok"] is False and k != "qa_flags"]
        out(f"delivery gates: {'READY' if s['delivery_ready'] else 'NOT READY'}"
            + (f"  failing: {', '.join(bad)}" if bad else "") + f"  (see {job.job_dir / 'run_summary.json'})")
        rc = 2 if bad else 0
    return rc


def _compact(m, limit=110) -> str:
    if not m:
        return ""
    s = "  ".join(f"{k}={v}" for k, v in m.items() if not isinstance(v, (dict, list)))
    return s if len(s) <= limit else s[:limit - 1] + "…"


def status_table(job: Job) -> str:
    w = max(len(s.name) for s in job.stages)
    L = [f"{'stage'.ljust(w)}  kind   status    fresh  time     metrics"]
    for st in job.stages:
        rec = job.state.get(st.name)
        s = rec.get("status", "pending")
        fresh = "-"
        if s == "done":
            fresh = "yes" if job.up_to_date(st, rec.get("pages")) or st.always else "STALE"
        dur = f"{rec['duration_s']}s" if rec.get("duration_s") is not None else ""
        extra = _compact(rec.get("metrics")) or (rec.get("error") or rec.get("note") or "")
        L.append(f"{st.name.ljust(w)}  {st.kind.ljust(5)}  {s.ljust(8)}  {fresh.ljust(5)}  {dur.ljust(7)}  {extra}")
    return "\n".join(L)
