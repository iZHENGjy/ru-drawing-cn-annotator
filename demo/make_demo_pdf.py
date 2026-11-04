"""Generate the synthetic demo drawing set (demo_drawing.pdf, 2 × A3 landscape).

Sheet 1: Russian-only foundation drawing - plan, section, notes, specification table,
         ГОСТ 21.101 title block and the rotated left margin stamp.
Sheet 2: bilingual RU/EN "general data" sheet (Russian line + English line below), the
         typical layout of drawings made for international projects.

Everything here is invented for the demo (names are placeholders).
usage: python demo/make_demo_pdf.py [out.pdf]
"""
import sys
from pathlib import Path
from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONTS = ["C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/Library/Fonts/Arial.ttf",
         "/System/Library/Fonts/Supplemental/Arial.ttf"]
font = next((f for f in FONTS if Path(f).is_file()), None)
if font is None:
    sys.exit("need a TTF with Cyrillic glyphs (Arial / DejaVu Sans / Liberation Sans)")
pdfmetrics.registerFont(TTFont("F", font))

W, H = landscape(A3)                       # 1190.55 x 841.89 pt
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("demo_drawing.pdf")
c = canvas.Canvas(str(OUT), pagesize=(W, H))
c.setTitle("Demo drawing (synthetic)")
c.setAuthor("ru-drawing-cn-annotator demo")


# ------------------------------------------------------------- helpers (top-left coordinates)
def Y(y):
    return H - y


def text(x, y, s, size=8, rot=0):
    """baseline-left at (x, y) in top-left page coordinates"""
    c.setFont("F", size)
    if rot:
        c.saveState(); c.translate(x, Y(y)); c.rotate(rot); c.drawString(0, 0, s); c.restoreState()
    else:
        c.drawString(x, Y(y), s)


def ctext(x0, x1, y, s, size=8):
    c.setFont("F", size)
    c.drawCentredString((x0 + x1) / 2, Y(y), s)


def line(x0, y0, x1, y1, w=0.5, dash=None):
    c.setLineWidth(w)
    c.setDash(list(dash)) if dash else c.setDash()
    c.line(x0, Y(y0), x1, Y(y1))
    c.setDash()


def rect(x0, y0, x1, y1, w=0.5):
    c.setLineWidth(w)
    c.rect(x0, Y(y1), x1 - x0, y1 - y0)


def circle(x, y, r, w=0.5):
    c.setLineWidth(w)
    c.circle(x, Y(y), r)


def frame_and_stamp():
    rect(57, 14, W - 14, H - 14, 1.2)
    # left margin stamp (rotated labels, ГОСТ 21.101 form 3 - additional columns)
    x0, x1, x2 = 25, 39, 57
    ys = [H - 14, H - 14 - 70, H - 14 - 170, H - 14 - 240]
    for ya, yb in zip(ys, ys[1:]):
        rect(x0, yb, x2, ya, 0.8)
    line(x1, ys[-1], x1, ys[0], 0.5)
    for (ya, yb), s in zip(zip(ys, ys[1:]), ["Инв. № подл.", "Подп. и дата", "Взам. инв. №"]):
        text(x1 - 4, (ya + yb) / 2 + 20, s, 6.5, rot=90)


