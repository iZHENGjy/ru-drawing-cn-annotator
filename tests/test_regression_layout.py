"""Layout regression: a build must reproduce the stored ``layout_sha256`` (placement of every translated span).

1. Public: the synthetic demo sheet (demo/), baseline in tests/baselines/demo_layout.json, one value per
   CJK font file (the font decides the glyph widths, so Windows and Linux differ).  A font without a stored
   value is skipped; ``DRAWTRANS_UPDATE_BASELINE=1`` records it (check the PDF first).
2. Private: set ``DRAWTRANS_VALIDATION`` to a folder with ``baselines/approved_<set>.json`` and the approved
   project folders (see docs/architecture.md "Regression"); every approved set is rebuilt and compared.
   Without it the test is skipped (client data never lives in this repository).
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT
from drawtrans.paths import child_env

pytestmark = pytest.mark.regression
BASE = ROOT / "tests" / "baselines" / "demo_layout.json"


def _fingerprint(pdf):
    import pymupdf

    from drawtrans.qa import metrics as QM
    return QM.text_fingerprints(pymupdf.open(pdf))


def test_demo_layout_baseline(tmp_path):
    from drawtrans.layout import place
    demo = tmp_path / "demo"
    shutil.copytree(ROOT / "demo", demo, ignore=shutil.ignore_patterns("work", "multilang"))
    shutil.copytree(ROOT / "glossary", tmp_path / "glossary")
    r = subprocess.run([sys.executable, "-m", "drawtrans.render.text_build", "all", "--config", "project.toml",
                        "--english", "keep"], cwd=demo, env=child_env(), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    pdf = next(p for p in (demo / "work" / "output").glob("*.pdf"))
    text_sha, layout_sha = _fingerprint(pdf)
    font = Path(place.FONT_FILE or "builtin-cjk").name
    base = json.loads(BASE.read_text(encoding="utf-8")) if BASE.exists() else {}
    if os.environ.get("DRAWTRANS_UPDATE_BASELINE"):
        base[font] = {"layout_sha256": layout_sha, "text_sha256": text_sha}
        BASE.parent.mkdir(parents=True, exist_ok=True)
        BASE.write_text(json.dumps(base, indent=1, sort_keys=True), encoding="utf-8")
    if font not in base:
        pytest.skip(f"no stored demo baseline for font {font} (DRAWTRANS_UPDATE_BASELINE=1 records one)")
    assert layout_sha == base[font]["layout_sha256"], "translated spans moved: check the demo PDF, then update"
    assert text_sha == base[font]["text_sha256"]


@pytest.mark.skipif(not os.environ.get("DRAWTRANS_VALIDATION"), reason="private validation data not configured")
def test_private_approved_layouts(tmp_path):
    val = Path(os.environ["DRAWTRANS_VALIDATION"])
    sets = sorted(p for p in (val / "approved").glob("*") if (p / "project.toml").exists())
    assert sets, f"{val}/approved/<set>/project.toml expected"
    for s in sets:
        base = json.loads((val / "baselines" / f"approved_{s.name}.json").read_text(encoding="utf-8"))
        r = subprocess.run([sys.executable, "-m", "drawtrans.render.scan_build", "--config", "project.toml"], cwd=s,
                           env=child_env(), capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
        pdf = next(p for p in (s / "output").glob("*_中文对照版.pdf"))
        assert _fingerprint(pdf)[1] == base["layout_sha256"], s.name


@pytest.mark.e2e
def test_demo_text_route_v2_passes_the_standard(tmp_path):
    """the synthetic Russian sheet built by the text route under output standard 3 passes the v2 gate."""
    from drawtrans.qa import standard as QS
    demo = tmp_path / "demo"
    shutil.copytree(ROOT / "demo", demo, ignore=shutil.ignore_patterns("work", "multilang"))
    shutil.copytree(ROOT / "glossary", tmp_path / "glossary")
    cfg = (demo / "project.toml").read_text(encoding="utf-8").replace("[project]\n", "[project]\noutput_standard = 3\n", 1)
    (demo / "project.toml").write_text(cfg, encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "drawtrans.render.text_build", "all", "--config", "project.toml",
                        "--english", "keep"], cwd=demo, env=child_env(), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    res = QS.check_file(next((demo / "work" / "output").glob("*.placements.json")))
    assert res["ok"], res["violations"][:5]
    items = __import__("json").loads(next((demo / "work" / "output").glob("*.placements.json")).read_text(encoding="utf-8"))["items"]
    v3 = [x for x in items if x.get("std") == 3 and x.get("role") == "label"]
    assert v3 and all(x["fs"] * x["ps"] >= 6.0 - 1e-6 for x in v3)
