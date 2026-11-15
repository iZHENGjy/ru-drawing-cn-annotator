"""project.toml schema: every table and key with its type and default, checked when a project loads.

A wrong type or an unknown language stops the run with a message that names the key and says how to
fix it; an unknown key (often a typo) is a warning with the closest known key.  The dataclasses that
hold the values are ``config.textroute.Config``, ``config.scanroute.ScanConfig`` and ``config.job.JobConfig``.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field, fields
from typing import Any

STR, INT, FLOAT, BOOL, LIST, DICT = "string", "integer", "number", "true/false", "list", "table"
_PY = {STR: (str,), INT: (int,), FLOAT: (int, float), BOOL: (bool,), LIST: (list,), DICT: (dict,)}


@dataclass(frozen=True)
class Key:
    type: str
    help: str = ""
    choices: tuple = ()


def _scan_keys() -> dict[str, Key]:
    from drawtrans.config.scanroute import ScanConfig
    kind = {str: STR, int: INT, float: FLOAT, bool: BOOL, list: LIST}
    out = {}
    for f in fields(ScanConfig):
        if f.name in ("root", "names"):
            continue
        t = f.type if isinstance(f.type, type) else None
        name = str(f.type)
        ty = kind.get(t) if t else (LIST if name.startswith("list") else BOOL if name == "bool" else INT if name == "int"
                                    else FLOAT if name == "float" else STR)
        out[f.name] = Key(ty or STR)
    out["pdf"] = out["work"] = out["output"] = out["font"] = out["tesseract"] = out["tessdata"] = Key(STR)
    out["output_standard"] = Key(INT, "1 = approved legacy layout, 2 = docs/output_standard.md, 3 = v2 (§8, layout.policy)", (1, 2, 3))
    return out


def schema() -> dict[str, dict[str, Key]]:
    from drawtrans import languages
    langs = tuple(sorted(languages.PROFILES))
    return {
        "project": {"name": Key(STR), "pdf": Key(STR), "dxf": Key(STR), "tm": Key(STR), "glossaries": Key(LIST),
                    "review_dir": Key(STR), "output_dir": Key(STR), "extract_dir": Key(STR), "font": Key(STR),
                    "notes_pages": Key(STR, 'pages of running text, e.g. "5-30,41-60"'),
                    "output_standard": Key(INT, "text route: 1 legacy placer, 3 = v2 (§8)", (1, 2, 3)),
                    "source_lang": Key(STR, "ISO 639-1 code of the source language", langs),
                    "target_lang": Key(STR, "ISO 639-1 code of the target language", langs)},
        "title_block": {"anchors": Key(LIST), "left_stamp_width": Key(FLOAT)},
        "keep": {"names": Key(LIST), "acronyms": Key(LIST)},
        "segmentation": {"words": Key(LIST)},
        "volume": {"sections": Key(LIST)},
        "scan": _scan_keys(),
        "llm": {"backend": Key(STR, "", ("claude-cli", "anthropic", "fake")), "concurrency": Key(INT),
                "budget_usd": Key(FLOAT), "background": Key(STR), "timeout_s": Key(INT), "max_retries": Key(INT),
                "models": Key(DICT, "model alias per task: phrase_translate, page_notes, audit_review, visual_review")},
        "tm": {"project": Key(STR), "shared": Key(LIST), "glossaries": Key(LIST)},
        "job": {"route": Key(STR, "", ("auto", "scan", "text")), "max_audit_loops": Key(INT), "visual": Key(BOOL),
                "visual_max_tiles": Key(INT), "qa": Key(BOOL), "batch_size": Key(INT),
                "english": Key(STR, "", ("keep", "replace")), "batch": Key(STR), "optimize": Key(BOOL),
                "cheap_model": Key(STR), "cheap_short_phrases": Key(BOOL)},
        "output": {"appendix": Key(BOOL), "compact": Key(BOOL)},
        "guard": {"mark_regex": Key(STR, "regex with two groups: letters, number"), "std_regex": Key(STR)},
    }


class ConfigError(ValueError):
    """project.toml does not validate; ``problems`` lists every error."""

    def __init__(self, path: Any, problems: list[str]):
        self.problems = problems
        super().__init__(f"{path}: {len(problems)} problem(s) in project.toml\n  - " + "\n  - ".join(problems))


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check(raw: dict) -> Report:
    """Type, choice and unknown-key check of a parsed project.toml."""
    rep = Report()
    sch = schema()
    for table, body in (raw or {}).items():
        if table not in sch:
            near = difflib.get_close_matches(table, list(sch), 1)
            rep.warnings.append(f"[{table}] is not a known table" + (f" (did you mean [{near[0]}]?)" if near else ""))
            continue
        if not isinstance(body, dict):
            rep.errors.append(f"[{table}] must be a table, e.g.  [{table}]  on its own line")
            continue
        for k, v in body.items():
            key = sch[table].get(k)
            if key is None:
                near = difflib.get_close_matches(k, list(sch[table]), 1)
                rep.warnings.append(f"[{table}] {k}: unknown key, ignored" + (f" (did you mean {near[0]}?)" if near else ""))
                continue
            ok = isinstance(v, _PY[key.type]) and not (key.type in (INT, FLOAT) and isinstance(v, bool))
            if not ok:
                rep.errors.append(f"[{table}] {k} = {v!r}: expected a {key.type}" + (f" ({key.help})" if key.help else ""))
            elif key.choices and v not in key.choices:
                rep.errors.append(f"[{table}] {k} = {v!r}: must be one of {', '.join(map(str, key.choices))}")
    return rep


def validate(raw: dict, path: Any = "project.toml", warn=print) -> None:
    """Raise ConfigError listing every error; print warnings (unknown keys)."""
    rep = check(raw)
    for w in rep.warnings:
        warn(f"warning: {path}: {w}")
    if rep.errors:
        raise ConfigError(path, rep.errors)
