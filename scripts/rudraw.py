"""Compatibility entry point: same as the ``drawtrans`` command (``python -m drawtrans``)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    from drawtrans.pipeline.cli import main
    sys.exit(main())
