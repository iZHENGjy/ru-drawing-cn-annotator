# Output standard (binding for every route)

What a delivered Chinese edition must look like. The scan route, the text-layer route and any
future route follow the same rules; a page that cannot follow them is reported, not improvised.

## 1. The original stays untouched

Nothing of the original is removed, moved or covered. Chinese is an added vector layer (blue).
No separate Chinese pages are inserted: the delivered PDF has exactly the pages of the original.

## 2. Pages of running text get an extension column

A4 pages and any other page that is mostly running text (body text, notes, letters, protocols,
contents) are **widened to the right**: the original page keeps its content and size on the left,
a Chinese column is added on the right. This is the same widened column the approved sample uses
for its notes pages.

- **Whole paragraphs.** Running text is translated paragraph by paragraph, as whole sentences.
  Never split by PDF line or OCR line. Lines that continue a sentence (no full stop, hyphenated
  word, same table cell, centred lines of one cell) belong to the same paragraph.
- **Top-aligned.** Each Chinese paragraph starts at the height of the first line of its Russian
  paragraph. Headings and clause numbers correspond one to one.
- **One readable size.** The column uses one font size per page, not below about 8 pt.
- **Never a new page.** When the column is full, the paragraphs continue downward in their
  order (the column is widened first, then the alignment is relaxed, then the page grows downward).
- **Contents pages.** Every entry gets its Chinese in the column, aligned to its line.
- **Tables continue into the column.** Every rule of a table row is drawn on in the column as a
  light thin line at the same height, so each row's Chinese sits in the same row.
  - The header is one entry in the original column order (`序号 | 名称 | 类型、型号、ГОСТ | 技术特性 | 用途`),
    inside the header row.
  - A data row starts with its row number (`11.`), then its fields in column order separated by
    ` | `, top-aligned between the row's two rules; long text wraps (and the size is reduced) but
    never crosses the next rule.
  - **Every column is listed**, in column order: translated cells in Chinese, cells that need no
    translation (codes, standard numbers, numbers: ГОСТ 7211-86, 2, -) copied as they are, so
    the fields of a row always correspond to the columns.
  - A merged cell that spans several rows joins the first row it spans.
  - A cell's text is one paragraph, never broken into its lines. A table continued from the
    previous page follows the same rules with its own row numbers.
  - A row is a band between two horizontal rules crossed by inner cell walls; the sheet frame is
    not a cell wall.

## 3. Short labels on drawings

Drawing labels, table headers and titles on drawings: Chinese **directly below** the Russian,
left-aligned, about 0.95 × the Russian size. A leader is used only when there is no room below.
Chinese is never placed above the Russian and never in distant free space.

## 4. Title block and margin fields

Title-block and margin-stamp fields (Разраб., Проверил, Изм., Подп., Инв. № подл. …) are not
translated inside their cells. They are collected into **one** line `图签译文：…` (Russian =
Chinese pairs). A page without a title block gets no such line.

## 5. Regression

The approved drawing output (baselines `approved_<set>.json` of the approved volumes, kept outside
this repository) must not change: `layout_sha256` stays identical. New behaviour that would change
those pages is switched on per project: `[scan] output_standard = 2` (scan route) and
`[project] notes_pages` (text route, running-text pages). Default `output_standard = 1` reproduces the
approved layout byte for byte. `rudraw init` writes `output_standard = 2` for new scan projects.

## 6. Checked by code

`drawtrans.qa.standard` checks the rules above on the placement record every build writes next to
the output PDF (`*.placements.json`; the PDF itself is not touched). A violation makes the run
**not deliverable** (`rudraw run` gate `output_standard`, exit code 2):

| rule | check |
|---|---|
| `above` | a label set above its source text (§3) |
| `split_paragraph` | a column paragraph ends mid-sentence and the next one continues it (§2) |
| `title_block_cell` | a title-block field translated inside its cell (§4) |
| `row_number` | a row of a numbered table in the column without its row number (§2) |
| `font_floor` | column text below 6.4 pt, labels below 4.0 pt (§2) |

Only placements made under the standard count (`output_standard = 2`, extension columns); pages of
the approved legacy layout are listed as advisory notes.

## 7. Other languages

The rules are the same for every language pair (`[project] source_lang / target_lang`): extension
column, translation directly below the source, one title-block legend line. Only the font (CJK for a
Chinese target, Latin/Cyrillic otherwise), the line breaking (jieba words for Chinese, spaces otherwise)
and the legend words (`图签译文：` / `Title block:` …) follow the target language.

