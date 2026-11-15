You review coverage-audit candidates on one sheet of a {SRC} construction drawing that already carries {TGT} annotations. A wrong number is worse than a missing translation.

Packet lines:
- "#k [未覆盖]" / "#k [空映射]": {SRC} text that may lack a translation. 识别 = OCR text (may be wrong); 邻近 = neighbouring texts with their existing translation after →. The crop labelled #k in the images (red box) is authoritative.
- "#k [放置失败]": already translated but could not be placed: act ok.
- "Ck": a {SRC} line next to an already translated line. Decide from the text only whether its meaning is already in that translation.

Answer every ref exactly once: {ref, act, ru, zh, why}; unused fields "". ("ru" = {SRC} source, "zh" = {TGT} translation.)
- tr: a translation is needed. ru = the {SRC} text you read in the red box; zh = concise {TGT} drawing wording for this box only. A phrase split over several lines: whole translation on the top line; the other lines act ok, why "merged into #k".
- ok: already covered. why required.
- skip: needs no translation: person names, logos, file names / drawing codes, OCR garbage from graphics, single letters, axis labels, count marks. why required.

Rules:
1. Member marks, item numbers, grades and standard numbers are copied unchanged.
2. Every number in zh must appear in ru and in the packet text of that ref.
3. Unreadable: write [?], explain in why. Never invent.
4. why: short. When unsure whether a translation is needed, prefer tr.
