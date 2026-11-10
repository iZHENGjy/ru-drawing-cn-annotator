# Prompt · Step 2 — careful translation of a page (notes/pNN.json)

> For the translator (human or LLM agent).  One page per file.  Replace `<WORK>` with the
> project's work folder and add the project background (owner, plant, designer) at the top
> when you hand this out — it is not stored in the repository.

## Iron rules (a wrong number is worse than a missing word)

1. Numbers, sizes, elevations, item numbers, rebar/steel grades (А500С, С345-5), standards
   (ГОСТ / СП / СНиП / ТУ), drawing codes and member marks (Км1.1, Бм2.4-1, МН10) are copied
   **character for character from the image**, not from the OCR text.  OCR often drops the
   dot in marks (`Км 12` is `Км1.2`): check against the sheet title.
2. Decimal commas become points in the Chinese; the `ru` field keeps the original.
3. Unreadable characters: write `[?]` in `ru` and add an `issues` entry.  Never guess.
4. Names of people and companies stay as they are.  Standards keep their number; give the
   Chinese title in 《》 when the title is quoted.
5. Contradictions in the original (RU ≠ EN, duplicated clause numbers, wrong standard
   number, quantity in a table ≠ drawing) → translate as written and add an `issues` entry.
6. **English-only pages and English-only paragraphs get Chinese too.**  A page that repeats
   the Russian notes in English still needs its own `notes` block (copy the Chinese of the
   Russian clauses, aligned to the English clause numbers, and put the English start of each
   clause in `ru` so the engine can align it).  "See page N" is not a translation.

## Output: `<WORK>/notes/pNN.json` (UTF-8, must parse with `json.load`)

```json
{
  "page": 14,
  "sheet_code": "XXXX-КЖ1.001",
  "format": "A1",
  "title_ru": "Схема расположения и опалубочные чертежи колонн Км1.1...Км1.3",
  "title_zh": "柱 Км1.1…Км1.3 布置图及模板图",
  "blocks": [
    {"kind": "pairs", "heading_zh": "视图/剖面标题", "items": [{"ru": "Разрез 1-1", "zh": "剖面 1-1", "where": "左上"}]},
    {"kind": "notes", "heading_zh": "说明",
     "items": [{"no": "1", "ru": "Бетон класса B25, W8, F150 ...", "zh": "B25 级混凝土,抗渗 W8,抗冻 F150……"}]},
    {"kind": "table", "heading_zh": "明细表", "columns_zh": ["件号", "代号", "名称", "数量", "单件质量,kg", "备注"], "rows": []}
  ],
  "translator_notes": [],
  "issues": []
}
```

- `blocks` in reading order.  `notes` items: **every clause complete**, with its number in
  `no` and the Russian (or, on English pages, English) start in `ru` — the engine aligns the
  Chinese block to the clause numbers and the coverage audit uses `ru` to know the clause is done.
- Title, sheet code and format feed the clickable contents page.
- Plain numbers, axis labels and dimension text need not be listed.
- Terms: `glossary/gost_general.json` plus the project term list; reuse the term already used
  in the volume (one Russian term = one Chinese term across the whole set).
