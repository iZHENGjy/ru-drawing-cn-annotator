"""rudraw - one command for a drawing-translation job.

    python scripts/rudraw.py doctor [--config project.toml]
    python scripts/rudraw.py init <folder> --pdf drawings.pdf
    python scripts/rudraw.py run [--from STAGE] [--only STAGE] [--until STAGE] [--pages 1-5] [--force] [--dry-run]
    python scripts/rudraw.py status | cost
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
