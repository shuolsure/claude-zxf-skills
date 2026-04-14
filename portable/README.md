# zxf-runner：CLI 无关的通用 skill

张雪峰转录处理的通用版本。所有业务逻辑下沉到 Python runner，CLI 层只做"用户意图 → 命令行参数"的翻译。任何能跑 bash 的 AI CLI（Claude Code / Codex / opencode / 自己搭的）都能用。

## 为什么做这个版本

`../claude-code/` 版本绑死 Claude Code 的 subagent 并发机制。本版本改成：

- runner 内部直接调 LLM API（anthropic / openai / ollama）
- 默认顺序执行，符合大多 CLI 只有单 agent 的现实
- 可选 `--parallel N` 线程池并发，不依赖 CLI 的 subagent 能力

详见 `../reports/skill_portable_plan.md`（需自行查阅上一层仓库 reports 目录）。

## 安装

```bash
cd portable
pip install -e ".[all]"          # 装 runner + anthropic + openai SDK
# 或按需最小化
pip install -e ".[anthropic]"    # 只用 Claude
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

```bash
# 环境检查
python -m zxf_runner precheck

# 分类 30 份（keyword-first 策略，对话优先）
python -m zxf_runner classify --limit 30 --strategy keyword-first

# 对话结构化：粗修 haiku + 精修 sonnet，5 份一批
python -m zxf_runner structure --content-type dialog --limit 5

# 换成 haiku 精修（省钱）
python -m zxf_runner structure --content-type dialog --limit 5 --refine-model haiku

# 用 GPT
python -m zxf_runner structure --content-type dialog --limit 5 \
    --draft-model gpt-cheap --refine-model gpt-top

# 独白/专题
python -m zxf_runner structure --content-type monolog --limit 5

# 单 BV
python -m zxf_runner structure --bv BV1xxxxxxxxx

# 并发 5 份（ThreadPool）
python -m zxf_runner structure --content-type dialog --limit 10 --parallel 5

# 历史成品回填 index
python -m zxf_runner reconcile

# 进度
python -m zxf_runner report
```

## 模型别名

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
