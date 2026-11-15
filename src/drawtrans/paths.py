"""Filesystem anchors of the package (one place instead of ``Path(__file__).parents[n]`` everywhere)."""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent          # src/drawtrans
SRC = PACKAGE.parent                                # src
REPO = SRC.parent                                   # repository root (glossary/, docs/, demo/)
GLOSSARY = REPO / "glossary"


def child_env(**extra: str) -> dict[str, str]:
    """Environment for a ``python -m drawtrans.…`` child process: the package importable without install."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **extra)
    env["PYTHONPATH"] = os.pathsep.join([str(SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env
