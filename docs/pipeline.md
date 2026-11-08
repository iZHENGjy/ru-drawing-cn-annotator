# Production pipeline (`rudraw`)

One command takes a PDF to a bilingual Chinese edition: `scripts/rudraw.py` runs the stages,
keeps resumable state, calls the language model only inside four constrained tasks, tracks the
cost, and checks the delivery gates. The scripts it drives (`scripts/scan/*.py`, `20_build.py`,
...) are the ones described in [scanned.md](scanned.md) and the README; you can still run them by hand.

## Quick start

```bash
python scripts/rudraw.py doctor                              # python deps, Tesseract + rus data, font, claude CLI
python scripts/rudraw.py init ~/jobs/vol1 --pdf vol1.pdf     # writes project.toml, detects scan vs text layer
cd ~/jobs/vol1
python scripts/rudraw.py run --dry-run                       # plan + estimated number of LLM calls
python scripts/rudraw.py run                                 # all stages, resumable
python scripts/rudraw.py status                              # stage table
python scripts/rudraw.py cost                                # spend per stage / task / page
```

Use the path of this repository for `scripts/rudraw.py`. Every command takes `--config project.toml`
(default `./project.toml`). `init --route auto|scan|text` overrides the detection; `--name` and `--code`
set the title and the short output tag; `--force` overwrites an existing `project.toml`.

Exit codes of `run`: `0` all stages ok and all gates passed, `1` a stage failed, `2` stages ran but a
delivery gate failed (see "Delivery gates").

## project.toml

Besides `[project]`, `[scan]` and `[keep]` (see [project.example.toml](../project.example.toml)):

### `[llm]`

| key | default | meaning |
|---|---|---|
| `backend` | `"claude-cli"` | `claude-cli` runs headless `claude -p` with structured output and reports the exact cost per call; `anthropic` uses the API (`pip install anthropic`, `ANTHROPIC_API_KEY`); `fake` is an offline stub for tests |
| `concurrency` | `4` | parallel calls |
| `budget_usd` | `20` | hard stop: no new call is made when ledger total + estimate would exceed it |
| `background` | `""` | project background given to the translator (keep it private; part of the cache key) |
| `timeout_s`, `max_retries` | `600`, `2` | per-call timeout; validator retries (capped at 2) |
| `[llm.models]` | sonnet, sonnet, haiku, sonnet | model alias per task: `phrase_translate`, `page_notes`, `audit_review`, `visual_review` |

### `[tm]`

| key | default | meaning |
|---|---|---|
| `project` | `"tm/project.json"` | the TM this run reads and writes (relative to `project.toml`) |
| `shared` | `[]` | read-only TMs of earlier volumes of the same project family |
| `glossaries` | `[]` | extra read-only glossaries. The public `glossary/gost_general.json` and `glossary/spds_sheet_frame.json` (sheet frame, title block, specification headers) are always added |

### `[job]`

| key | default | meaning |
|---|---|---|
| `route` | `"auto"` | `auto` (scan if a `[scan]` table exists) / `scan` / `text` |
| `batch_size` | `60` | phrases per `phrase_translate` call |
| `max_audit_loops` | `2` | audit_review, apply, rebuild, re-audit at most this many times |
| `visual` | `false` | run the LLM visual review of QA-flagged tiles |
| `visual_max_tiles` | `60` | cap on tiles sent to the visual review (cost guard) |
| `qa` | `true` | run `scripts/qa` after the build |

### `[output]`

