"""work/job/run_summary.json: delivery gates, stage table, cost totals (+ cost_report.md/json)."""
from __future__ import annotations
import json

from . import cost
from .state import now


def gates(job) -> dict:
    """Delivery gates.  ok: True / False / None (not evaluated yet)."""
    def g(value, ok, note=""):
        return {"value": value, "ok": ok, "note": note}
    m = job.metrics
    if job.cfg.route == "scan":
        b, a, c, q = m("build"), m("audit"), m("check"), m("qa")
        ar = a.get("remaining")
        det = a.get("second_detector", True)
        return {
            "unplaced": g(b.get("unplaced"), None if b.get("unplaced") is None else b["unplaced"] == 0, "build_report 未放置 must be 0"),
            "audit_remaining": g(ar, None if ar is None else (ar == 0 and det),
                                 "audit must reach remaining 0 with the second detector present" if det
                                 else "no work/rapid: the audit only saw Tesseract"),
            "mark_gate": g(c.get("mark_problems"), c.get("gate_ok"), "50_check member marks"),
            "qa_flags": g(q.get("qa_flags"), None if q.get("qa_flags") is None else q["qa_flags"] == 0,
                          "advisory: flagged pages go to visual review"),
        }
    b = m("build")
    mk = b.get("missing_tm_keys")
    return {"missing_tm_keys": g(mk, None if mk is None else mk == 0, "untranslated TM keys after the final build")}


def write(job) -> dict:
    job.job_dir.mkdir(parents=True, exist_ok=True)
    rep = cost.write_report(job.job_dir, job.n_pages)
    gs = gates(job)
    advisory = {"qa_flags"}
    ready = all(v["ok"] is True for k, v in gs.items() if k not in advisory)
    stages = {}
    for st in job.stages:
        rec = job.state.get(st.name)
        stages[st.name] = {"kind": st.kind, "status": rec.get("status", "pending"), "duration_s": rec.get("duration_s"),
                           "finished": rec.get("finished"), "error": rec.get("error"), "note": rec.get("note")}
    summary = {"generated": now(), "route": job.cfg.route, "config": str(job.cfg.path), "pages": job.n_pages,
               "delivery_ready": ready, "gates": gs, "stages": stages,
               "cost": {"total_usd": rep["totals"]["cost_usd"], "calls": rep["totals"]["calls"],
                        "cached_calls": rep["totals"]["cached"], "replay_saved_usd": rep["totals"]["orig_cost_usd"],
                        "per_page_usd": rep["per_page_usd"]}}
    (job.job_dir / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"delivery_ready": ready, "cost_usd": rep["totals"]["cost_usd"],
            "gates_failed": [k for k, v in gs.items() if v["ok"] is False and k not in advisory]}
