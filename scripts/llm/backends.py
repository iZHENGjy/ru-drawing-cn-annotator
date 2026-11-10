"""LLM backends.  Each backend has ``call(req) -> Reply`` and never raises for model
errors: failures come back as ``Reply(error=...)`` so the runner can retry and log them.

* ``claude-cli``  — the local Claude Code CLI in print mode (uses the user's login).
* ``anthropic``   — the Messages API (needs ``ANTHROPIC_API_KEY`` and ``pip install anthropic``).
* ``fake``        — deterministic canned responder for tests / dry runs (no network).
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# USD per million tokens: (input, output, cache read, cache write 5m, cache write 1h).
# Checked 2026-11 (claude-api skill model table); cache write = 1.25x / 2x input, read = 0.1x.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50, 4.00),
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00, 8.00),
}
ALIASES = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}


def model_id(model: str) -> str:
    return ALIASES.get(model, model)


def price(model: str):
    return PRICES.get(model_id(model), PRICES["claude-sonnet-5-5"])


def cost_from_usage(model: str, usage: dict, write_ttl: str = "5m") -> float:
    pi, po, pr, pw5, pw1 = price(model)
    pw = pw1 if write_ttl == "1h" else pw5
    return (usage.get("input_tokens", 0) * pi + usage.get("output_tokens", 0) * po
            + usage.get("cache_read_tokens", 0) * pr + usage.get("cache_write_tokens", 0) * pw) / 1e6


@dataclass
class Request:
    task: str
    model: str
    system: str
    text: str
    images: list = field(default_factory=list)      # list[Path]
    schema: dict = field(default_factory=dict)
    timeout: float = 600
    payload: dict | None = None                     # original payload (fake backend only)
    attempt: int = 1


@dataclass
class Reply:
    output: dict | None = None
    cost_usd: float = 0.0
    usage: dict = field(default_factory=dict)       # input/output/cache_read/cache_write tokens
    error: str = ""
    raw: dict | None = None


def norm_usage(u: dict | None) -> dict:
    u = u or {}
    return {"input_tokens": int(u.get("input_tokens") or 0),
            "output_tokens": int(u.get("output_tokens") or 0),
            "cache_read_tokens": int(u.get("cache_read_input_tokens") or u.get("cache_read_tokens") or 0),
            "cache_write_tokens": int(u.get("cache_creation_input_tokens") or u.get("cache_write_tokens") or 0)}


def _image_block(p: Path) -> dict:
    mt = mimetypes.guess_type(str(p))[0] or "image/png"
    return {"type": "image", "source": {"type": "base64", "media_type": mt,
                                         "data": base64.standard_b64encode(Path(p).read_bytes()).decode()}}


# ---------------------------------------------------------------------------------------------
class ClaudeCliBackend:
    """``claude -p`` with the task prompt as system prompt and the answer forced into a JSON
    schema (``structured_output``).  No tools, no MCP, no session files, run in an empty temp dir.
    Text-only calls use ``--output-format json``; calls with images send one stream-json user
    message (text + base64 image blocks) and read the final ``result`` event."""
    name = "claude-cli"

    def __init__(self, exe: str | None = None):
        self.exe = exe or shutil.which("claude")
        if not self.exe:
            raise RuntimeError("claude CLI not found on PATH (backend claude-cli)")

    def argv(self, req: Request, sysfile: Path) -> list[str]:
        a = [self.exe, "-p", "--model", req.model, "--safe-mode", "--strict-mcp-config", "--tools", "",
             "--no-session-persistence", "--system-prompt-file", str(sysfile),
             "--json-schema", json.dumps(req.schema, ensure_ascii=False, separators=(",", ":"))]
        if req.images:
            a += ["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
        else:
            a += ["--output-format", "json"]
        return a

    def call(self, req: Request) -> Reply:
        with tempfile.TemporaryDirectory(prefix="llmcall_") as td:
            sysfile = Path(td) / "system.txt"
            sysfile.write_text(req.system, encoding="utf-8")
            if req.images:
                content = [{"type": "text", "text": req.text}] + [_image_block(p) for p in req.images]
                stdin = json.dumps({"type": "user", "message": {"role": "user", "content": content}},
                                   ensure_ascii=False) + "\n"
            else:
                stdin = req.text
            try:
                pr = subprocess.run(self.argv(req, sysfile), input=stdin.encode("utf-8"), cwd=td,
                                    capture_output=True, timeout=req.timeout)
            except subprocess.TimeoutExpired:
                return Reply(error=f"timeout after {req.timeout:.0f}s")
            out = pr.stdout.decode("utf-8", "replace")
        res = None
        if req.images:
            for line in out.splitlines():
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "result":
                    res = d
        else:
            try:
                res = json.loads(out)
            except ValueError:
                res = None
        if not isinstance(res, dict):
            err = pr.stderr.decode("utf-8", "replace").strip()[-400:]
            return Reply(error=f"claude-cli rc={pr.returncode}: no result JSON. {err}")
        usage = norm_usage(res.get("usage"))
        cost = float(res.get("total_cost_usd") or 0.0)
        if res.get("is_error") or res.get("subtype") != "success":
            return Reply(cost_usd=cost, usage=usage, raw=res,
                         error=f"claude-cli error: {res.get('subtype')} {str(res.get('result'))[:300]}")
        so = res.get("structured_output")
        if so is None:                               # fall back to a JSON text answer
            try:
                so = json.loads(res.get("result") or "")
            except ValueError:
                return Reply(cost_usd=cost, usage=usage, raw=res, error="no structured_output in reply")
        return Reply(output=so, cost_usd=cost, usage=usage, raw=res)


# ---------------------------------------------------------------------------------------------
class AnthropicBackend:
    """Messages API with ``output_config.format`` (JSON schema).  The system prompt is marked for
    prompt caching (5 min), so a batch of calls of one task pays the prompt once."""
    name = "anthropic"

    def __init__(self):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("backend anthropic needs ANTHROPIC_API_KEY")
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise RuntimeError("backend anthropic needs `pip install anthropic`") from e
        import anthropic
        self.client = anthropic.Anthropic(max_retries=2)

    def call(self, req: Request) -> Reply:
        import anthropic
        content = [_image_block(p) for p in req.images] + [{"type": "text", "text": req.text}]
        try:
            r = self.client.with_options(timeout=req.timeout).messages.create(
                model=model_id(req.model), max_tokens=16000,
                system=[{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": content}],
                output_config={"format": {"type": "json_schema", "schema": req.schema}})
        except anthropic.APIError as e:
            return Reply(error=f"anthropic: {type(e).__name__}: {str(e)[:300]}")
        u = r.usage
        usage = {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
                 "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                 "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0}
        cost = cost_from_usage(req.model, usage)
        if r.stop_reason in ("refusal", "max_tokens"):
            return Reply(cost_usd=cost, usage=usage, error=f"anthropic stop_reason={r.stop_reason}")
        txt = "".join(b.text for b in r.content if b.type == "text")
        try:
            return Reply(output=json.loads(txt), cost_usd=cost, usage=usage)
        except ValueError:
            return Reply(cost_usd=cost, usage=usage, error="anthropic: reply is not JSON")


# ---------------------------------------------------------------------------------------------
class FakeBackend:
    """Deterministic, offline.  ``responder(req) -> dict | Reply`` (default: the task's own
    ``fake(payload)`` that returns a schema-valid, conservative answer).  Reports a fixed
    synthetic cost so budget and ledger logic can be tested."""
    name = "fake"

    def __init__(self, responder: Callable | None = None, cost_usd: float = 0.001, delay: float = 0.0):
        self.responder = responder
        self.cost = cost_usd
        self.delay = delay
        self.calls: list[Request] = []

    def call(self, req: Request) -> Reply:
        self.calls.append(req)
        if self.delay:
            time.sleep(self.delay)
        if self.responder is None:
            from .tasks import get
            out = get(req.task).fake(req.payload or {})
        else:
            out = self.responder(req)
        if isinstance(out, Reply):
            return out
        usage = {"input_tokens": len(req.system + req.text) // 3, "output_tokens": len(json.dumps(out)) // 3,
                 "cache_read_tokens": 0, "cache_write_tokens": 0}
        return Reply(output=out, cost_usd=self.cost, usage=usage)


def make(name: str):
    if name == "claude-cli":
        return ClaudeCliBackend()
    if name == "anthropic":
        return AnthropicBackend()
    if name == "fake":
        return FakeBackend()
    raise ValueError(f"unknown llm backend {name!r} (claude-cli | anthropic | fake)")
