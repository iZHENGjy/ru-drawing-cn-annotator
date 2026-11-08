"""Cost report from work/job/ledger.jsonl (one row per LLM call attempt)."""
from __future__ import annotations
import json
from pathlib import Path

NUM = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "images")


def read_ledger(path: Path) -> list[dict]:
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            try:
                rows.append(json.loads(ln))
            except ValueError:
                continue
    return rows


def _blank():
    return {"calls": 0, "cached": 0, "failed": 0, "retries": 0, **{k: 0 for k in NUM},
            "cost_usd": 0.0, "orig_cost_usd": 0.0}


def _add(acc: dict, r: dict):
    acc["calls"] += 1
    cached = bool(r.get("cached"))
    acc["cached"] += cached
    acc["failed"] += (r.get("ok") is False)
    acc["retries"] += (int(r.get("attempt") or 1) > 1)
    for k in NUM:
        acc[k] += int(r.get(k) or 0)
    acc["cost_usd"] += float(r.get("cost_usd") or 0)
    if cached:
        acc["orig_cost_usd"] += float(r.get("orig_cost_usd") or 0)


def build_report(rows: list[dict], pages: int = 0) -> dict:
    groups: dict[tuple, dict] = {}
    total = _blank()
    for r in rows:
        key = (r.get("stage") or "?", r.get("task") or "?")
        _add(groups.setdefault(key, _blank()), r)
        _add(total, r)
    out = [{"stage": s, "task": t, **v} for (s, t), v in sorted(groups.items())]
    for d in out + [total]:
        d["cost_usd"] = round(d["cost_usd"], 4)
        d["orig_cost_usd"] = round(d["orig_cost_usd"], 4)
    return {"rows": out, "totals": total, "pages": pages,
            "per_page_usd": round(total["cost_usd"] / pages, 4) if pages else None}


def render_md(rep: dict) -> str:
    L = ["# 成本报告", "", "## 按阶段 / 任务", "",
         "| 阶段 | 任务 | 调用 | 缓存命中 | 失败 | 重试 | 输入 token | 输出 token | 缓存读 | 缓存写 | 图片 | 费用 USD | 回放原价 USD |",
         "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for r in rep["rows"] + [dict(rep["totals"], stage="**合计**", task="")]:
        L.append(f"| {r['stage']} | {r['task']} | {r['calls']} | {r['cached']} | {r['failed']} | {r['retries']} | "
                 f"{r['input_tokens']} | {r['output_tokens']} | {r['cache_read_tokens']} | {r['cache_write_tokens']} | "
                 f"{r['images']} | {r['cost_usd']:.4f} | {r['orig_cost_usd']:.4f} |")
    t = rep["totals"]
    L += ["", "## 合计", "", f"- 调用 {t['calls']} 次（缓存命中 {t['cached']}，失败 {t['failed']}，重试 {t['retries']}）",
          f"- 实际费用 {t['cost_usd']:.4f} USD；缓存回放节省 {t['orig_cost_usd']:.4f} USD", "", "## 每页平均", ""]
    if rep["pages"]:
        L.append(f"- 共 {rep['pages']} 页，平均每页 {rep['per_page_usd']:.4f} USD")
    else:
        L.append("- 页数未知")
    if not rep["rows"]:
        L += ["", "（账本为空：尚未调用 LLM，或全部阶段为本地阶段。）"]
    return "\n".join(L) + "\n"


def write_report(job_dir: Path, pages: int = 0) -> dict:
    job_dir = Path(job_dir)
    rep = build_report(read_ledger(job_dir / "ledger.jsonl"), pages)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "cost_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    (job_dir / "cost_report.md").write_text(render_md(rep), encoding="utf-8")
    return rep
