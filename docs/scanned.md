# Scanned drawings (no text layer)

The main route (`scripts/20_build.py`) needs a PDF with a text layer.  Many sets arrive as
photocopier scans: 1-bit images, no extractable text.  `scripts/scan/` is the route for
those.  It was built on a real 69-page set (КЖ reinforced-concrete drawings + embedded-items
volume, RU/EN bilingual, A4 to A1×3); no client material is in this repository.

What is different from the text-layer route:

| | text-layer PDF | scan |
|---|---|---|
| where Russian is | glyphs, exact | OCR (Tesseract + RapidOCR), noisy |
| translation unit | translation memory, numbers as ⟨n⟩ | per-page notes + line map, reviewed |
| proof of completeness | Russian word count | **coverage audit**: every detected Russian box must be accounted for |
| proof of layout | geometric QA | geometric checks + **page-by-page visual review** |

## Pipeline

```
project.toml [scan]
   │
10_ocr.py      render 300 dpi, Tesseract rus+eng psm 11 ─► work/ocr, work/lines (numbered)
11_rapid.py    RapidOCR second detector (recommended)    ─► work/rapid
   │
   ├─ translator: careful page translation  ─► work/notes/pNN.json   (docs/scanned/prompt_translate.md)
   └─ translator: line id → Chinese          ─► work/map/pNN.json     (docs/scanned/prompt_map.md)
   │                                           12_preview.py draws the numbered boxes
20_build.py    place Chinese, notes block, legend, TOC    ─► output/<code>_中文对照版.pdf
30_audit.py    coverage audit + review packets           ─► work/packets, "remaining N"
   └─ reviewer answers                                    ─► work/answers/audit/pNN.json
31_apply_audit.py  exact-box additions / accepts          ─► work/map/pNN.fix.json
   (repeat 20 → 30 → 31 until remaining 0)
50_check.py    member-mark gate (+ --digits advisory)
40_tiles.py    tiles of the finished PDF                 ─► work/visual/rN
   └─ visual reviewers                                    ─► work/visual/rN/pNN.json
41_apply_visual.py  snapped additions; illegible/wrong     ─► _todo.tsv for a human
   (rebuild, audit again)
```

Quick start (Tesseract: UB-Mannheim build on Windows, `rus` from tessdata_best;
`pip install rapidocr onnxruntime` for the second detector):

```bash
cd ~/jobs/kzh1                                   # project folder, outside this repo
cp path/to/ru-drawing-cn-annotator/project.example.toml project.toml   # fill in [scan]
R=path/to/ru-drawing-cn-annotator/scripts/scan
python $R/10_ocr.py && python $R/11_rapid.py
#   ... notes/ and map/ from the translator ...
python $R/20_build.py --pages 14                 # try one sheet
python $R/20_build.py && python $R/30_audit.py   # whole set; answer packets; 31_apply_audit.py
python $R/50_check.py
python $R/40_tiles.py --round 1                  # visual review; then 41_apply_visual.py --round 1
```

## What the engine does on a scan (`scan/engine.py`)

- **Ink and line masks** at 72 dpi: text ink (without long rules) and horizontal/vertical
  rules (1 px tolerant to scan skew).  Chinese never covers ink; it may not cross a table
  rule either (a thin column line through a label is rejected even if its area share is tiny).
- **Placement order** for a label: directly below (left-aligned, titles centred, then left /
  right aligned), stepping down past up to 4 blocking lines; inside the table cell (below the
  last text line, or beside it); the **empty neighbouring cell of the same row** (e.g. the
  Remarks column) when the cell is full; then a leader to the nearest free spot.
- **Font size** = OCR line height × 1.0 (≈ 0.92 of the Russian glyph size); title-block and
  margin text capped; never below a readable floor — a label that does not fit at the floor
  goes to a leader instead of shrinking to 2 pt.
- **Word-aware wrapping**: codes and values (`F200`, `Км2.1`, `Ø16`, `А500С`) are never split.
- **шт./pcs.N and ш./sp.N**: found per OCR *word* box (not per line, which mixes stacked
  labels) and labelled 根 / 间距 (`count_word` = 件 for parts volumes) right next to the word;
  the legend line starts with `шт./pcs.=根数; ш./sp.=间距`.
- **Title block and margin fields** (Разраб., Проверил, Подп., Взам. инв. № ...) go into one
  bilingual legend line by the title block, with canonical Russian keys (never OCR garbage);
  if there is no room it goes to a widened column on the right.
- **Numbered notes**: one Chinese block in free space near the Russian, or a widened right
  column aligned clause by clause (by the clause's first words, falling back to its number).
- **Approval stamp** (raster image of known size): its lines merged into one Chinese line.
- Every decision is recorded (`engine.COVER`) for the audit.

## Delivery gates

1. `20_build.py`: no `未放置` (unplaced) in `work/review/build_report.tsv`.
2. `30_audit.py`: `remaining 0` — with `work/rapid` present (without the second detector
   the audit only sees what Tesseract saw).
3. `50_check.py`: exit 0 (every member mark in the Chinese exists in the sheet titles).
4. Visual review of every page: no `漏译`; `遮挡` / `错译` items in `_todo.tsv` resolved.

## Lessons from the first real set

These are why the steps above exist; each one was a visible defect in a delivered draft.

- **"Blank" in the map was the largest single source of omissions.**  Translators set lines
  to `""` as "merged" or "not needed"; hundreds were not.  The audit now treats every blank
  line as a claim to verify.
- **English-only pages got "see page 9" instead of Chinese.**  The prompt now requires a
  notes block on them, aligned to the English clause numbers.
- **Count labels attached per OCR line went to the wrong label** when a line held both
  `ш./sp.150` and `шт./pcs.3`; eyeballed boxes for `4根` drifted.  Labels are now per word.
- **OCR drops decimal points in member marks** (`Км 12` for `Км1.2`) and a translator copying
  OCR text carries it into the Chinese.  `50_check.py` catches it.
- **Small Chinese reads as missing Chinese.**  0.66× of the Russian size was the first
  default; reviewers reported "missing" text that was there at 3 pt.
- **Two OCR engines are needed.**  Tesseract alone missed vertical text and many short labels;
  RapidOCR found 22 of 24 regions a human had to box by eye.
- **Visual review finds what no check finds** (labels next to the wrong item, text crossing
  table lines, unreadable sizes) but costs most: about 2.4 M tokens per full pass over 69
  pages with 11 reviewers.  Review only changed / flagged tiles in later rounds
  (`40_tiles.py --pages`).
- **Cost control**: keep translation, mapping, audit review and visual review in separate
  jobs with fresh context and only the files they need; the review prompts already restrict
  reading to packets / tiles.  Re-reading page images inside a long context was the biggest
  cost of the first run.

## Files of a page

```
work/png/p07.png           render (300 dpi)          work/rapid/p07.tsv   RapidOCR boxes
work/ocr/p07.tsv           Tesseract words           work/lines/p07.tsv   numbered lines
work/notes/p07.json        careful translation       work/map/p07.json    line → Chinese
work/map/p07.fix.json      audit / review additions  work/audit/p07.json  audit result
work/packets/p07.*         audit review packet       work/visual/rN/...   visual review
```
