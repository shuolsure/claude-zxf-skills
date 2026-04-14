---
name: zxf-portable
description: 张雪峰转录的通用结构化 skill（通用版 runner 薄壳）。当用户说"分类 N 份"、"跑 N 份对话片段"、"用 haiku 精修"、"跑 BVxxx"、"查进度"、或提及 zxftrans/phase_dialog/phase_monolog 时使用。所有业务逻辑在 python runner 里，主 agent 只负责翻译意图到命令行。不要派 subagent——runner 内部已处理并发。
---

# zxf-portable（Claude Code 适配）

## 定位

这是通用版 skill 的 Claude Code 适配层。真正的业务逻辑在 `portable/zxf_runner/`（同仓库），主 agent 只做一件事：把用户的中文意图翻译成 `python -m zxf_runner` 命令，用 Bash 跑，转述 stdout。

**不要**：派 subagent、读 prompts/ 内容、手写 JSON。这些都是 runner 的事。

## 前置

- 用户必须先跑过 `pip install -e <仓库>/portable`
- 环境变量 `ANTHROPIC_API_KEY`（必需）、`OPENAI_API_KEY`（可选）要在用户 shell 里

第一次触发时先跑 `python -m zxf_runner precheck`，不通过直接告诉用户缺什么。

## 触发词 → 命令映射

| 用户说 | 执行 |
|---|---|
| "分类 N 份" / "keyword-first 跑 N 份" | `python -m zxf_runner classify --limit N --strategy keyword-first` |
| "全量分类" | `python -m zxf_runner classify --limit 9999 --strategy keyword-first` |
| "跑 N 份对话片段" | `python -m zxf_runner structure --content-type dialog --limit N` |
| "用 haiku 精修跑 N 份" | 上面命令 + `--refine-model haiku` |
| "用 GPT 跑 N 份" | 上面命令 + `--draft-model gpt-cheap --refine-model gpt-top` |
| "跑 N 份独白片段" | `python -m zxf_runner structure --content-type monolog --limit N` |
| "跑 BVxxx" | `python -m zxf_runner structure --bv BVxxx` |
| "并发跑 N 份" | 结构化命令 + `--parallel 5` |
| "回填历史成品" | `python -m zxf_runner reconcile` |
| "查进度" / "进度到哪了" | `python -m zxf_runner report` |

N 默认：分类 20、结构化 5。

## 执行约定

- 用 Bash 工具跑命令，**不要**派 subagent（runner 内部并发够了）
- runner 把进度打到 stderr，结束给 stdout JSON 汇报
- 单条命令超过 10 分钟（比如 limit=30 还在 parallel=1）：建议用户加 `--parallel 5` 或拆批
- 失败 BV 在 stdout results 里会有 `status: "needs_review"`，告诉用户去 `_needs_review/` 看中间态

## 汇报模板

跑完后给用户看：

```
已执行：{命令}

{runner 返回的 summary JSON 精简呈现}

本批 done {n} | needs_review {n} | skipped {n}

累计产品（调 report 补充）：phase_dialog {x} / phase_monolog {y} / MVP 150
```

## 边界

- 不改 prompts / config（让用户手改）
- 不吞错：runner 报错就把 stderr 最后几行给用户
- 不自动把 needs_review 再跑一次——人工决定
