---
name: ru-drawing-cn-annotator
description: 把俄文工程图纸转成中文对照版——蓝色中文叠加在俄文旁,原图零改动,输出可打印的双语 PDF(或 DWG/DXF 中文图层)。用户上传俄文图纸或俄文技术文件(PDF/扫描件/DWG/DXF,常见 КМ/КЖ/КМД 图册、预埋件册、总说明、明细表、工程量清单),或提到"俄文图纸""俄译中""图纸翻译""双语图纸""中文标注版/对照版""ГОСТ/СП/СНиП 文件翻译"时使用,即使用户没有明说要"标注版"。
---

# 俄文图纸 → 中文对照版

代码在本仓库 `scripts/`,**用现成脚本,不要临场重写引擎**。先判断走哪条路线。

## 0. 普查,选路线
- `python scripts/01_extract.py`(在项目文件夹里,带 project.toml)。**有文字层** → 主流程(第 1 节);
  **扫描件 / SHX 线条字** → 扫描件流程(第 2 节);DWG → DXF 后走 `scripts/cad/`(README)。
- 项目资料放在本仓库**之外**的项目文件夹;业主名、印章文字、图号前缀等写进 project.toml,绝不写进代码。

## 1. 有文字层的 PDF(主流程)
翻译记忆库循环,直到 `missing TM keys: 0`:
`20_build.py all --dry` → `41_export_todo.py` → 翻译成 batch.tsv(数字保持 ⟨n⟩ 占位符)→ `40_merge_tm.py` → `20_build.py all` → `43_issues.py`。命令见 README。

## 2. 扫描件(无文字层)——`scripts/scan/`,详见 docs/scanned.md
1. `10_ocr.py`(Tesseract rus+eng psm11,用 tessdata_best)+ `11_rapid.py`(RapidOCR 第二检测器,必跑:漏检的竖排字和短标注靠它)。
2. 翻译(独立任务):每页 `notes/pNN.json`,按 `docs/scanned/prompt_translate.md`。
3. 行映射(**另开新任务**,不要和第 2 步共用上下文):每页 `map/pNN.json`,按 `docs/scanned/prompt_map.md`;`12_preview.py` 出编号框图。
4. `20_build.py --pages N` 试一页 → 全量 `20_build.py`(自动加可点击目录)。
5. `30_audit.py` → 复核员按 `docs/scanned/prompt_audit_review.md` 回答 packets → `31_apply_audit.py` → 重建,循环到 **remaining 0**。
6. `50_check.py`(构件代号必须在图名清单里;exit 0)。
7. `40_tiles.py --round 1` → 逐页看图复核(`docs/scanned/prompt_visual_review.md`)→ `41_apply_visual.py --round 1` → 重建 → 再审。第二轮起只看改动或有问题的页(`--pages`)。

### 交付关卡(全部满足才交付)
- build_report 无「未放置」;audit `remaining 0`(必须有 work/rapid);`50_check.py` 通过;
- 每页都看过图,`漏译` 为 0,`遮挡/错译` 清单已处理。
- 交付说明里如实写:哪些靠程序证明、哪些靠抽查,还剩什么没做。

### 铁律(来自真实项目的返工)
- 错译比漏译危险:数字、规格、件号、构件代号逐字照图抄;OCR 常丢小数点(Км 12 = Км1.2)。
- map 里的 `""` 只用于续行、人名、乱码、说明条文行——其余都会被审计查出来。
- 英文版页面也要有中文,不能只写"见第 N 页"。
- 不写「4根」「间距200」这类数量标注,也不目测补框:引擎按 OCR 单词框自动挂「根/间距」。
- 中文字号不小于引擎下限;图签/侧栏栏目进「图签译文」对照行,不硬塞格子。

### 控制成本
- 翻译、映射、审计复核、看图复核各用**新的上下文**,只给它需要的文件(复核员只读 packets / 分块图)。
- 同一项目的图签、表头、通用说明每册都一样:先查已有译文/术语表,只翻新出现的。
- 看图复核最贵(69 页一轮约 240 万 token):第一轮全看,之后只看改过的页。
