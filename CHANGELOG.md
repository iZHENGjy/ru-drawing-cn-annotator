# Changelog

## 0.3.0 — layout standard v2

- One placement engine for both routes (`layout/core.py`, `layout/policy.py`): candidates → hard filter →
  score → tiers → leader → numbered column; `[scan] / [project] output_standard = 3`, default for new projects.
- Printed sizes (A3 drawings, A4 documents): no label below 6 pt, ≤ 5 % below 7 pt per page; shrinking ≤ 0.8 ×.
- No translation on a rule or on source ink (150 dpi raster, dashed rules, no inset); same thresholds in QA.
- Leaders ≤ 5 per page, ≤ 3 % of labels, mean ≤ 15 pt, never across source text; end points on the box edges.
- Approval stamp: one line outside the stamp ≥ 7 pt; title-block legend above the title block ≥ 6.5 pt.
- Tables: rows that leave their cells are listed in the column between the row's continued rules.
- QA: printed-size, occlusion and leader rules in the gate; visual review sampled evenly per page; per-page
  layout hashes in the metrics; page cache keyed on the engine files.
- Earlier deliveries (standard 1/2) rebuild byte for byte.

## 0.2.0 — multilingual, restructured

### Structure
- Installable package `src/drawtrans/` split by domain (`languages`, `config`, `ocr`, `segment`, `memory`,
  `llm`, `layout`, `render`, `qa`, `pipeline`, `cad`); command `drawtrans` (`rudraw` kept). The numbered
  scripts became modules (`python -m drawtrans.…`); `scripts/` keeps thin compatibility entry points.
- `project.toml` checked at load (`config/schema.py`): wrong types and unknown languages stop with a message
  naming the key; unknown keys warn with the closest known key.
- Shared records in `records.py`; one page-range parser; repository paths in `paths.py`.
- ruff, mypy (typed core), pre-commit, CI on Linux and Windows; tests in unit / e2e / regression layers.
- Docs: `docs/architecture.md`, `CONTRIBUTING.md`, this changelog.

### Languages
- `[project] source_lang / target_lang` (ru, en, kk, uk, de, fr, zh). Default `ru → zh` unchanged.
- OCR models, source-text detection, look-alike repair, fonts, line breaking, prompts, title-block words,
  legend words and code guards follow the pair; glossaries in `glossary/<src>-<tgt>/`.
- Synthetic demos `demo/multilang/` (en → zh scan, zh → en text layer).

### Rework prevention
- Samples first: one page per kind as PNG before the whole volume (`--approve-samples`, `--no-samples`).
- Output standard checked by code (`qa/standard.py`, gate `output_standard`).

### Speed
- RapidOCR in parallel processes; build, audit and QA per page in a process pool; build and QA cached per
  page (only changed pages are redone); the second audit loop asks only about new candidates.

### Cost (`[job] optimize`, on for new projects)
- Repeated notes blocks translated once; title-block-only and reused sheets on the cheap model; real text
  layers sent as text; fixed texts listed as known; bad labels dropped instead of a paid retry.
- Always: mechanical answer fixes before validation; prompt-cache warm-up.

### Quality (`[scan] quality_rules`, on for new projects)
- Labels over 2–3 lines translated as one phrase; «Лист» without dimensions is the sheet field (页).
- Volume terminology consistency report (`work/review/consistency.tsv`).

## 0.1.0
- Russian → Chinese drawing annotator: text-layer route, scan route with the `rudraw` pipeline, DWG/DXF route,
  extension column and output standard (PR #3).
