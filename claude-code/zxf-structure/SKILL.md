---
name: zxf-structure
description: 从 classification/index.json 取待跑清单，把张雪峰转录片段跑成结构化 JSON 知识库（粗修→精修→校验→入库）。当用户说"跑 N 份对话片段"、"跑 N 份独白片段"、"用 haiku 精修跑 X 份"、"跑 BVxxx"、"查结构化进度"、或提及 zxftrans 结构化/粗修/精修/phase_dialog/phase_monolog 时使用。即使用户不用"skill"字样，只要任务是对已分类的 zxftrans 片段做结构化 JSON 抽取，就用这个 skill。依赖 zxf-classify 已经打好标签。
---

# zxf-structure：张雪峰转录结构化流水线

## 脚本路径（重要）

所有脚本都在 skill 目录，用绝对路径调用：

```bash
PIPELINE=~/.claude/skills/zxf-structure/scripts/pipeline.py
VALIDATE=/Users/shuo/Documents/Claude/daxue/my-advisor-app/knowledge/zxftrans_structured/scripts/validate_json.py
```

`validate_json.py` 是项目自有脚本（含白名单，随项目迭代），放在项目里。其他一律走 skill 绝对路径，不要复制。

## 冷启动一次

如果 `structured/phase_dialog/` 或 `phase_monolog/` 已经有历史成品（index 里没对应 BV 或 processed 不对），先跑一次对齐：

```bash
python3 $PIPELINE --reconcile
```

输出会列出 orphan BVs（成品在但 index 无记录）——用 zxf-classify 对这些 BV 补分类即可。

## 定位

从 `classification/index.json` 按条件取清单，对每份片段跑粗修→精修→校验→入库。独立运行、按 processed 状态增量执行，失败走 needs_review 兜底。

**不做**：分类判定（那是 zxf-classify）、整场切块（未来 zxf-segment）、prompt 升版（人工）。

## 触发识别

| 用户说 | 清单条件 | 流程 |
|---|---|---|
| "跑 N 份对话片段" | content_type=对话 & segment_type=片段 & processed=pending | 粗修→精修→入库 |
| "跑 N 份独白片段" | content_type∈{独白,专题} & segment_type=片段 & processed=pending | 一次性 monolog |
| "用 haiku 精修跑 N 份对话片段" | 同上对话条件 | refine_model=haiku |
| "跑 BVxxx" | 指定 BV（忽略 processed） | 按 content_type 走对应流程 |
| "查结构化进度" / "结构化到哪了" | — | 只读汇报 |
| "重跑 BVxxx" | 指定 BV，清掉旧成品 | 同上 |

解析规则：
- N 默认 5，用户说数字以用户为准
- refine_model 默认 `sonnet`，用户说 "haiku 精修"/"用 haiku" 时切 `haiku`
- 粗修默认固定用 haiku（成本考虑），不暴露给用户选

## 前置检查

运行前用 `$PIPELINE --precheck` 确认：
- `classification/index.json` 存在且非空（否则让用户先跑 zxf-classify）
- `$VALIDATE` 存在
- 三个 prompt 文件都在 prompts/ 下

任一缺失直接报错退出，不要猜。

## 核心流程

### Step 1：取清单

```bash
python3 $PIPELINE --list \
  --content-type 对话 \
  --segment-type 片段 \
  --processed pending \
  --limit N
```

返回 `[{bv, file_path, content_type, signal_hint}, ...]`。用户指定 BV 时用 `--bv BVxxx`。

### Step 2：按 content_type 派发

**职责分工铁律**（**勿违反**）：

| 角色 | 做什么 | 不做什么 |
|---|---|---|
| subagent | **只产出 JSON 字符串**（在最后一段消息里返回完整 JSON） | 不写盘、不跑 validate、不调 pipeline、不 mark-done |
| 主 agent | 写盘、跑 $VALIDATE、失败重试、mark-done、汇报 | 不做内容抽取 |

**为什么**：subagent 默认权限受限（Bash 常被拦截授权），让它做 IO 会导致并行度塌掉到 1。把 IO 全交主 agent，subagent 只做纯计算，5 份并行才能真跑起来。

---

**对话类**（content_type=对话）→ 两步走：

**Step 2a：粗修（Haiku subagent，5 份并行）**

并行派 5 个 Haiku subagent，每个 subagent 的 prompt 模板：

```
[读 prompts/phase_dialog_prompt_haiku.md 作 system]

处理 BV: {bv}
源文件完整文本：
{text_content}  ← 主 agent 用 Read 先读好，拼进 prompt

要求：
- 按 prompt 产出完整 JSON
- 把 JSON 作为最后一段消息返回（用 ```json ... ``` fence 包裹）
- 不要写文件，不要跑任何 shell 命令
```

主 agent 拿到 subagent 返回后：
1. 从 fence 里解出 JSON
2. 写 `structured/phase_dialog_draft/{BV}.json`
3. 跑 `python3 $VALIDATE <path>`
4. 校验失败：重新派同样 subagent 1 次（共 2 次）；仍失败写 `_needs_review/{BV}.json` + mark-done=needs_review

