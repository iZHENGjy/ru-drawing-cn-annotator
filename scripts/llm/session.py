"""Session: run one LLM task over many payloads with cache, retries, budget and ledger.

    from llm import Session
    s = Session(work_dir, cfg, stage="translate")         # cfg = parsed [llm] table
    results = s.run("phrase_translate", payloads, ids)     # list[Result], input order

cfg keys: backend (claude-cli | anthropic | fake), concurrency (4), budget_usd (inf),
background (""), models {task: alias}, timeout_s (600), max_retries (2, capped at 2).
"""
from __future__ import annotations

import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import backends as B
from . import tasks as T
from .cache import Cache, make_key

DEFAULT_MODELS = {"phrase_translate": "sonnet", "page_notes": "sonnet", "audit_review": "haiku",
                  "visual_review": "sonnet"}
LEDGER_FIELDS = ("ts", "stage", "task", "item", "model", "backend", "cached", "attempt", "ok", "input_tokens",
                 "output_tokens", "cache_read_tokens", "cache_write_tokens", "images", "cost_usd",
                 "orig_cost_usd", "duration_s")
CALL_OVERHEAD_TOKENS = 1600       # claude-cli fixed overhead per call (structured-output tool etc.)
IMAGE_TOKENS = 1600               # budget estimate per image (upper bound for a <=1.15 Mpx image)
_file_locks: dict[str, threading.Lock] = {}
_file_locks_guard = threading.Lock()


def _lock_for(p: Path) -> threading.Lock:
    with _file_locks_guard:
        return _file_locks.setdefault(str(Path(p).resolve()), threading.Lock())


@dataclass
class Result:
    id: str
    ok: bool
    output: dict | None = None        # when ok is False this may hold the last (rejected) answer
    errors: list = field(default_factory=list)
    cached: bool = False
    cost_usd: float = 0.0             # spent in this run (0 for a cache hit)
    usage: dict = field(default_factory=dict)
    attempts: int = 0


def ledger_total(path: Path) -> float:
    tot = 0.0
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                tot += float(json.loads(line).get("cost_usd") or 0)
            except ValueError:
                pass
    return tot


