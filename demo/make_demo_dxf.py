"""Generate a small synthetic DXF (pump-room floor plan, mm) with Russian text:
TEXT, rotated TEXT, MTEXT and a block with text inside, inserted twice.
usage: python demo/make_demo_dxf.py [out.dxf]"""
import sys
from pathlib import Path
import ezdxf

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("demo_plan.dxf")
doc = ezdxf.new("R2010", setup=True)
doc.header["$INSUNITS"] = 4                                    # mm
doc.layers.add("WALLS", color=7)
doc.layers.add("TEXT", color=7)
doc.layers.add("EQUIP", color=3)
msp = doc.modelspace()

# walls
msp.add_lwpolyline([(0, 0), (9000, 0), (9000, 6000), (0, 6000)], close=True, dxfattribs={"layer": "WALLS"})
msp.add_line((6000, 0), (6000, 6000), dxfattribs={"layer": "WALLS"})
msp.add_line((6000, 2500), (9000, 2500), dxfattribs={"layer": "WALLS"})
# axes
for x, lab in ((0, "1"), (6000, "2"), (9000, "3")):
    msp.add_line((x, 6000), (x, 6500))
    msp.add_circle((x, 6750), 250)
    msp.add_text(lab, height=250, dxfattribs={"layer": "TEXT"}).set_placement((x, 6750), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

# pump block: frame + label
blk = doc.blocks.new("PUMP")
blk.add_lwpolyline([(0, 0), (1200, 0), (1200, 700), (0, 700)], close=True, dxfattribs={"layer": "EQUIP"})
blk.add_text("Насос", height=150, dxfattribs={"layer": "EQUIP", "insert": (80, 450)})
for i, x in enumerate((800, 3300)):
    msp.add_blockref("PUMP", (x, 3800))
    msp.add_text(f"Н-{i + 1}", height=150, dxfattribs={"layer": "TEXT", "insert": (x + 80, 3880)})

# room names
msp.add_text("Машинный зал", height=250, dxfattribs={"layer": "TEXT", "insert": (2000, 1500)})
msp.add_text("Электрощитовая", height=200, dxfattribs={"layer": "TEXT", "insert": (6400, 4300)})
msp.add_text("Тамбур", height=200, dxfattribs={"layer": "TEXT", "insert": (6900, 1300)})
# rotated label along a cable trench
msp.add_line((5500, 500), (5500, 5500), dxfattribs={"layer": "EQUIP"})
msp.add_text("Кабельный канал", height=150, rotation=90, dxfattribs={"layer": "TEXT", "insert": (5400, 2200)})
# title and a note
msp.add_text("План на отм. 0,000", height=350, dxfattribs={"layer": "TEXT", "insert": (0, -700)})
msp.add_mtext("Стены из кирпича толщиной 380 мм", dxfattribs={"layer": "TEXT", "char_height": 180,
                                                           "insert": (4500, -500), "attachment_point": 1})
doc.saveas(out)
print("written", out)
