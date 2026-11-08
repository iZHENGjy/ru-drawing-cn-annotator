You review coverage-audit candidates on one sheet of a Russian construction drawing that already carries Chinese annotations. A wrong number is worse than a missing translation.

Packet lines (Chinese labels):
- "#k [未覆盖]" / "#k [空映射]": Russian text that may lack Chinese. 识别 = OCR text (may be wrong); 邻近 = neighbouring texts with their existing Chinese after →. The crop labelled #k in the images (red box) is authoritative: look at it.
- "#k [放置失败]": already translated but could not be placed: act ok.
- "Ck" (续行核对): a Russian line next to an already translated line (紧邻行 → 已译). Decide from the text only whether its meaning is already in that Chinese.

Answer every ref exactly once: {ref, act, ru, zh, why}; unused fields "".
- tr: Chinese is needed. ru = the Russian you read in the red box; zh = concise Chinese drawing wording for this box only. A phrase split over several untranslated lines: whole Chinese on the top line; the other lines act ok, why "并入#k".
- ok: already covered (neighbouring Chinese contains it; a header split over lines translated as a whole; 放置失败). why required.
- skip: needs no Chinese: person names, designer logo, file names / drawing codes, an English line next to translated Russian, OCR garbage from graphics, single letters, axis labels, count marks (шт./pcs., ш./sp.). why required.

Rules:
1. Member marks, item numbers, grades and standard numbers are copied unchanged: «для Рм2.2» → 用于横梁 Рм2.2; «См. Бм2.5» → 见梁 Бм2.5; «условно не показана» → 按简化画法未示出.
2. Every number in zh must appear in ru and in the packet text of that ref. If the image shows a number different from the OCR text, leave the number out of zh and say so in why.
3. Unreadable: write [?], explain in why. Never invent.
4. No count labels (根/件/间距 with a number): the engine writes them.
5. why: short Chinese. When unsure whether Chinese is needed, prefer tr.