def title_block(sheet, sheets, sheet_title):
    x0, x1 = W - 14 - 524, W - 14
    y0, y1 = H - 14 - 156, H - 14
    rect(x0, y0, x1, y1, 1.0)
    # change table + personnel (left 190 pt)
    xl = x0 + 190
    line(xl, y0, xl, y1, 1.0)
    rows = [y0 + 16 * i for i in range(10)]
    for yy in rows[1:]:
        line(x0, yy, xl, yy, 0.4 if yy != rows[3] else 1.0)
    cols = [x0, x0 + 28, x0 + 56, x0 + 84, x0 + 124, x0 + 166, xl]
    for xx in cols[1:-1]:
        line(xx, y0, xx, rows[3], 0.4)
    for xx in (x0 + 56, x0 + 124, x0 + 166):
        line(xx, rows[3], xx, rows[8], 0.4)
    for a, b, s in zip(cols, cols[1:], ["Изм.", "Кол.уч.", "Лист", "№док.", "Подп.", "Дата"]):
        ctext(a, b, rows[3] - 4.5, s, 6.5)
    people = [("Разраб.", "Иванов"), ("Пров.", "Петров"), ("Н.контр.", "Сидоров"), ("ГИП", "Кузнецов")]
    for i, (role, name) in enumerate(people):
        yb = rows[4 + i] - 4.5
        text(x0 + 3, yb, role, 6.5)
        text(x0 + 59, yb, name, 6.5)
    # right part: project, sheet title, stage / sheet / sheets, organisation
    ya, yb = y0 + 44, y0 + 108
    line(xl, ya, x1, ya, 1.0); line(xl, yb, x1, yb, 1.0)
    ctext(xl, x1, y0 + 27, "Насосная станция. Фундаменты под оборудование", 10)
    xs = x1 - 150
    line(xs, ya, xs, y1, 1.0)
    for xx in (xs + 50, xs + 100):
        line(xx, ya, xx, ya + 32, 0.4)
    line(xs, ya + 16, x1, ya + 16, 0.4); line(xs, ya + 32, x1, ya + 32, 0.4)
    for i, (h, v) in enumerate([("Стадия", "Р"), ("Лист", str(sheet)), ("Листов", str(sheets))]):
        ctext(xs + 50 * i, xs + 50 * (i + 1), ya + 11, h, 6.5)
        ctext(xs + 50 * i, xs + 50 * (i + 1), ya + 27, v, 7)
    for k, s in enumerate(sheet_title):
        ctext(xl, xs, ya + 24 + 16 * k, s, 8.5)
    ctext(xs, x1, yb + 28, "ООО «Проектное бюро»", 9)


# ============================================================= sheet 1: Russian only
frame_and_stamp()
# ---- plan of the foundation
cx, cy, s = 270, 330, 100              # centre, half size of the footing
text(cx - 85, 120, "План фундамента Фм1", 13)
line(cx - 85, 124, cx + 45, 124, 0.6)
text(cx + 80, 120, "М 1:25", 9)
rect(cx - s, cy - s, cx + s, cy + s, 1.2)                 # footing
rect(cx - 45, cy - 45, cx + 45, cy + 45, 1.2)             # pedestal
for dx in (-25, 25):
    for dy in (-25, 25):
        circle(cx + dx, cy + dy, 4, 0.8)                  # anchor bolts
line(cx, cy - s - 40, cx, cy + s + 40, 0.4, dash=(12, 3, 2, 3))
line(cx - s - 40, cy, cx + s + 40, cy, 0.4, dash=(12, 3, 2, 3))
circle(cx, cy + s + 52, 10); ctext(cx - 10, cx + 10, cy + s + 55.5, "А", 10)
circle(cx - s - 52, cy, 10); ctext(cx - s - 62, cx - s - 42, cy + 3.5, "1", 10)
# dimensions
line(cx - s, cy + s + 18, cx + s, cy + s + 18, 0.4)
for xx in (cx - s, cx + s):
    line(xx - 3, cy + s + 21, xx + 3, cy + s + 15, 0.6)
ctext(cx - s, cx + s, cy + s + 15, "2400", 8)
line(cx + s + 18, cy - s, cx + s + 18, cy + s, 0.4)
for yy in (cy - s, cy + s):
    line(cx + s + 15, yy + 3, cx + s + 21, yy - 3, 0.6)
