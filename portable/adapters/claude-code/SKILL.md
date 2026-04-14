---
name: zxf-portable
description: 张雪峰转录结构化 skill（通用版 runner 薄壳，**无需 API key**）。当用户说"分类 N 份"、"跑 N 份对话片段"、"跑 N 份独白"、"跑 BVxxx"、"查进度"、或提及 zxftrans/phase_dialog/phase_monolog 时使用。runner 不调 LLM，由主 agent 用自身 AI 能力按工作包产出 JSON。
---

# zxf-portable（Claude Code 适配，Mode B 默认）

## 定位

runner 只做**工作包分发 + 校验 + 索引管理**。真正"读原文、理解、产 JSON"由主 agent 用 Claude Code 内置 AI 完成——**不需要 ANTHROPIC_API_KEY**。

## 前置

第一次触发先跑：

```bash
python -m zxf_runner precheck
```

## 触发词 → 命令

| 用户说 | 执行 |
|---|---|
| "分类 N 份" | `python -m zxf_runner classify --limit N --strategy keyword-first` |
| "跑 N 份对话片段" | Mode B 对话流程（见下） |
| "跑 BVxxx" | Mode B 单 BV |
| "跑 N 份独白" | Mode B 独白流程（见下） |
| "回填" | `python -m zxf_runner reconcile` |
| "查进度" | `python -m zxf_runner report` |

N 默认：分类 20、结构化 5。

## Mode B 对话全流程（主 agent 按步执行）

### Step 1：取粗修工作包

```bash
python -m zxf_runner prepare-dialog-draft --limit N
# 或单份：--bv BVxxx
```

stdout 返回 `{count, instructions, packets: [{bv, system_prompt, user_content, target_path}, ...]}`。

### Step 2：对每个 packet 产粗修 JSON

**主 agent 自己做**（不派 subagent，顺序处理一份一份）：

1. 读取 `packet.system_prompt`（当 system 理解）和 `packet.user_content`（里面含原文）
2. 在自己的思考里按 system prompt 产出完整 JSON
3. 用 Write 工具把 JSON 写到 `packet.target_path`（覆盖）
4. Bash：`python -m zxf_runner check --path <target_path>`
5. 校验失败 → 看 errors，改 JSON 重写，再 check；连续 2 次失败则走 needs_review：
   `python -m zxf_runner finalize --bv <bv> --status needs_review --reason <errors 摘要>`

### Step 3：取精修工作包

粗修全过后：

```bash
python -m zxf_runner prepare-dialog-refine --bvs BV1,BV2,... --refine-model-name claude-code
```

返回一个 packet：`{system_prompt, user_content, targets: [{bv, target_path}, ...]}`。`user_content` 已经把 N 份 draft 按顺序拼好。

### Step 4：产精修 JSON

主 agent 按 system_prompt 一次性精修 N 份：

1. 先给用户输出问题清单（≤500 字纯文本，摘要即可）
2. 为每份产出精修 JSON，Write 到对应 `targets[i].target_path`
3. 每份里加 `refined_by: "claude-code"` 和 `refine_notes`
4. 逐份 `check`
5. 全过 → 逐份 `finalize --bv <bv> --status done`

### Step 5：汇报

跑 `python -m zxf_runner report`，告诉用户：

```
本批：done {n} | needs_review {m}
累计：phase_dialog {D} / phase_monolog {M}（MVP 150）
剩余：对话片段 pending {p1} | 独白 pending {p2}
```

## Mode B 独白流程（更简单）

```bash
python -m zxf_runner prepare-monolog --limit N
```

对每个 packet：产 JSON → Write → check → `finalize --status done`。无精修阶段。

## 关键约束

- 一次对话处理 N 份，每份 1-2 次 LLM 思考 + 精修 1 次，context 会膨胀。**N > 5 建议分多次用户指令**。
- Mode B runner **从不调 LLM** —— 看到 "API key" 相关报错就是用错了命令（可能误调 `structure`）。
- `finalize --status done` **只在精修 + check 都通过后**调。粗修通过不等于完成。
- 不要用 `structure` 子命令（那是 Mode A，需 API key）。

## 汇报模板

```
已完成：{n}/{N} 份（{content_type}）

{若有失败} needs_review：{bv1, bv2}（原因：{reason}）

累计产品（phase_dialog {D} / phase_monolog {M}，MVP 150）
索引剩余：对话 {p1} | 独白 {p2}
```

## 边界

- 不改 prompts / config（让用户手改 `portable/prompts/` 或 `portable/config/`）
- 不自动重跑 needs_review
- 不做分类判定（那是 runner 里 `classify` 的启发式逻辑，runner 自己能跑）
