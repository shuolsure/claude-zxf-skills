# 结构化流水线参考

## 核心架构原则：subagent 零 IO

**subagent 只做纯计算（读 prompt、理解转录、产出 JSON），所有 IO 由主 agent 承担。**

为什么：subagent 默认 Bash/Write 权限受限，在跑的时候会卡在授权请求上，导致"5 份并行"实际退化成 1 份串行。把写盘/校验/mark-done 全交主 agent 后，subagent 可以 100% 并发跑起来。

| 职责 | 归属 |
|---|---|
| 读源文件 | **主 agent** 用 Read，把文本拼进 subagent prompt |
| 理解+抽取 JSON | subagent |
| 写盘 `structured/.../{BV}.json` | **主 agent** 用 Write |
| 跑 `$VALIDATE` 校验 | **主 agent** 用 Bash |
| 重试决策 | **主 agent** |
| `$PIPELINE --mark-done` | **主 agent**（且仅在精修完成后） |

## 对话类全流程

```
index.pending
  ↓ 主 agent: $PIPELINE --list ...
[清单 N 份]

【粗修阶段，5 份并行】
  ↓ 主 agent 用 Read 读每份原文
  ↓ 主 agent 把 5 个 subagent 并行派出（每个含完整原文）
  ↓ subagent 返回 JSON 字符串（fence 包裹，不写文件）
[5 份 JSON 字符串]
  ↓ 主 agent 解析 → Write 到 phase_dialog_draft/{BV}.json
  ↓ 主 agent 跑 $VALIDATE 每份
  ↓ 失败的重派 subagent 1 次；仍失败 → Write _needs_review + mark needs_review

【精修阶段，1 个 subagent 处理 5 份】
  ↓ 主 agent 拼 5 份 draft JSON 进 refine subagent prompt
  ↓ refine subagent 返回问题清单 + 5 份精修 JSON
  ↓ 主 agent 解析 → Write 到 phase_dialog/{BV}.json
  ↓ 主 agent 跑 $VALIDATE 每份
  ↓ 失败单份返回 refine subagent 重修 1 次；仍失败 → _needs_review

【收尾】
  ↓ 主 agent 每份调 $PIPELINE --mark-done <BV> --status done
```

## 独白/专题类全流程

```
$PIPELINE --list --content-type 独白+专题 --segment-type 片段 --processed pending
  ↓ 主 agent 读每份原文
  ↓ 5 份并行派 Sonnet subagent（只返回 JSON）
  ↓ 主 agent Write 到 phase_monolog/{BV}.json
  ↓ 主 agent 跑 $VALIDATE
  ↓ 失败重派；仍失败 → needs_review
  ↓ 主 agent mark-done
```

## 模型参数矩阵

| 阶段 | 默认模型 | 可调 |
|---|---|---|
| 对话粗修 | haiku | 不可改（成本） |
| 对话精修 | sonnet | 可切 haiku（refine_model=haiku） |
| 独白一次性 | sonnet | 不可改 |

## Subagent prompt 模板

### 粗修 Haiku（每份单独派）

```
你是粗修 subagent。

[粘贴 prompts/phase_dialog_prompt_haiku.md 全文作为 system]

处理：
- BV: {bv}
- 原文:
---
{full_text_content}
---

输出要求：
- 最后一条消息里用 ```json ... ``` fence 包裹完整 JSON
- 不要调用任何工具（Write/Bash/Edit），只输出文本
- 不要解释、不要分析，只给 JSON
```

### 精修（1 个派，处理 5 份）

```
你是精修 subagent，模型 {refine_model}。

[粘贴 prompts/refine_prompt.md 全文作为 system]

以下是 5 份粗修 draft JSON，一次性处理，保证横向一致：

=== BV1xxxxx ===
{draft_1}

=== BV2xxxxx ===
{draft_2}

...

输出要求：
- Step 1: 先输出问题清单（≤500 字纯文本）
- Step 2: 输出 5 份精修后的 JSON，每份用 ```json
  // BV: {bv}
  ... ``` fence 包裹
- 不要调用工具，只输出文本
- 每份加字段 refined_by={refine_model}、refine_notes
```

### 独白 Sonnet（每份单独派）

```
你是独白 subagent。

[粘贴 prompts/phase_monolog_prompt.md 全文作为 system]

处理：
- BV: {bv}
- 原文:
---
{full_text_content}
---

输出要求：
- 最后用 ```json ... ``` fence 包裹 JSON
- 不要调用工具
```

## 汇报时机

- 每批 5 份跑完：小结
- N > 10 时：按 5 份一轮分轮，每轮之间汇报
- 累计每 30 份：提醒"该做复盘了"
- 触及 MVP 150：提醒里程碑