text(cx + s + 15, cy + 16, "2400", 8, rot=90)
# section mark 1-1
line(cx - s - 30, cy - 70, cx - s - 10, cy - 70, 1.4); line(cx + s + 10, cy - 70, cx + s + 30, cy - 70, 1.4)
text(cx - s - 26, cy - 76, "1", 9); text(cx + s + 20, cy - 76, "1", 9)
# leaders
line(cx + 45, cy - 40, cx + 150, cy - 160, 0.4); line(cx + 150, cy - 160, cx + 245, cy - 160, 0.4)
text(cx + 152, cy - 164, "Подколонник", 8)
line(cx + 25, cy + 25, cx + 160, cy + 170, 0.4); line(cx + 160, cy + 170, cx + 300, cy + 170, 0.4)
text(cx + 162, cy + 166, "Анкерные болты М24", 8)
line(cx - s + 20, cy + s - 20, cx - 60, cy + s + 115, 0.4); line(cx - 60, cy + s + 115, cx + 40, cy + s + 115, 0.4)
text(cx - 58, cy + s + 111, "Фундамент Фм1", 8)

# ---- section 1-1
sx, base = 700, 330
text(sx - 30, 140, "Разрез 1-1", 13)
line(sx - 30, 144, sx + 40, 144, 0.6)
rect(sx - s - 10, base + 60, sx + s + 10, base + 70, 0.6)       # lean concrete
rect(sx - s, base, sx + s, base + 60, 1.2)                # slab
rect(sx - 45, base - 140, sx + 45, base, 1.2)             # pedestal
for dx in (-25, 25):
    line(sx + dx, base - 160, sx + dx, base - 10, 0.8)    # bolts
line(sx - 170, base - 110, sx - 45, base - 110, 0.4)      # ground line
line(sx + 45, base - 110, sx + 170, base - 110, 0.4)
text(sx - 165, base - 116, "±0,000", 8)
text(sx + 120, base + 54, "-1,500", 8)
line(sx + 100, base + 60, sx + 170, base + 60, 0.4)
line(sx + 130, base + 66, sx + 220, base + 105, 0.4); line(sx + 220, base + 105, sx + 330, base + 105, 0.4)
text(sx + 222, base + 101, "Бетонная подготовка", 8)
line(sx - 45, base - 40, sx - 160, base - 40, 0.4)
text(sx - 232, base - 44, "Обратная засыпка", 8)

# ---- notes
nx, ny = 870, 60
text(nx, ny, "Примечания", 11)
notes = ["1. Размеры даны в миллиметрах, отметки - в метрах.",
         "2. Бетон фундамента класса B25 W6 F150 по ГОСТ 26633-2015.",
         "3. Арматура класса А500С по ГОСТ 34028-2016.",
         "4. Защитный слой бетона для нижней арматуры 40 мм.",
         "5. Под фундаментом выполнить подготовку из бетона B7,5 толщиной 100 мм.",
         "6. Обратную засыпку выполнять слоями по 300 мм с уплотнением."]
for i, n in enumerate(notes):
    text(nx, ny + 30 + 26 * i, n, 8)

# ---- specification table
tx, ty = 690, 470
text(tx + 170, ty - 10, "Спецификация", 11)
cols = [30, 105, 150, 35, 60, 65]
xs = [tx]
for w_ in cols:
    xs.append(xs[-1] + w_)
rows = [ty, ty + 30] + [ty + 30 + 30 * (i + 1) for i in range(4)]
rect(xs[0], rows[0], xs[-1], rows[-1], 1.0)
for yy in rows[1:-1]:
    line(xs[0], yy, xs[-1], yy, 0.6 if yy == rows[1] else 0.4)
for xx in xs[1:-1]:
    line(xx, rows[0], xx, rows[-1], 0.6)
for a, b, h in zip(xs, xs[1:], ["Поз.", "Обозначение", "Наименование", "Кол.", "Масса ед., кг", "Примечание"]):
    ctext(a, b, rows[0] + 11, h, 7)
data = [["1", "ГОСТ 34028-2016", "Ø12 А500С, L=2350", "24", "2,09", ""],
        ["2", "ГОСТ 24379.1-2012", "Болт фундаментный М24", "4", "5,6", "с гайками"],
        ["3", "", "Бетон класса B25", "", "", "4,2 м³"],
        ["4", "", "Бетон класса B7,5", "", "", "0,6 м³"]]
