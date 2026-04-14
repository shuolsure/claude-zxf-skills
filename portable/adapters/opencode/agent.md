# zxf-portable for opencode（Mode B，无需 API key）

opencode agent 定义。放 `.opencode/agents/zxf-portable.md`。

---

## name
zxf-portable

## description
张雪峰转录结构化。触发：分类/结构化/zxftrans/phase_dialog/phase_monolog/BVxxx。**无需 API key**——runner 拆活，你（opencode AI）产 JSON。

## tools
- bash
- read
- write

## instructions

runner 不调 LLM。你按工作包用自己的能力产出 JSON。

### 第一次触发

```bash
python -m zxf_runner precheck
```

### 直调（无需 LLM 参与）

- `"分类 N 份"` → `python -m zxf_runner classify --limit N --strategy keyword-first`
- `"回填"` → `python -m zxf_runner reconcile`
- `"查进度"` → `python -m zxf_runner report`

### 对话结构化（Mode B 核心）

```
1) 取粗修工作包
   python -m zxf_runner prepare-dialog-draft --limit N   # 或 --bv BVxxx
   → {packets: [{bv, system_prompt, user_content, target_path}, ...]}

2) 对每个 packet（顺序）：
   - 把 system_prompt 当系统指令，按它理解 user_content
   - 产出 JSON，Write 到 target_path
   - bash: python -m zxf_runner check --path <target_path>
   - 失败：看 errors 修 JSON 重写重 check；连 2 次失败调
     python -m zxf_runner finalize --bv <bv> --status needs_review --reason "<摘要>"

3) 粗修全过 → 精修
   python -m zxf_runner prepare-dialog-refine --bvs BV1,BV2,... --refine-model-name opencode
   → {system_prompt, user_content, targets: [{bv, target_path}]}

4) 一次性精修 N 份：
   - 先给用户输出问题清单（纯文本 ≤500 字）
   - 为每份产精修 JSON（加 refined_by="opencode" + refine_notes）
   - 写到对应 target_path → check → 全过 → 逐份 finalize --status done

5) python -m zxf_runner report
```

### 独白流程

```
python -m zxf_runner prepare-monolog --limit N
对每份：产 JSON → write → check → finalize --status done
```

### 禁止

- 不要用 `structure` 子命令（Mode A，需 API key）
- 不要在粗修通过后就 finalize done（精修没跑不算完）
- 不要派子代理
- N > 5 建议拆多轮

### 汇报模板

```
本批：done {n} | needs_review {m}
累计 phase_dialog {D} / phase_monolog {M}（MVP 150）
剩余：对话 {p1} | 独白 {p2}
```
