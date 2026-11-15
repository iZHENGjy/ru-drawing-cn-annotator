"""Compatibility entry point: same as ``python -m drawtrans.ocr.rapid`` (the code lives in src/drawtrans/)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

if __name__ == "__main__":
    from drawtrans.ocr.rapid import main
    sys.exit(main())