class Session:
    def __init__(self, work_dir, cfg: dict | None, stage: str, backend=None):
        self.work = Path(work_dir)
        self.cfg = dict(cfg or {})
        self.stage = stage
        self.backend_name = self.cfg.get("backend", "claude-cli")
        self._backend = backend
        self.concurrency = max(1, int(self.cfg.get("concurrency", 4)))
        b = self.cfg.get("budget_usd")
        self.budget = float(b) if b not in (None, "") else math.inf
        self.timeout = float(self.cfg.get("timeout_s", 600))
        self.max_retries = max(0, min(2, int(self.cfg.get("max_retries", 2))))
        self.background = self.cfg.get("background", "") or ""
        self.models = {**DEFAULT_MODELS, **(self.cfg.get("models") or {})}
        self.ledger_path = self.work / "job" / "ledger.jsonl"
        self.cache = Cache(self.work / "llm" / "cache")
        self.errlog = self.work / "llm" / "errors.jsonl"
        self._lock = threading.Lock()
        self.spent = ledger_total(self.ledger_path)
        self.reserved = 0.0
        self._observed: dict[tuple, list] = {}

    # ------------------------------------------------------------------ public
    @property
    def backend(self):
        if self._backend is None:
            with self._lock:
                if self._backend is None:
                    self._backend = B.make(self.backend_name)
        return self._backend

    def model_for(self, task: str) -> str:
        return self.models.get(task) or DEFAULT_MODELS[task]

    def run(self, task: str, payloads: list[dict], ids: list[str]) -> list[Result]:
        if len(payloads) != len(ids):
            raise ValueError("payloads and ids differ in length")
        if len(set(map(str, ids))) != len(ids):
            raise ValueError("ids must be unique")
        spec = T.get(task)
        if not payloads:
            return []
        with ThreadPoolExecutor(max_workers=min(self.concurrency, len(payloads))) as ex:
            futs = [ex.submit(self._one, spec, p, str(i)) for p, i in zip(payloads, ids)]
            return [f.result() for f in futs]

    # ------------------------------------------------------------------ internals
    def _one(self, spec, payload: dict, item: str) -> Result:
        try:
            return self._one_inner(spec, payload, item)
        except Exception as e:                                   # never lose the batch
            return Result(id=item, ok=False, errors=[f"internal error: {type(e).__name__}: {e}"])

    def _one_inner(self, spec, payload: dict, item: str) -> Result:
        model = self.model_for(spec.name)
        system = spec.system(self.background)
        images = [Path(p) for p in payload.get("images") or []]
        key = make_key(spec.name, spec.prompt_version, model, payload.get("data"), images,
                       spec.system_hash(self.background))
        hit = self.cache.get(key)
        if hit and hit.get("output") is not None and not spec.validate(payload, hit["output"]):
            self._ledger(dict(task=spec.name, item=item, model=model, backend=hit.get("backend", self.backend_name),
                              cached=True, attempt=1, ok=True, usage={}, images=len(images), cost_usd=0.0,
                              orig_cost_usd=float(hit.get("cost_usd") or 0), duration_s=0.0))
            return Result(id=item, ok=True, output=hit["output"], cached=True, cost_usd=0.0,
                          usage=hit.get("usage") or {}, attempts=0)

        cur, prev, feedback = payload, None, ""
        total_cost, usage_sum, errs, merged = 0.0, {}, [], None
        for attempt in range(1, self.max_retries + 2):
            text, imgs = spec.render(cur)
            if feedback:
                text += feedback
            est = self._estimate(spec.name, model, system, text, imgs)
            with self._lock:
                if self.spent + self.reserved + est > self.budget:
                    return Result(id=item, ok=False, output=merged, errors=["budget"] + errs,
                                  cost_usd=total_cost, usage=usage_sum, attempts=attempt - 1)
                self.reserved += est
            t0 = time.time()
            try:
                reply = self.backend.call(B.Request(task=spec.name, model=model, system=system, text=text,
                                                    images=list(imgs), schema=spec.schema, timeout=self.timeout,
                                                    payload=cur, attempt=attempt))
            except Exception as e:
                reply = B.Reply(error=f"backend {type(e).__name__}: {e}")
            dt = time.time() - t0
            with self._lock:
                self.reserved -= est
                self.spent += reply.cost_usd
                if reply.cost_usd and not reply.error:
                    self._observed.setdefault((spec.name, model), []).append(reply.cost_usd)
            total_cost += reply.cost_usd
            for k, v in reply.usage.items():
                usage_sum[k] = usage_sum.get(k, 0) + v
            if reply.error:
                errs = [reply.error]
            else:
                merged = spec.merge(prev, reply.output) if cur is not payload else reply.output
                errs = spec.validate(payload, merged)
            self._ledger(dict(task=spec.name, item=item, model=model, backend=self.backend_name, cached=False,
                              attempt=attempt, ok=not errs, usage=reply.usage, images=len(imgs),
                              cost_usd=reply.cost_usd, orig_cost_usd=reply.cost_usd, duration_s=dt))
            if not errs:
                self.cache.put(key, {"task": spec.name, "prompt_version": spec.prompt_version, "model": model,
                                     "backend": self.backend_name, "item": item, "output": merged,
                                     "cost_usd": total_cost, "usage": usage_sum, "attempts": attempt,
                                     "ts": _now()})
                return Result(id=item, ok=True, output=merged, cost_usd=total_cost, usage=usage_sum,
                              attempts=attempt)
            self._errlog(spec.name, item, attempt, errs)
            if reply.error:                              # transport / model error: same request again
                continue
            sub = spec.retry_payload(payload, merged, errs)
            if sub is not None:
                cur, prev = sub, merged
                feedback = _feedback(errs, _subset(merged, sub), partial=True)
            else:
                cur, prev = payload, None
                feedback = _feedback(errs, merged, partial=False)
        return Result(id=item, ok=False, output=merged, errors=errs, cost_usd=total_cost, usage=usage_sum,
                      attempts=self.max_retries + 1)

    def _estimate(self, task, model, system, text, imgs) -> float:
        obs = self._observed.get((task, model))
        if obs:
            return 1.5 * sum(obs) / len(obs)
        tin = (len(system) + len(text)) / 2.5 + CALL_OVERHEAD_TOKENS + IMAGE_TOKENS * len(imgs)
        tout = min(0.8 * len(text) / 2.5, 16000) + 300
        pi, po, pr, pw5, pw1 = B.price(model)
        return (tin * pw1 + tout * po) / 1e6

    def _ledger(self, r: dict) -> None:
        u = r.pop("usage") or {}
        row = {"ts": _now(), "stage": self.stage, **r,
               "input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
               "cache_read_tokens": u.get("cache_read_tokens", 0), "cache_write_tokens": u.get("cache_write_tokens", 0)}
        row["cost_usd"] = round(float(row["cost_usd"]), 6)
        row["orig_cost_usd"] = round(float(row["orig_cost_usd"]), 6)
        row["duration_s"] = round(float(row["duration_s"]), 2)
        row = {k: row[k] for k in LEDGER_FIELDS}
        _append(self.ledger_path, row)

    def _errlog(self, task, item, attempt, errs):
        _append(self.errlog, {"ts": _now(), "stage": self.stage, "task": task, "item": item,
                              "attempt": attempt, "errors": errs[:50]})


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _lock_for(path):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _subset(prev, sub):
    """previous answers of the items that are asked again (batch tasks with items[].id)"""
    try:
        want = {str(it["id"]) for it in sub["data"]["items"]}
        return {"items": [o for o in prev["items"] if str(o.get("id")) in want]}
    except (KeyError, TypeError, AttributeError):
        return None


def _feedback(errs, prev, partial: bool) -> str:
    s = "\n\n## Your previous answer was rejected by the checker\n" + "\n".join(f"- {e}" for e in errs[:40])
    if partial:
        if prev:
            s += "\nYour previous answer for these items:\n" + json.dumps(prev, ensure_ascii=False, separators=(",", ":"))
        return s + "\nAnswer only the items listed above, fixing these problems."
    if prev is not None:
        s += "\nPrevious answer:\n" + json.dumps(prev, ensure_ascii=False, separators=(",", ":"))
    return s + "\nReturn the complete corrected answer."
