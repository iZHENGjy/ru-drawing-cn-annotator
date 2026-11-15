"""``python -m drawtrans`` = the ``drawtrans`` command (doctor | init | run | status | cost)."""
import sys

from drawtrans.pipeline.cli import main

if __name__ == "__main__":
    sys.exit(main())
