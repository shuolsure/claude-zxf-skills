# claude-zxf-skills

张雪峰直播转录 → 结构化知识库的 skill 套件。包含两个版本：

```
claude-zxf-skills/
├── claude-code/       # v1：Claude Code 原生 skill（subagent 并发版）
│   ├── zxf-classify/
│   └── zxf-structure/
└── portable/          # v2：CLI 无关的通用版（Python runner，可在 Claude Code / Codex / opencode 跑）
```

## 两个版本的区别

| 维度 | claude-code/ | portable/ |
|---|---|---|
| 运行环境 | 仅 Claude Code | 任意支持 bash 的 AICLI |
| LLM 调用 | Claude Code 内部 subagent | Python runner 直接调 LLM API |
| 并发 | 5 份 subagent 并行 | 默认顺序，可选 `--parallel N` (asyncio) |
| 模型 | haiku / sonnet 硬编码 | 配置化（anthropic/openai/ollama） |
| 依赖 | 无（CC 内建） | python ≥3.9 + `anthropic` / `openai` / `litellm` |

## 用途

处理 `zxftrans/*.txt`（1000+ 份直播转录），为每份打分类标签，再对"对话片段"跑粗修→精修→校验→入库，产出 `phase_dialog/*.json` 和 `phase_monolog/*.json` 供下游 RAG 消费。

## 快速开始

- Claude Code 用户：把 `claude-code/zxf-classify` 和 `claude-code/zxf-structure` 复制到 `~/.claude/skills/` 即可
- 其他 CLI 用户：见 `portable/README.md`

## 路径约定

所有版本都假定数据目录是：

- 源转录：`/Users/shuo/Documents/Claude/daxue/zxftrans/*.txt`
- 产物根：`/Users/shuo/Documents/Claude/daxue/my-advisor-app/knowledge/zxftrans_structured/`

如需迁移，改 `portable/runner/config.py` 或 `claude-code/*/scripts/*.py` 顶部常量。