**Step 2b：精修（1 个 refine subagent，一次处理 5 份）**

5 份粗修 JSON 都落盘校验通过后，主 agent 把 5 份 draft 内容拼进 1 个 refine subagent 的 prompt：

```
[读 prompts/refine_prompt.md 作 system]

以下是 5 份粗修 JSON（请一次性处理，保证横向一致）：

=== BV1xxx ===
{draft_json_1}

=== BV2xxx ===
{draft_json_2}
...

要求：
- 先输出问题清单（≤500 字）
- 再按顺序输出 5 份精修后的 JSON，每份用 ```json {BV} ... ``` fence 包裹
- 不要写文件，不要跑 shell
```

主 agent 拿到返回后：
1. 解析出 5 份 JSON
2. 每份写到 `structured/phase_dialog/{BV}.json`
3. 跑 $VALIDATE
4. 校验失败：单份返回 refine subagent 重修 1 次；仍失败走 needs_review

**独白/专题类**（content_type∈{独白,专题}）→ 单步：

5 份并行派 Sonnet subagent，同样模式（subagent 只返回 JSON，主 agent 写 `structured/phase_monolog/{BV}.json` + 校验）。

### Step 3：回写索引（**仅主 agent 可调**）

**硬约束**：只有主 agent 在整个对话类流水线（粗修+精修+校验）全部完成**之后**，才调：

```bash
python3 $PIPELINE --mark-done <BV> --status done
```

- subagent 不得调 `--mark-done`
- 粗修完成后不要 mark-done（那时精修还没跑，标 done 是欺骗）
- 只有 `phase_dialog/{BV}.json` 或 `phase_monolog/{BV}.json` 已落盘并过校验，才 mark done
- 失败的标 `needs_review`，整场跳过的标 `skipped`

### Step 4：汇报

调 `$PIPELINE --report` 输出：
- 本批处理数 / 粗修通过率 / 精修通过率
- dialogue_type 分布（B_qa / B_emotional / non_dialog）
- signal 分布（high / medium / low）
- 累计 phase_dialog+phase_monolog 样本数
- 索引剩余 pending 数
- 下一步建议（凑整到 MVP 150 / 触发 30 份复盘等）

## 并行度约定

| 阶段 | 并行度 | 原因 |
|---|---|---|
| 粗修 Haiku | 5 份并行 | 独立任务，成本低 |
| 精修 | 5 份/批串行 | 需要横向一致性 |
| 独白 Sonnet | 5 份并行 | 独立任务 |

N > 10：按 5 份一轮分轮跑，每轮之间汇报一次小结。

## 错误处理

| 情况 | 处理 |
|---|---|
| 校验失败 | 同 agent 重试 1 次（共 2 次） |
| 仍失败 | 写 `_needs_review/{BV}.json` + processed=needs_review |
| 原文 txt 不存在 | processed=skipped + skip_reason |
| JSON 解析失败 | 当成校验失败处理 |

## 路径约定

| 路径 | 说明 |
|---|---|
| `.../zxftrans_structured/classification/index.json` | 读：取清单；写：回状态 |
| `.../zxftrans_structured/prompts/phase_dialog_prompt_haiku.md` | 粗修 prompt |
| `.../zxftrans_structured/prompts/refine_prompt.md` | 精修 prompt |
| `.../zxftrans_structured/prompts/phase_monolog_prompt.md` | 独白 prompt |
| `.../zxftrans_structured/scripts/validate_json.py` | 校验脚本 |
| `.../zxftrans_structured/structured/phase_dialog_draft/{BV}.json` | 粗修成品 |
| `.../zxftrans_structured/structured/phase_dialog/{BV}.json` | 精修成品 |
| `.../zxftrans_structured/structured/phase_monolog/{BV}.json` | 独白成品 |
| `.../zxftrans_structured/structured/_needs_review/{BV}.json` | 兜底 |

基础目录：`/Users/shuo/Documents/Claude/daxue/my-advisor-app/knowledge/zxftrans_structured/`

## 汇报模板

```
结构化批次完成：{n}/{N} 份（对话｜独白）

粗修：{ok}/{n} 通过（{retry} 重试，{fail} needs_review）
精修：{ok}/{n} 通过（模型：{refine_model}）
dialogue_type：B_qa {a} | B_emotional {b} | non_dialog {c}
signal：high {h} | medium {m} | low {l}

累计：phase_dialog {D} 份 | phase_monolog {M} 份（MVP 目标 150）
索引剩余：对话片段 pending {p1} | 独白片段 pending {p2}

下一步建议：
- {condition}: 再跑 {N} 份
- {condition}: 做累计复盘（每 30 份触发）
```

## 边界

- 不改 prompt（人工升版）
- 不改 validate_json.py 白名单（人工改）
- 整场文件打 skipped 不跑
- needs_review 等人工处理，不自动再试
- 单 BV 已有成品：除非用户说"重跑"，否则跳过
