---
name: ru-drawing-cn-annotator
description: 把俄文工程图纸转成中文对照版——蓝色中文叠加在俄文旁,原图零改动,输出可打印的双语 PDF(或 DWG/DXF 中文图层)。用户上传俄文图纸或俄文技术文件(PDF/扫描件/DWG/DXF,常见 КМ/КЖ/КМД 图册、预埋件册、总说明、明细表、工程量清单),或提到"俄文图纸""俄译中""图纸翻译""双语图纸""中文标注版/对照版""ГОСТ/СП/СНиП 文件翻译"时使用,即使用户没有明说要"标注版"。
---

# 俄文图纸 → 中文对照版

代码在本仓库 `scripts/`,**用现成脚本,不要临场重写引擎**。先判断走哪条路线。

**输出标准(所有路线强制执行):[docs/output_standard.md](docs/output_standard.md)。**要点:
- 原图不动,不另插译文页。
- 成段文字页(A4 正文、说明、信函、协议、目录)向右加宽一栏放中文:整段整句翻译,与俄文段落顶部对齐,字号统一且不小于约 8pt。
- 图纸短标注:中文放在俄文正下方,不放上方、不放远处。
- 图签和侧栏:归入一行「图签译文」。
- 认可图纸的 `layout_sha256` 不得改变。

## 0. 普查,选路线
- `python scripts/01_extract.py`(在项目文件夹里,带 project.toml)。**有文字层** → 主流程(第 1 节);
  **扫描件 / SHX 线条字** → 扫描件流程(第 2 节);DWG → DXF 后走 `scripts/cad/`(README)。
- 项目资料放在本仓库**之外**的项目文件夹;业主名、印章文字、图号前缀等写进 project.toml,绝不写进代码。

## 1. 有文字层的 PDF(主流程)
翻译记忆库循环,直到 `missing TM keys: 0`:
`20_build.py all --dry` → `41_export_todo.py` → 翻译成 batch.tsv(数字保持 ⟨n⟩ 占位符)→ `40_merge_tm.py` → `20_build.py all` → `43_issues.py`。命令见 README。

## 2. 扫描件(无文字层)——一条命令,详见 docs/pipeline.md、docs/scanned.md
```
python scripts/rudraw.py doctor                         # 环境自检(Tesseract rus、字体、claude CLI)
python scripts/rudraw.py init <项目文件夹> --pdf X.pdf   # 生成 project.toml,自动判断扫描件/文字层
python scripts/rudraw.py run --dry-run                  # 先看计划和预计 LLM 调用数
python scripts/rudraw.py run                            # 可续跑;status / cost 看进度和花费
```
- 流程:ocr → rapid → segments → tm → translate → notes → automap → build → audit → audit_review → check → qa → visual → report。
- LLM 工作(短语翻译、页面说明、审计复核、看图复核)全部由流水线里**受约束的任务**完成(固定输入包 + schema + 代码校验 + 缓存 + 成本账本)。
  **agent 不得手写 map/notes**,也不要另开翻译/映射任务;`prompt_map.md` 只是个别页面自动路线处理不了时的备用。
- 跨册复用:上一册的 `tm/project.json` 加进 `[tm] shared`;预算由 `[llm] budget_usd` 硬限制。
- 关卡不过(`run` 退出码 2 或某阶段失败):`rudraw.py status` 看哪一关 → 读 `work/job/logs/<阶段>.log` → 修原因 →
  `rudraw.py run --from <阶段>` 重跑该阶段(相同输入走缓存,不重复花钱)。

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
- `rudraw.py cost` 看账本;图签、表头、通用说明走术语表和翻译记忆,只翻新出现的短语。
- 看图复核(`[job] visual`)最贵:只看 QA 标记的图块;之后只看改动页(`run --pages`)。
