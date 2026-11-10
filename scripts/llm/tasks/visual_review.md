You check tiles of a finished bilingual construction drawing: black = the original Russian drawing, blue = the added Chinese. Find Russian (or English-only) text without Chinese, and Chinese that is illegible or wrong. Report only real problems.

Issue types:
- 漏译: Russian text (word, header, label, note, title, title-block field, stamp) with no corresponding blue Chinese. Chinese below, to the right, via a thin blue leader line, or in a Chinese block in free space / a widened right column all count as present.
- 英文无中文: English-only text with no Russian counterpart and no Chinese.
- 遮挡: blue Chinese overlapping lines or text, or too small to read.
- 错译: Chinese clearly wrong, especially numbers or codes that differ from the original, or attached to the wrong label.

Do not report: person names, logos, drawing codes / file names, standard numbers alone, steel grades, lone member marks, item numbers, axis labels, dimensions, units; standard title-block fields (translated in one blue "图签译文:" line near the title block); count marks шт./pcs.N and ш./sp.N (labelled by the engine without numbers).

Each issue: {tile, type, text, where, zh}
- where: [x0, y0, x1, y1] box of the original text inside that tile image, fractions 0–1.
- text: the original text as read in the image, numbers copied exactly.
- zh: suggested Chinese for 漏译 / 英文无中文 / 错译 ("" for 遮挡). Every number in zh must appear in that tile's OCR text; otherwise leave the number out.
checked: the ids of all tiles (you must look at every tile).
