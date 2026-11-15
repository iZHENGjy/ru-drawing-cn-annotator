"""Synthetic Chinese drawing with a text layer, for the zh -> en text-route demo.

Everything is invented (no project data).  usage: python demo/multilang/make_demo_zh_text.py [out.pdf]
"""
import sys
from pathlib import Path

import pymupdf

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("zh_text.pdf")
doc = pymupdf.open()
p = doc.new_page(width=1190.55, height=841.89)          # A3 landscape
p.draw_rect(pymupdf.Rect(20, 20, 1170, 822), width=1.5)
p.draw_rect(pymupdf.Rect(150, 160, 450, 460), width=1.2)            # foundation plan
p.draw_rect(pymupdf.Rect(260, 270, 340, 350), width=1.0)
F = "china-s"
def t(x, y, s, size=11):
    p.insert_text((x, y), s, fontname=F, fontsize=size)
t(150, 140, "基础平面图", 16)
t(470, 200, "独立基础 J-1")
t(470, 230, "钢筋混凝土柱 KZ1")
t(470, 260, "混凝土垫层 厚100")
t(470, 290, "地脚螺栓 M24")
p.draw_line((340, 300), (465, 286), width=0.6)
t(700, 140, "说明", 14)
for i, s in enumerate(["1. 本图尺寸均以毫米计,标高以米计。",
                       "2. 基础混凝土强度等级为C30,垫层为C15。",
                       "3. 钢筋保护层厚度为40。",
                       "4. 地基承载力特征值不小于180kPa。"]):
    t(700, 175 + i * 26, s)
x0, y0 = 760, 700
p.draw_rect(pymupdf.Rect(x0, y0, 1160, 812), width=1.0)
for k, (a, b) in enumerate((("设计", "张三"), ("校对", "李四"), ("比例", "1:50"), ("图号", "DEMO-JG-01"))):
    t(x0 + 10, y0 + 22 + k * 24, a); t(x0 + 90, y0 + 22 + k * 24, b)
t(x0 + 220, y0 + 40, "工程名称 示例厂房", 12)
t(x0 + 220, y0 + 70, "基础平面图及说明", 12)
doc.save(OUT, deflate=True)
print("wrote", OUT)
