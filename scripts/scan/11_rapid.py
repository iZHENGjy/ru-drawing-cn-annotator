"""Step 1b (recommended) — second text detector, RapidOCR (PP-OCRv5, East-Slavic model).

    pip install rapidocr onnxruntime
    python scripts/scan/11_rapid.py [--config project.toml] [--pages 3,4] [--threads 3]

Tesseract misses vertical text, text on hatching and many short labels; RapidOCR finds most
of them.  Its boxes are used (a) by the coverage audit to find Russian that has no Chinese,
(b) to anchor шт./pcs and ш./sp labels.  Output: work/rapid/pNN.tsv
(x0 y0 x1 y1 text score rotated W H — pixels of the 300 dpi render).
Run several processes in parallel for large sets (each page is tiled in 2000 px squares).
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scan import ctx as C  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--pages")
    ap.add_argument("--threads", type=int, default=3, help="onnxruntime intra-op threads")
    a = ap.parse_args()
    try:
        import numpy as np
        from PIL import Image
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
    except ImportError:
        sys.exit("pip install rapidocr onnxruntime pillow")
    Image.MAX_IMAGE_PIXELS = None
    cfg = C.load(a.config)
    ctx = C.Ctx(cfg); ctx.mkdirs()
    eng = RapidOCR(params={"Rec.lang_type": LangRec.ESLAV, "Rec.ocr_version": OCRVersion.PPOCRV5,
                           "Rec.model_type": ModelType.MOBILE, "Global.log_level": "error",
                           "Global.max_side_len": 2400, "Global.text_score": 0.3,
                           "EngineConfig.onnxruntime.intra_op_num_threads": a.threads, "Global.use_cls": True})

    def iou(p, q):
        ix = max(0, min(p[2], q[2]) - max(p[0], q[0])); iy = max(0, min(p[3], q[3]) - max(p[1], q[1]))
        return ix * iy / min((p[2] - p[0]) * (p[3] - p[1]) + 1e-9, (q[2] - q[0]) * (q[3] - q[1]) + 1e-9)

    pngs = sorted(ctx.w.joinpath("png").glob("p*.png"))
    want = None
    if a.pages:
        want = set()
        for part in a.pages.split(","):
            x, _, y = part.partition("-"); want |= set(range(int(x), int(y or x) + 1))
    for png in pngs:
        pg = int(png.stem[1:])
        if (want and pg not in want) or ctx.rapid(pg).exists():
            continue
        t0 = time.time()
        im = Image.open(png).convert("RGB"); Wd, Ht = im.size
        T, O = 2000, 250; boxes = []
        for y in range(0, Ht, T - O):
            for x in range(0, Wd, T - O):
                r = eng(np.array(im.crop((x, y, min(x + T, Wd), min(y + T, Ht)))))
                if r.boxes is None:
                    continue
                for b, txt, sc in zip(r.boxes, r.txts, r.scores):
                    b = np.array(b)
                    boxes.append([b[:, 0].min() + x, b[:, 1].min() + y, b[:, 0].max() + x, b[:, 1].max() + y, txt, float(sc)])
        ded = []
        for b in sorted(boxes, key=lambda b: -b[5] * ((b[2] - b[0]) * (b[3] - b[1])) ** 0.5):
            if all(iou(b, d) < 0.5 for d in ded):
                ded.append(b)
        tmp = ctx.rapid(pg).with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            for b in ded:
                fh.write("\t".join([str(int(v)) for v in b[:4]] + [b[4].replace("\t", " "), f"{b[5]:.3f}", "0", str(Wd), str(Ht)]) + "\n")
        tmp.replace(ctx.rapid(pg))
        print(f"p{pg:02d}: {len(ded)} boxes, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