| key | meaning |
|---|---|
| `appendix` | append a Russian-to-Chinese reference table of the materials and standards found in the notes (`glossary/ref_materials.json`, `ref_standards.json`; the notes in those files need an engineer's review) |
| `compact` | leave out tiny count labels (根/件) and the `шт./pcs.` legend entries |

The same as `20_build.py --appendix` / `--compact`. Low-confidence Chinese is drawn with a dotted
underline, and one legend sentence in the title block explains it.

## Stages (scan route)

| stage | LLM | what it does | main outputs |
|---|---|---|---|
| `ocr` | no | render 300 dpi, Tesseract rus+eng | `work/png`, `work/ocr`, `work/lines` |
| `rapid` | no | RapidOCR second detector | `work/rapid` |
| `segments` | no | collect the unique phrases of the volume (dedupe, normalisation, fuzzy merge) | `work/seg/segments.json` |
| `tm` | no | layered TM lookup, keep/skip rules; what is left are the misses | `work/job/tm_misses.json` |
| `translate` | `phrase_translate` | translate the misses in batches; validated answers go into the project TM | `tm/project.json` |
| `notes` | `page_notes` | title block, numbered notes and number checks from image crops + OCR | `work/job/notes_results.json` |
| `automap` | no | line to Chinese from the TM with the digit guard; writes `notes/` and `map/` (+ `.meta.json` confidence sidecar) | `work/notes`, `work/map` |
| `build` | no | placement engine, notes block, legend, TOC (`20_build.py`) | `output/*.pdf`, `build_report.tsv` |
| `audit` | no | coverage audit + review packets (`30_audit.py`) | `work/packets`, `remaining N` |
| `audit_review` | `audit_review` | only if `remaining > 0`: answer the packets, apply, rebuild, re-audit | `work/answers/audit`, `map/*.fix.json` |
| `check` | no | member-mark gate (`50_check.py`) | exit status |
| `qa` | no | metrics and occlusion flags (`scripts/qa`) | flagged pages and tiles |
| `visual` | `visual_review` | only if `[job] visual = true`; QA-flagged tiles only; report-only | `work/visual/qa/_todo.tsv` |
| `report` | no | gates, stage table, cost | `work/job/run_summary.json`, `cost_report.md` |

The text-layer route has its own shorter list (`extract`, `build_dry`, `export_todo`, `translate`,
`merge_tm`, `build`, `issues`, `report`); its `translate` stage is still a stub that reports the todo
count and merges `review/batch.tsv` when you provide one.

## The four LLM tasks

The model never sees a whole sheet and never writes the map. Each task has a fixed input packet, a
JSON Schema, a code validator, up to two retries (the validator errors are appended to the prompt),
a replay cache and a cost-ledger row. An answer that fails validation is neither used nor cached.
Prompts live in `scripts/llm/tasks/*.md`.

| task | input packet | answer (schema) |
|---|---|---|
| `phrase_translate` | up to 60 items `{id, key (numbers as ⟨n⟩), sample (raw OCR), ctx [line above, below], n}` + glossary/TM hits + background | `{items: [{id, action: translate\|keep\|skip, zh, conf: high\|low}]}`; `keep` = names/codes, `skip` = OCR garbage |
| `page_notes` | one page: crops of the title block and the notes regions, their OCR lines, "number check" snippets | `{sheet_code, format, title_ru, title_zh, notes: [{no, ru, zh}], labels: [{ru, zh}], issues}` |
| `audit_review` | one audit packet (text + crops) with its candidate references | `{items: [{ref, act: tr\|ok\|skip, ru, zh, why}]}`, applied by `31_apply_audit.py` |
| `visual_review` | tiles flagged by QA with their OCR lines and flags | `{checked, issues: [{tile, type, text, where, zh}]}` |

**Validator rules (code, not prompt).** All tasks: answer shape matches the schema, ids/refs/tiles
complete and unique. For every Chinese string: the `⟨n⟩` placeholders must equal those of the key;
**no bare digit** may appear (numbers come only from the source); numbers, member marks (`Км1.2`),
steel/concrete grades and standard numbers must occur in the source text; no Cyrillic except copied
codes; no decimal comma; no count-only labels. A `translate` answer needs Chinese, `ok`/`skip` need a
reason, and `where` boxes must lie inside the tile.

**Digit guard.** A number reaches the drawing only if Tesseract and RapidOCR agree on it for that box
(or `page_notes` read the same label from the image). Otherwise the number is dropped from the
Chinese (the Russian stays visible), the line is marked low-confidence and drawn with the dotted
underline. Member marks must still pass `50_check`.

## Translation memory layering

Lookup order, weakest to strongest: **glossary < shared < project**. A more specific layer wins, and
an exact match in any layer beats a fuzzy one. Glossary entries marked `@tb` / `@body` apply only in
the title block or the body; `@ambiguous` ones hit with low confidence. Fuzzy matching tolerates OCR
noise (letters-only skeleton, at most 12 % edits, identical number signature). Entries with
`src = human` are never overwritten by the LLM or by rules.

To reuse one volume for the next, list the finished volume's project TM in the next project's
`[tm] shared`:

```toml
[tm]
project = "tm/project.json"
shared  = ["../vol1/tm/project.json"]     # read-only; identical phrases are not translated again
```

To promote a TM to the shared layer, keep the reviewed `tm/project.json` of a volume (or copy it to a
common private folder) and reference it from the other projects. Correct mistakes once in that file
with `src = human` entries and every project that lists it picks them up. A shared TM is plain data
and can live outside this repository. Only phrases found in no layer reach `translate`, so each
further volume of a set is cheaper.

## Cost control

- Every call is a row in `work/job/ledger.jsonl` (task, model, tokens, exact cost, cached flag).
  `rudraw cost` writes `work/job/cost_report.md` and `.json`: per stage, task and page, and the money
  saved by replay.
- `[llm] budget_usd` is a hard stop. A refused call fails its item (error `budget`), the stage reports
  it, and a re-run continues; finished batches replay for free.
- Start with `run --dry-run`. Cheap levers: a shared TM, the cheap model for `audit_review` (default),
  `visual = false` or a small `visual_max_tiles`, and `--pages` for a trial.

## Resume, selection, force

State is `work/job/state.json`; logs are `work/job/logs/<stage>.log`. A stage is skipped when it is
`done`, its inputs hash (config keys, input files, upstream outputs, pages) is unchanged and its
outputs are untouched. Failed stages are re-evaluated.

```bash
python scripts/rudraw.py run --from build               # this stage and everything after it
python scripts/rudraw.py run --only audit               # one stage
python scripts/rudraw.py run --until automap            # stop after a stage
python scripts/rudraw.py run --pages 3,7-9              # page-aware stages only; partial builds go to *_test.pdf
python scripts/rudraw.py run --only translate --force   # re-run even if unchanged
```

## Determinism

The LLM cache `work/llm/cache/<hash>.json` is keyed by task, prompt version, model, input data, image
hashes and the system prompt. A re-run with the same inputs replays identical answers at cost 0, so the
output is reproducible; editing a prompt, the background or a model invalidates exactly the entries it
affects. `scripts/qa/determinism.py` checks the build itself (below).

## Delivery gates

They are written to `work/job/run_summary.json`; `run` exits `2` if one fails.

| gate | must be |
|---|---|
| `unplaced` | no `未放置` rows in `build_report.tsv` |
| `audit_remaining` | `remaining 0`, with `work/rapid` present |
| `mark_gate` | `50_check` exit 0 (every member mark exists in the sheet titles) |
| `qa_flags` | advisory only: flagged pages go to the visual review |

The text route has one gate, `missing_tm_keys` = 0. When a gate fails: `status`, read
`work/job/logs/<stage>.log`, fix the cause, re-run that stage (`run --from <stage>`).

## Regression and QA commands

```bash
python scripts/qa/metrics.py OUT.pdf --config project.toml [--src SRC.pdf] --json run.json
python scripts/qa/regress.py run.json --baseline baseline.json [--strict] [--tol key=abs]   # exit 1 = regression
python scripts/qa/occlusion.py OUT.pdf --src SRC.pdf --json flagged.json    # ink / rule / tiny / overlap flags
python scripts/qa/determinism.py --config project.toml [--pages 1-5]        # two fresh builds must be identical
```

`metrics.py` writes flat JSON (CJK character count, leaders, unplaced, font-size percentiles, audit
remaining, mark gate, text/layout fingerprints, occlusion counts). `regress.py` compares it with a
stored baseline metric by metric (equal / better / worse); keep baselines of your own volumes outside
this repository. `occlusion.py` is also what the `qa` stage uses to pick the tiles for the visual review.
