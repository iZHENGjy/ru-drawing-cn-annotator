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
- **Tables.** A table row is one column entry, its cells joined by `｜` in reading order, aligned
  to the row. A cell's text is one paragraph, never broken into its lines.

## 3. Short labels on drawings

Drawing labels, table headers and titles on drawings: Chinese **directly below** the Russian,
left-aligned, about 0.95 × the Russian size. A leader is used only when there is no room below.
Chinese is never placed above the Russian and never in distant free space.

## 4. Title block and margin fields

Title-block and margin-stamp fields (Разраб., Проверил, Изм., Подп., Инв. № подл. …) are not
translated inside their cells. They are collected into **one** line `图签译文：…` (Russian =
Chinese pairs). A page without a title block gets no such line.

## 5. Regression

The approved drawing output (baselines `approved_kg1.json`, `approved_kg1i.json`, kept outside
this repository) must not change: `layout_sha256` stays identical. New behaviour that would change
those pages is switched on per project: `[scan] output_standard = 2` (scan route) and
`[project] notes_pages` (text route, running-text pages). Default `output_standard = 1` reproduces the
approved layout byte for byte.

## Where it is implemented

| rule | text-layer route | scan route |
|---|---|---|
| extension column, paragraphs, contents, tables | `engine/extend.py` (pages in `[project] notes_pages`) | `scan/prose.py` paragraphs, `scan/engine.py` widened column (`output_standard = 2`) |
| labels below the Russian | `engine/place.py` | `scan/engine.py` (never above with `output_standard = 2`) |
| `图签译文` line | `engine/extend.py` | `scan/engine.py` legend (title-block texts read whole by `page_notes` replace OCR fragments) |
