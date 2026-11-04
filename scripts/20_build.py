"""Build the Chinese-annotated PDF.

usage:
  20_build.py [pages: 10,58,80 | 1-126 | all] [--config project.toml] [--pdf in.pdf]
              [--out name.pdf] [--english keep|replace] [--notes inplace|appendix]
              [--notes-pages 7,8] [--dry]

  --english keep     (default) the original is not modified at all: Chinese is added next to
                     the Russian, English (if any) stays.
  --english replace  bilingual RU/EN sheets: the English text layer is removed glyph by glyph
                     and the Chinese is set in its place.

Reports: <review>/report.csv (per-page QA), <review>/tm_todo.json (untranslated keys)
"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pymupdf
from engine import config
from engine.pdf_build import build, parse_pages
from engine.tm import TM


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pages", nargs="?", default="all")
    ap.add_argument("--config", default=None, help="project TOML (default ./project.toml)")
    ap.add_argument("--pdf", default=None, help="input PDF (overrides the config)")
    ap.add_argument("--tm", default=None, help="project translation memory JSON (overrides the config)")
    ap.add_argument("--out", default=None, help="output file name or path (default <output>/<pdf stem>_CN.pdf)")
    ap.add_argument("--english", default="keep", choices=["keep", "replace"])
    ap.add_argument("--notes", default="inplace", choices=["inplace", "appendix"])
    ap.add_argument("--notes-pages", default=None)
    ap.add_argument("--dry", action="store_true", help="analyse and translate only, write reports")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    cfg = config.load(a.config)
    config.apply(cfg)
    pdf = Path(a.pdf).resolve() if a.pdf else cfg.pdf
    if pdf is None:
        ap.error("no input PDF: pass --pdf or set [project] pdf in project.toml")
    tm = TM(Path(a.tm).resolve() if a.tm else cfg.tm, cfg.glossaries)
    n = pymupdf.open(pdf).page_count
    pages = parse_pages(f"1-{n}" if a.pages == "all" else a.pages, n)
    out = Path(a.out) if a.out else Path(pdf.stem + "_CN.pdf")
    if not out.is_absolute() and len(out.parts) == 1:
        out = cfg.output / out
    notes_pages = a.notes_pages if a.notes_pages is not None else cfg.notes_pages
    build(pdf, pages, str(out), tm, cfg.review, notes_mode=a.notes,
          notes_pages=set(parse_pages(notes_pages, n)) if notes_pages else set(),
          dry=a.dry, english=a.english, tb_anchors=cfg.tb_anchors, left_stamp=cfg.left_stamp)


if __name__ == "__main__":
    main()
