"""Compatibility entry point: same as ``python -m drawtrans.render.text_build`` (the code lives in src/drawtrans/)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    from drawtrans.render.text_build import main
    sys.exit(main())
