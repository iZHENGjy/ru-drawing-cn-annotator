"""Compatibility entry point: same as ``python -m drawtrans.ocr.probe`` (the code lives in src/drawtrans/)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    import runpy
    runpy.run_module("drawtrans.ocr.probe", run_name="__main__", alter_sys=True)
