"""`rudraw doctor`: environment check with an OK / WARN / FAIL table."""
from __future__ import annotations
import importlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REQUIRED_MODULES = [("pymupdf", "pymupdf"), ("PIL", "pillow"), ("numpy", "numpy"), ("pypdf", "pypdf"),
                    ("reportlab", "reportlab"), ("jieba", "jieba"), ("ezdxf", "ezdxf"), ("pdfplumber", "pdfplumber")]
OPTIONAL_MODULES = [("rapidocr", "rapidocr (second OCR detector, recommended for scans)"),
                    ("onnxruntime", "onnxruntime (needed by rapidocr)"), ("anthropic", "anthropic (only for backend = anthropic)")]
FONTS = ["C:/Windows/Fonts/Deng.ttf", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/msyh.ttc",
         "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Light.ttc",
         "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"]
BEST_MIN_BYTES = 10 * 1024 * 1024    # tessdata_best rus/eng are ~15 MB, tessdata_fast ~3-4 MB


def _row(rows, name, status, detail):
    rows.append((name, status, detail))


def find_tesseract(explicit=None) -> str | None:
    for c in (explicit, os.environ.get("TESSERACT"), shutil.which("tesseract"),
              r"C:\Program Files\Tesseract-OCR\tesseract.exe"):
        if c and Path(c).exists():
            return str(c)
    return None


def _tessdata_dirs(cfg, tess):
    dirs = []
    if cfg is not None and getattr(cfg, "route", "") == "scan" and cfg.scan.tessdata:
        dirs.append(Path(cfg.scan.tessdata) if Path(cfg.scan.tessdata).is_absolute() else cfg.root / cfg.scan.tessdata)
    if os.environ.get("TESSDATA_PREFIX"):
        p = Path(os.environ["TESSDATA_PREFIX"])
        dirs += [p, p / "tessdata"]
    if tess:
        dirs.append(Path(tess).parent / "tessdata")
    return dirs


def tessdata_variant(size: int) -> str:
    return "best" if size >= BEST_MIN_BYTES else "fast"


def run_checks(cfg=None) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    route = getattr(cfg, "route", None)
    need_ocr = route in (None, "scan")          # no config: check everything as if a scan job
    soft = "FAIL" if need_ocr else "WARN"

    v = sys.version_info
    _row(rows, "python", "OK" if v >= (3, 11) else "FAIL", f"{v.major}.{v.minor}.{v.micro} (need 3.11+)")
    for mod, label in REQUIRED_MODULES:
        if importlib.util.find_spec(mod):
            try:
                ver = getattr(importlib.import_module(mod), "__version__", "")
            except Exception as e:                      # installed but broken
                _row(rows, f"module {mod}", "FAIL", f"import error: {e}")
                continue
            _row(rows, f"module {mod}", "OK", str(ver))
        else:
            _row(rows, f"module {mod}", "FAIL", f"missing: pip install {label}")
    for mod, label in OPTIONAL_MODULES:
        if importlib.util.find_spec(mod):
            _row(rows, f"module {mod}", "OK", "installed")
        else:
            _row(rows, f"module {mod}", "WARN", f"optional, not installed: {label}")

    tess = find_tesseract(cfg.scan.tesseract if route == "scan" else None)
    if tess:
        try:
            out = subprocess.run([tess, "--version"], capture_output=True, text=True, timeout=20)
            ver = ((out.stdout or out.stderr).splitlines() or ["?"])[0]
        except Exception as e:
            ver = f"version check failed: {e}"
        _row(rows, "tesseract", "OK", f"{tess}  ({ver})")
    else:
        _row(rows, "tesseract", soft, "not found: install it (UB Mannheim build on Windows) or set $TESSERACT / [scan] tesseract")
    dirs = _tessdata_dirs(cfg, tess)
    for lang in ("rus", "eng"):
        f = next((d / f"{lang}.traineddata" for d in dirs if (d / f"{lang}.traineddata").is_file()), None)
        if f is None:
            _row(rows, f"tessdata {lang}", soft if lang == "rus" else "WARN",
                 f"{lang}.traineddata not found in {[str(d) for d in dirs] or 'any known folder'}")
        else:
            size = f.stat().st_size
            var = tessdata_variant(size)
            st = "OK" if var == "best" or lang == "eng" else "WARN"
            note = "" if var == "best" else "  (fast model: small drawing text is read worse; use tessdata_best)"
            _row(rows, f"tessdata {lang}", st, f"{var}, {size // 1024} KB, {f}{note}")

    font = next((c for c in ((cfg.scan.font if route == "scan" else (cfg.raw.get("project", {}).get("font") if cfg else None)),
                             os.environ.get("RUDRAW_FONT"), *FONTS) if c and Path(c).is_file()), None)
    if font:
        _row(rows, "chinese font", "OK", font)
    else:
        _row(rows, "chinese font", soft, "none found: set RUDRAW_FONT or font = ... (text route falls back to PyMuPDF's built-in CJK font)")

    backend = (cfg.llm["backend"] if cfg else "claude-cli")
    cli = shutil.which("claude")
    key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    s_cli = "OK" if cli else ("FAIL" if backend == "claude-cli" else "WARN")
    _row(rows, "claude CLI", s_cli, cli or "not on PATH (backend claude-cli needs it)")
    s_key = "OK" if key else ("FAIL" if backend == "anthropic" else "WARN")
    _row(rows, "ANTHROPIC_API_KEY", s_key, "set" if key else "not set (only needed for backend = anthropic)")
    if backend not in ("claude-cli", "anthropic", "fake"):
        _row(rows, "llm backend", "FAIL", f"unknown backend {backend!r}")

    if cfg is not None:
        pdf = cfg.pdf
        if pdf is None:
            _row(rows, "input pdf", "FAIL", "no pdf configured ([project] pdf / [scan] pdf)")
        else:
            _row(rows, "input pdf", "OK" if Path(pdf).is_file() else "FAIL", str(pdf))
        base = Path(cfg.work_dir)
    else:
        base = Path.cwd()
    probe_dir = base if base.exists() else base.parent if base.parent.exists() else Path.cwd()
    try:
        with tempfile.NamedTemporaryFile(dir=probe_dir, prefix=".rudraw_probe_", delete=True):
            pass
        _row(rows, "write access", "OK", str(probe_dir))
    except OSError as e:
        _row(rows, "write access", "FAIL", f"{probe_dir}: {e}")
    return rows


def render(rows) -> str:
    w = max(len(r[0]) for r in rows)
    lines = [f"{'check'.ljust(w)}  status  detail", "-" * (w + 40)]
    lines += [f"{n.ljust(w)}  {s.ljust(6)}  {d}" for n, s, d in rows]
    cnt = {k: sum(1 for r in rows if r[1] == k) for k in ("OK", "WARN", "FAIL")}
    lines.append(f"\n{cnt['OK']} OK, {cnt['WARN']} WARN, {cnt['FAIL']} FAIL")
    return "\n".join(lines)


def doctor(cfg=None, out=print) -> int:
    rows = run_checks(cfg)
    out(render(rows))
    return 1 if any(r[1] == "FAIL" for r in rows) else 0
