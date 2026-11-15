# Architecture

`drawtrans` turns a drawing set or a technical document (vector PDF, scanned PDF, DWG/DXF) into a
bilingual edition: the original is never changed, the translation is an added vector layer that
follows [output_standard.md](output_standard.md). This page is the map of the code: modules, data
flow, extension points and the decisions behind them.

## Module map

```
src/drawtrans/
  languages/      language pairs: alphabets, OCR models, fonts, line breaking, prompts, guards (i18n)
  config/         project.toml: schema.py (types, defaults, checks) + the three config objects
                  textroute.Config · scanroute.ScanConfig (+ work-folder paths, OCR file I/O) · job.JobConfig
  ocr/            reading the page: tesseract (render + Tesseract), rapid (RapidOCR), textlayer (PDF words)
  segment/        what needs translating: segments (volume-wide unique phrases, OCR repair, label groups),
                  prose (running-text paragraphs), packets (LLM input packets: crops + OCR text)
  memory/         translation memory: tm (keys with ⟨n⟩ number placeholders), store (glossary < shared < project),
                  automap (OCR line -> translation map), merge / export_todo (text route TM loop)
  llm/            constrained LLM tasks: session (cache, retries, budget, ledger), backends, validate (code guards),
                  tasks/<task>.py + prompts <task>.md / <task>.generic.md
  layout/         where the translation goes: analyse (text-layer words/lines/runs), place (slots, wrapping,
                  fitting, Painter), extend (extension column of running-text pages), scan_place (scan route placer)
  render/         writing PDFs: scan_build / text_build (per-page build, page cache), text_pdf, appendix,
                  placelog (placement record), previews
  qa/             checks: standard (output-standard gate), audit (coverage), marks (member-mark gate), metrics,
                  occlusion, consistency (terminology), regress, determinism, apply_audit / apply_visual
  pipeline/       orchestration: cli (drawtrans …), core (Stage, Job, StageCtx), runner (plan, resume),
                  stages_scan / stages_text (the stage lists), samples (samples first), cost, summary (gates)
  cad/            DWG/DXF route (separate CAD layer instead of a PDF overlay)
  records.py      the records exchanged between modules (TypedDicts)
  paths.py        repository anchors; child_env() for `python -m drawtrans.…` steps
  pagesel.py      "3,7-9" page selections
scripts/          compatibility entry points only (rudraw.py, NN_*.py → python -m drawtrans.…)
glossary/<src>-<tgt>/   public term lists per language pair (data, edited by people)
```

## Data flow

```
PDF ──► ocr ──► OcrLine (work/lines)            ┌──────── llm.page_notes (title block, notes) ───┐
          │                                      │                                                 ▼
          └─► segment ──► Segment (work/seg) ──► memory.store ──► misses ──► llm.phrase_translate ──► TM
                                                                                                   │
                       PageMap (work/map) ◄── memory.automap ◄─────────────────────────────────────┘
                              │
                              ▼
                 layout (scan_place | analyse+place+extend) ──► render ──► output PDF + Placement record
                              │                                                       │
                              ▼                                                       ▼
            qa.audit ─► llm.audit_review ─► qa.apply_audit (map/*.fix.json)    qa.standard / metrics / consistency
```

Every arrow is a file in the work folder with a type in `records.py`, so a run can be resumed from any
stage (`pipeline/state.py` hashes the inputs) and every intermediate result can be inspected.

## Extension points

**A language pair.** Add a `Lang` profile to `languages/__init__.py` (alphabet classes, Tesseract `-l`,
RapidOCR model, standard designations, title-block field names, month names) and a glossary folder
`glossary/<src>-<tgt>/` (list its files in `GLOSSARY_BASE`). Prompts work through `<task>.generic.md`; a
pair that needs its own wording adds `llm/tasks/<task>.<src>-<tgt>.md`. Patterns written for Russian
(`[А-Яа-яЁё]` …) are rewritten for the source alphabet by `languages.rx` / `languages.re_src`, so most
modules need no change. Add a synthetic end-to-end test (see `tests/test_multilang.py`).

