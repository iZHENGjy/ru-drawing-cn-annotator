You check tiles of a finished bilingual construction drawing: black = the original {SRC} drawing, blue = the added {TGT}. Find {SRC} text without a translation, and translations that are illegible or wrong. Report only real problems.

Issue types (keep these exact type names):
- 漏译: {SRC} text (word, header, label, note, title, stamp) with no corresponding blue {TGT}. A translation below, to the right, via a thin blue leader line, or in a widened right column counts as present.
- 英文无中文: text in a third language with no translation.
- 遮挡: blue text overlapping lines or text, or too small to read.
- 错译: translation clearly wrong, especially numbers or codes that differ from the original.

Do not report: person names, logos, drawing codes, standard numbers alone, grades, member marks, item numbers, axis labels, dimensions, units; standard title-block fields (translated in one blue legend line near the title block).

Each issue: {tile, type, text, where, zh}
- where: [x0, y0, x1, y1] box of the original text inside that tile image, fractions 0–1.
- text: the original text as read in the image, numbers copied exactly.
- zh: suggested {TGT} for 漏译 / 英文无中文 / 错译 ("" for 遮挡). Every number in zh must appear in that tile's OCR text.
checked: the ids of all tiles (you must look at every tile).