for r, row in enumerate(data):
    for k, (a, b, v) in enumerate(zip(xs, xs[1:], row)):
        if not v:
            continue
        if k == 2:
            text(a + 4, rows[r + 1] + 11, v, 7)
        else:
            ctext(a, b, rows[r + 1] + 11, v, 7)

title_block(1, 2, ["План фундамента Фм1.", "Разрез 1-1. Спецификация"])
c.showPage()

# ============================================================= sheet 2: bilingual RU / EN
frame_and_stamp()
text(90, 70, "Общие данные", 14)
text(90, 86, "General data", 12)

# list of drawings
tx, ty = 90, 130
text(tx, ty - 10, "Ведомость рабочих чертежей основного комплекта", 9)
text(tx, ty + 1, "List of working drawings of the main set", 8)
cols = [50, 330, 120]
xs = [tx]
for w_ in cols:
    xs.append(xs[-1] + w_)
rows = [ty + 10, ty + 40, ty + 70, ty + 100]
rect(xs[0], rows[0], xs[-1], rows[-1], 1.0)
for yy in rows[1:-1]:
    line(xs[0], yy, xs[-1], yy, 0.5)
for xx in xs[1:-1]:
    line(xx, rows[0], xx, rows[-1], 0.6)
for (a, b), ru, en in zip(zip(xs, xs[1:]), ["Лист", "Наименование", "Примечание"], ["Sheet", "Title", "Note"]):
    ctext(a, b, rows[0] + 12, ru, 7.5)
    ctext(a, b, rows[0] + 23, en, 7)
for r, (num, ru, en) in enumerate([("1", "Общие данные", "General data"),
                                    ("2", "План фундамента Фм1. Разрез 1-1. Спецификация",
                                     "Foundation Fm1 plan. Section 1-1. Specification")]):
    ctext(xs[0], xs[1], rows[r + 1] + 12, num, 7.5)
    text(xs[1] + 5, rows[r + 1] + 12, ru, 7.5)
    text(xs[1] + 5, rows[r + 1] + 23, en, 7)

# general notes, Russian clause + English clause below
nx, ny = 90, 290
text(nx, ny, "Общие указания", 11)
text(nx, ny + 13, "General notes", 10)
pairs = [("1. Проект разработан в соответствии с СП 22.13330.2016 и СП 63.13330.2018.",
          "1. The design is developed in accordance with SP 22.13330.2016 and SP 63.13330.2018."),
         ("2. За отметку 0,000 принята отметка чистого пола насосной станции.",
          "2. Elevation 0.000 is the finished floor level of the pumping station."),
         ("3. Основанием фундамента служит суглинок тугопластичный.",
          "3. The foundation rests on stiff loam."),
         ("4. Перед бетонированием выполнить приемку арматуры с составлением акта.",
          "4. Reinforcement shall be accepted with a report before concreting."),
         ("5. Работы выполнять в соответствии с проектом производства работ.",
          "5. Works shall be carried out according to the work execution plan.")]
for i, (ru, en) in enumerate(pairs):
    y = ny + 40 + 34 * i
    text(nx, y, ru, 8.5)
    text(nx, y + 11, en, 7.5)

# a few RU/EN labels on a small key plan
kx, ky = 760, 160
text(kx, ky - 30, "Схема расположения фундаментов", 10)
text(kx, ky - 18, "Foundation layout", 9)
rect(kx, ky, kx + 300, ky + 180, 0.8)
for i, (fx, fy) in enumerate([(40, 40), (150, 40), (40, 120), (150, 120)]):
    rect(kx + fx, ky + fy, kx + fx + 30, ky + fy + 30, 1.0)
    text(kx + fx + 4, ky + fy + 19, "Фм1", 7)
rect(kx + 230, ky + 50, kx + 280, ky + 140, 1.0)
text(kx + 233, ky + 88, "Насос", 7.5)
text(kx + 233, ky + 98, "Pump", 7)

title_block(2, 2, ["Общие данные"])
c.showPage()
c.save()
print("written", OUT)