**An LLM task.** A module in `llm/tasks/` with `NAME`, `PROMPT_VERSION`, `SCHEMA`, `render(payload)`,
`validate(payload, output)`, `fake(payload)` (optional `normalize`, `retry_payload`, `merge`), a prompt
`<name>.md`, an entry in `tasks.NAMES`, and a stage that calls `StageCtx.session().run(name, payloads, ids)`.
The session gives caching, retries with the validator's errors, the budget stop and the cost ledger.

**A layout rule.** Write the rule in [output_standard.md](output_standard.md), implement it in `layout/`
behind a project switch (the approved layouts must keep their `layout_sha256`), record what the check needs
through `render.placelog.add(...)` and add the check to `qa/standard.py` (`RULES`). The gate then makes every
run that breaks it "not deliverable".

**A stage.** A function `run_x(sc: StageCtx) -> dict` and a `Stage(...)` entry in `stages_scan.py` or
`stages_text.py` (inputs, config keys and outputs decide when it re-runs).

## Benchmarks: what we took and what we did not

| project | taken | not taken, and why |
|---|---|---|
| **OCRmyPDF** | A pipeline of named stages with explicit inputs/outputs and resumable work files; one CLI entry point with subcommands; a `src/` package with the logic in modules and a thin CLI; tests split by speed (unit vs. full runs); a doctor-style dependency check. | Its pluggy plugin system: our stages are few and project-specific, and a plugin layer would add indirection without users who write plugins. Extension points are documented functions and registries instead. |
| **BabelDOC / PDFMathTranslate** | An intermediate representation between "read the PDF" and "write the PDF" (here: OCR lines, segments, page maps, placement records as typed records in `records.py`); translation behind a service interface with a cache (`llm/session.py`, `llm/backends.py`); configuration in one checked model. | Re-typesetting the page: they rebuild the document in the target language, we must leave the original untouched and add a layer (an engineering deliverable is checked against the original). Their general-purpose layout model does not know title blocks, member marks or extension columns. |
| **Argos Translate** | Language pairs as first-class objects (`languages.Pair` with source/target profiles), pair-specific data in its own place (`glossary/<src>-<tgt>/`), pivoting avoided by explicit pairs. | Downloadable model packages: the translation model here is an LLM with constrained tasks; what varies per pair is small (OCR model, alphabet, glossary, prompt wording) and ships with the code. |

## Two placers, one layout package

The text-layer route (`layout/analyse.py` + `place.py` + `extend.py`, drawn with PyMuPDF) and the scan route
(`layout/scan_place.py`, drawn with reportlab and merged with pypdf) both live in `layout/` and share the
language layer (fonts, line breaking), the output standard, the placement record and its gate. They are not
merged into one placer yet: each one reproduces an approved delivery byte for byte (`layout_sha256`), and a
merge would move translated spans on those deliveries. The merge is the next structural step; it needs a new
output-standard version, a visual check of the approved samples and new baselines.

## Regression

- `tests/test_regression_layout.py`: the synthetic demo against `tests/baselines/demo_layout.json` (one value
  per CJK font) and, with `DRAWTRANS_VALIDATION=<folder>`, every approved private set against
  `baselines/approved_<set>.json` (the private folder never enters this repository).
- `python -m drawtrans.qa.metrics OUT.pdf --config project.toml --json run.json` and
  `python -m drawtrans.qa.regress run.json --baseline approved_<set>.json` for a full comparison.

## Code quality

`ruff check src tests scripts` (bug classes and import order; the dense one-line geometry style of the
placers is allowed on purpose), `mypy` on the typed core (`languages`, `config.schema`, `records`, `paths`,
`render.placelog`, `qa.standard`, `pipeline.samples`), `pre-commit` hooks, CI on Linux and Windows with
three test layers (`-m "not e2e and not regression"`, `-m e2e`, `-m regression`).