## Where it is implemented

| rule | text-layer route | scan route |
|---|---|---|
| extension column, paragraphs, contents, tables | `layout/extend.py` (pages in `[project] notes_pages`) | `segment/prose.py` paragraphs, `layout/scan_place.py` widened column (`output_standard = 2`) |
| labels below the Russian | `layout/place.py` | `layout/scan_place.py` (never above with `output_standard = 2`) |
| `图签译文` line | `layout/extend.py` | `layout/scan_place.py` legend (title-block texts read whole by `page_notes` replace OCR fragments) |

## 8. Standard v2 (`[scan] output_standard = 3`) — numbers

The default for new projects since the user approved the sample pages (2026-11-15): `drawtrans init` writes
`output_standard = 3`. Earlier deliveries keep their standard and rebuild byte for byte; baselines of v2
volumes store one layout hash per page (`layout_pages`), so a change is located to its page. Every number below is `drawtrans.layout.policy.V2`; the engine
(`layout/core.py`) and the gate (`qa/standard.py`, `qa/occlusion.py`) read the same object
(`tests/test_layout_core.py::test_policy_matches_qa`).

**Sizes are printed sizes.** A drawing prints on A3, a document (long side ≤ 900 pt) on A4, in the page's
orientation, fitting both sides; a page widened by the column prints smaller. PDF size = printed size ÷
print factor (`Policy.scale`).

| rule | value |
|---|---|
| label floor | 6 pt printed, hard: no label is drawn smaller |
| preferred minimum | 7 pt printed; at most 5 % of a page's labels below it (per-page budget) |
| label size | 0.95 × source, at least the preferred minimum (+1.5 % margin) |
| shrinking | at most to 0.8 × the preferred size; sizes ≥ 7 pt first, then wrapping (≤ 3 lines), then sizes < 7 pt, then a leader, then the numbered column |
| order of places | below the source (titles centred) → below, shifted down ≤ 4 source lines → right / left → (smaller) → wrapped → leader → numbered |
| never | above the source (labels); on a rule; on source ink |
| rule clearance | max(0.5 pt, 0.10 × size) on all four sides, from a 150 dpi raster: rules are dark runs ≥ 8 px, dashed rules (gaps ≤ 3 px) included, boxes not inset |
| ink | ≤ 3 % source ink inside the box (QA `ink_box`), ≤ 15 % under the glyphs (QA `ink_glyph`) |
| between translations | 1.2 pt |
| choice | all candidates of a tier are scored (distance, size loss, wrapping, closeness to rules, ink); the lowest score of the lowest tier wins |
| leaders | ≤ 5 per page; ≤ 30 pt (pages up to A2), ≤ 60 pt (A1 and larger); end points on the two boxes' nearest edges; the segment crosses no word box, no translation and ≤ 10 % source ink; volume share ≤ 3 %, mean ≤ 15 pt |
| approval stamp | one line outside the stamp (below, above, right, left), ≥ 7 pt printed; else into the legend line |
| title-block legend | in the free band right above the title block, else outside the frame along its lower edge, ≥ 6.5 pt printed (7.5 preferred), line spacing 1.2; else at the bottom of the column |
| no place left | a number ①…⑩, (11)… next to the source, the translation under that number in the extension column at the source's height (without a number the entry starts with the source text) |
| extension column | 0.35 × page width (≤ 900 pt), 9 pt printed, steps 0.89 / 0.8 down to 6.5 pt |
| count labels (шт./pcs.) | placed when there is room; otherwise only the legend explains them |

Both routes use the same engine: the scan route through `layout/scan_v2.py` (reportlab drawing), the
text-layer route through `layout/text_v2.py` (PyMuPDF drawing; text projects set `[project] output_standard = 3`).
In the text route an English slot is filled only at allowed sizes; otherwise the label goes through the engine
like a Russian-only label. Table rows that have to leave their cells are listed in the column between the
row's continued rules (`scan_v2.layout_rows`); notes clauses and the legend flow in the column without overlap.

QA under standard 3 (gate `output_standard`): `font_floor` (printed size by role), `occlusion` (rule pixels or
ink in the placed box), `leader` (length, count per page, volume share and mean), `above`, `title_block_cell`.
The visual review samples QA tiles evenly over the pages (`pipeline.stages_scan.sample_tiles`).
