# Prompt · Step 3 — map OCR lines to Chinese (map/pNN.json)

> Run this as a **separate** job from step 2 (fresh context, text + small crops only): reading
> the whole page images again inside the translation context was the single largest cost
> in the first real project.

## Input
- `<WORK>/lines/pNN.tsv` — `id x0 y0 x1 y1 cyr text` (page fractions; `cyr=1` = has Russian).
  OCR text has errors; the image decides what a line says.
- Numbered preview: `python scripts/scan/12_preview.py NN --box x0 y0 x1 y1` (red = Russian).
- Your step-2 file `notes/pNN.json` is the dictionary: same terms, same numbers.

## Output: `<WORK>/map/pNN.json`
```json
{"lines": {"12": "柱布置图", "13": "", "57": "预埋件 МН10"},
 "extra": [{"anchor": 40, "zh": "剖面 2-2"}],
 "appendix": true}
```

## Rules
1. Every `cyr=1` line appears in `lines`.
2. **`""` means "nothing to add here", and it is checked.**  Use it only when
   (a) the line is the continuation of a multi-line label whose *first* line carries the full
   Chinese, (b) it is a person's name, a logo or OCR garbage, or (c) it is a clause of the
   notes block.  Anything else left blank is reported by the coverage audit as `[空映射]`.
3. Numbered notes are **not** labelled line by line: the heading line gets
   `说明(中文译文见右侧)` and `"appendix": true`; the engine sets the whole Chinese block from
   `notes/pNN.json` next to them (free space or a widened right column, clause by clause).
4. **Do not write count labels** (`4根`, `间距200`): every `шт./pcs.N` and `ш./sp.N` is found
   and labelled by the engine from the OCR word boxes, and a legend line explains them.
5. Member marks keep their dot and suffix (`柱 Км1.2`, `柱 Км2.4-1`); `50_check.py` fails the
   build on a mark that is not in the set of sheet titles.
6. Tables: translate every header cell and every name with Russian words; pure specification
   strings (`Ø12 А500С L=1200`, `ГОСТ 34028-2016`) get `""`.
7. Title block and margin fields (Разраб., Проверил, Изм., Подп., Дата, Взам. инв. № ...)
   get their normal Chinese; the engine moves them into one bilingual legend line by the
   title block instead of squeezing them into the signature cells.
8. Russian that OCR missed: add `extra` with an `anchor` (a neighbouring line id).  Do **not**
   estimate boxes by eye — the coverage audit (step 5) finds missed text and gives exact boxes.
9. Keep the Chinese short; explanations belong in the notes file.
