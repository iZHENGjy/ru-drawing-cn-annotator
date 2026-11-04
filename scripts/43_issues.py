"""Build the reviewer's issue list (Markdown, Chinese) from <review>/report.csv.

usage: 43_issues.py [--config project.toml] [--out 问题清单.md] [--product name_CN.pdf]
                    [--notes manual_notes.md]

--notes   optional Markdown file with your own findings about the source drawings
          (typos, RU/EN contradictions, terms to confirm with the designer); it is
          inserted as section 二 verbatim.
"""
import argparse, csv, datetime, re, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import config


def group(items):
    """same text on many pages -> one line with a page list"""
    g = defaultdict(list)
    for p, t in items:
        g[t].append(p)
    return sorted(g.items(), key=lambda kv: kv[1][0])


def pages(ps):
    ps = sorted(set(ps))
    return "、".join(f"p{p}" for p in ps) if len(ps) <= 8 else f"p{ps[0]} 等 {len(ps)} 页"


def categorise(rows):
    cats = defaultdict(list)          # category -> [(page, text)]
    for r in rows:
        for it in filter(None, (r["issues"] or "").split(" || ")):
            p = int(r["page"])
            if it.startswith("callout"):
                cats["callout"].append((p, it.split(": ", 1)[1]))
            elif it.startswith("补回数字"):
                # "шаг/ step 200": the spacing value sits next to the English word by design
                if not re.search(r"\]: шаг\s*/?$", it):
                    cats["numfix"].append((p, it))
            elif it.startswith("英文与俄文数字不一致"):
                cats["mismatch"].append((p, it))
            elif it.startswith("overflow"):
                cats["overflow"].append((p, it.split(": ", 1)[1]))
            elif it.startswith(("missing", "unplaced")):
                cats["missing"].append((p, it))
            elif it.startswith(("中文压", "英文残留", "俄文丢失", "标准号不一致", "中文出界")):
                cats["qa"].append((p, it))
            else:
                cats["other"].append((p, it))
    return cats


def render(rows, project, product, manual=""):
    tot = defaultdict(int)
    for r in rows:
        for k in ("units", "ok", "missing", "overflow", "callout", "unplaced", "ru_lost", "en_residual", "overlap"):
            tot[k] += int(r.get(k) or 0)
    cats = categorise(rows)
    L = [f"# {project} 中文翻译件 问题清单\n",
         f"对应成品：`{product}`，共 {len(rows)} 页。生成日期 {datetime.date.today().isoformat()}。\n",
         "## 一、自动检查结果\n", "| 检查项 | 结果 |", "|---|---|",
         f"| 翻译单元总数 | {tot['units']} |",
         f"| 缺译或无处放置 | {tot['missing'] + tot['unplaced']} |",
         f"| 俄文被误删的词 | {tot['ru_lost']} |",
         f"| 删除后残留的英文 | {tot['en_residual']} |",
         f"| 中文压原文或压中文 | {tot['overlap']} |",
         f"| 原位放不下、改用圈号引出 | {tot['callout']} |",
         f"| 超出预定区域 | {tot['overflow']} |", "",
         "自动检查只能证明每段俄文都有中文、数字与原文一致、版面没有压字，不能证明译文正确。"
         "译文正确性靠词典人工审定，建议设计方对术语抽查确认。\n"]
    L.append("## 二、原文本身的问题（建议与设计方核实）\n")
    for t, ps in group(cats["mismatch"]):
        m = re.match(r"英文与俄文数字不一致\[英文 (.*?)\]: (.*)", t)
        en, ru = (m.group(1), m.group(2)) if m else ("", t)
        L.append(f"- **{pages(ps)} 英文与俄文数字不一致：** 俄文为\"{ru.strip(' -')}\"，英文写成\"{en}\"。中文按俄文。")
    L.append(manual.strip() + "\n" if manual.strip() else ("（无）\n" if not cats["mismatch"] else ""))
    L.append("## 三、版面处理说明\n")
    if cats["callout"]:
        L.append("以下内容在原位置放不下中文，已在俄文旁加蓝色圈号（①②…），中文放在本页最近的空白处，前面标同一圈号：\n")
        L += [f"- {pages(ps)}：{t}" for t, ps in group(cats["callout"])] + [""]
    if cats["overflow"]:
        L.append("以下内容中文超出了预定区域，请目视确认：\n")
        L += [f"- {pages(ps)}：{t}" for t, ps in group(cats["overflow"])] + [""]
    if cats["numfix"]:
        L.append("以下条目中文最初漏带了代号或数字，程序已按俄文自动补回，建议抽查：\n")
        L += [f"- {pages(ps)}：{t}" for t, ps in group(cats["numfix"])] + [""]
    if not (cats["callout"] or cats["overflow"] or cats["numfix"]):
        L.append("（无）\n")
    if cats["missing"] or cats["qa"] or cats["other"]:
        L.append("## 四、需要人工处理的检查项\n")
        L += [f"- {pages(ps)}：{t}" for t, ps in group(cats["missing"] + cats["qa"] + cats["other"])] + [""]
    return "\n".join(L), cats, tot


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default="问题清单.md")
    ap.add_argument("--product", default=None, help="file name of the delivered PDF (for the header)")
    ap.add_argument("--notes", default=None, help="Markdown file with manual findings")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = config.load(a.config)
    rows = list(csv.DictReader(open(cfg.review / "report.csv", encoding="utf-8-sig")))
    product = a.product or (cfg.pdf.stem + "_CN.pdf" if cfg.pdf else "output.pdf")
    manual = Path(a.notes).read_text(encoding="utf-8") if a.notes else ""
    text, cats, tot = render(rows, cfg.name, product, manual)
    cfg.output.mkdir(parents=True, exist_ok=True)
    (cfg.output / a.out).write_text(text, encoding="utf-8")
    print("written", cfg.output / a.out, {k: len(v) for k, v in cats.items()}, dict(tot))


if __name__ == "__main__":
    main()
