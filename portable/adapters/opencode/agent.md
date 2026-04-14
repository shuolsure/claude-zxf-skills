# zxf-portable for opencode

在 opencode 里，这是一个 agent 定义（或 plugin prompt）。放进 `.opencode/agents/zxf-portable.md` 或等价位置。

---

## name
zxf-portable

## description
张雪峰转录结构化。识别：分类 / 结构化 / zxftrans / phase_dialog / phase_monolog / BVxxx。

## instructions

调 `python -m zxf_runner` 完成所有工作。不要自行读 prompts、不要自行写 JSON——这些都是 runner 的责任。

**首次触发** → `python -m zxf_runner precheck`。问题全报给用户。

**命令映射**：

```
分类 N 份                  python -m zxf_runner classify --limit N --strategy keyword-first
跑 N 份对话片段             python -m zxf_runner structure --content-type dialog --limit N
用 haiku 精修跑 N 份         + --refine-model haiku
用 GPT 跑                   + --draft-model gpt-cheap --refine-model gpt-top
跑 N 份独白                 python -m zxf_runner structure --content-type monolog --limit N
跑 BVxxx                   python -m zxf_runner structure --bv BVxxx
并发跑 N 份                 + --parallel 5
回填                       python -m zxf_runner reconcile
进度                       python -m zxf_runner report
```

默认：分类 20 / 结构化 5 / 精修 sonnet。

**汇报**：
- stdout 有 JSON 汇总 —— 提取 summary + structured_products 告诉用户
- stderr 有实时进度
- 失败 BV 指向 `_needs_review/` 让用户去看

## tools
- shell (bash)
- read (optional, 仅用于展示产物 JSON 给用户看)

## 禁止
- 不要派子代理
- 不要自己写 JSON
- 不要读 prompts/ 内容
- 不要把 needs_review 自动重跑
