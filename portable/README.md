# zxf-runner：CLI 无关的通用 skill

张雪峰转录处理的通用版本。业务逻辑（拆活/校验/索引/分类）在 Python runner，LLM 调用由 CLI 的主 agent 负责。任何能跑 bash 的 AI CLI（Claude Code / Codex / opencode）都能用。

## 两种工作模式

### Mode B：工作包模式（默认推荐，**无需 API key**）

runner 拆活成"工作包"（含 prompt + 原文 + 目标路径），主 agent 用自己的 AI 能力产 JSON 写盘，再调 runner 校验+入库。

- ✅ 用 CLI 订阅/内置 AI，不掏 API key
- ✅ Claude Code / Codex / opencode 一套代码
- ⚠ 主 agent 在一次对话里会连续做 N 次 LLM 思考，context 膨胀；N > 5 建议拆多轮

### Mode A：自驱模式（需 API key）

runner 内部直接调 anthropic/openai/ollama API，全程无需主 agent 介入 LLM 推理。

- ✅ 高吞吐，支持 `--parallel`
- ❌ 需要 `ANTHROPIC_API_KEY` 或 `OPENAI_API_KEY`，按调用付费

**没 key 就只用 Mode B。** 下面 "常用命令" 两种都列了。

## 安装

```bash
cd portable
pip install -e .                 # Mode B 够用（只需 pyyaml）
pip install -e ".[anthropic]"    # + Mode A anthropic
pip install -e ".[all]"          # + Mode A 全部 provider
```

## 配置

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...     # 如果要用 GPT

# 如果数据目录和默认不同
export ZXF_SRC_DIR=/path/to/zxftrans
export ZXF_OUT_DIR=/path/to/zxftrans_structured
```

默认路径：

- 源：`/Users/shuo/Documents/Claude/daxue/zxftrans/`
- 产物：`/Users/shuo/Documents/Claude/daxue/my-advisor-app/knowledge/zxftrans_structured/`

## 常用命令

### 共用（两种模式都能用）

```bash
python -m zxf_runner precheck
python -m zxf_runner classify --limit 30 --strategy keyword-first
python -m zxf_runner reconcile
python -m zxf_runner report
```

### Mode B（无 key）工作流

这些命令**不直接处理一份转录**，而是把活拆成"工作包"丢给主 agent。主 agent 读 packet 里的 prompt+原文，产出 JSON 写到 target_path，再调 runner 校验和入库。

```bash
# 1. 出对话粗修工作包
python -m zxf_runner prepare-dialog-draft --limit 5       # 或 --bv BVxxx

# （主 agent 按 packet 产 JSON → Write → check）
python -m zxf_runner check --path <draft_json_path>

# 2. 粗修全过 → 出精修工作包（一次多份保横向一致）
python -m zxf_runner prepare-dialog-refine --bvs BV1,BV2,BV3 --refine-model-name claude-code

# （主 agent 一次精修 N 份 → Write 每份 → check 每份）
# 3. 全过后逐份入库
python -m zxf_runner finalize --bv BVxxx --status done

# 失败走 needs_review
python -m zxf_runner finalize --bv BVxxx --status needs_review --reason "JSON 解析失败"

# 独白/专题（无精修阶段）
python -m zxf_runner prepare-monolog --limit 5
# ...write, check, finalize...
```

每个 CLI 的 adapter（`adapters/claude-code/SKILL.md` 等）已把这串流程写成主 agent 能直接跟随的步骤。

### Mode A（有 key）一键跑

```bash
python -m zxf_runner structure --content-type dialog --limit 5
python -m zxf_runner structure --content-type dialog --limit 5 --refine-model haiku
python -m zxf_runner structure --content-type dialog --limit 10 --parallel 5
python -m zxf_runner structure --bv BVxxx
python -m zxf_runner structure --content-type monolog --limit 5
python -m zxf_runner structure --content-type dialog --limit 5 \
    --draft-model gpt-cheap --refine-model gpt-top
```

## 模型别名（仅 Mode A 用）

见 `config/models.yaml`，默认提供：

| 别名 | provider | 模型 |
|---|---|---|
| `haiku` | anthropic | claude-haiku-4-5 |
| `sonnet` | anthropic | claude-sonnet-4-6 |
| `gpt-cheap` | openai | gpt-4o-mini |
| `gpt-top` | openai | gpt-5 |
| `local` | ollama | qwen2.5:14b |

加新模型：直接编辑 `config/models.yaml`。

## 白名单

`config/whitelist.yaml` 管理 profile/recommendation/knowledge 的 type 白名单和字数上限。改完不用重启，下次调用自动生效。

## 目录结构

```
portable/
├── README.md            ← 你在这里
├── pyproject.toml       ← pip install -e .
├── .env.example
├── zxf_runner/          ← 核心包
│   ├── __main__.py      ← CLI 入口
│   ├── config.py        ← 路径/常量
│   ├── llm.py           ← LLM provider 抽象
│   ├── classify.py      ← 启发式分类
│   ├── index.py         ← index.json R/W
│   ├── structure.py     ← 粗修+精修+校验流水线
│   ├── validate.py      ← JSON 白名单校验
│   └── reconcile.py     ← 历史成品回填
├── prompts/             ← 三份 prompt（粗修/精修/独白）
├── config/
│   ├── models.yaml      ← 模型别名映射
│   └── whitelist.yaml   ← 白名单 + 长度限制
└── adapters/
    ├── claude-code/SKILL.md    ← Claude Code 适配
    ├── codex/prompt.md         ← Codex 适配
    └── opencode/agent.md       ← opencode 适配
```

## CLI 适配

每个 CLI 的适配层只做一件事：把用户说的中文意图翻译成 runner 命令。见 `adapters/` 下各 CLI 的文件。

## 状态模型

每份 BV 在 `classification/index.json` 里有 `processed` 字段：

- `pending` — 已分类，未结构化
- `done` — 结构化成品入库
- `needs_review` — 跑失败（2 次重试都挂了），等人工看
- `skipped` — 整场文件/空文件/识别为 non_dialog

断点续跑：再次 `structure --limit N` 会自动跳过 `done`/`skipped`/`needs_review`，从 `pending` 继续。

## 错误处理

LLM 失败：runner 内部重试 2 次（总 3 次调用），仍失败写 `_needs_review/{BV}.json` + mark `needs_review`。

校验失败：同上，再跑一次；仍失败走 needs_review。

JSON 解析失败：按校验失败处理。

所有错误不终止整批，只跳过当前 BV，继续跑下一份。

## 调试

- `--parallel 1`（默认）：顺序执行，stderr 实时打印每份状态
- 失败 BV 的中间态保留在 `phase_dialog_draft/{BV}.json` 和 `_needs_review/{BV}.json`
- 想看某份具体问题：`python -m zxf_runner structure --bv BVxxx` 单跑
