---
name: zxf-classify
description: 为张雪峰直播转录文件（zxftrans/*.txt）打分类标签，产出 classification/index.json 供下游结构化流水线消费。当用户说"分类 N 份"、"全量分类"、"查分类进度"、"重分类 BVxxx"、或提及 zxftrans 分类、启发式筛选、内容类型判定时使用。支持增量运行，自动跳过已分类文件。即使用户不用"skill"字样，只要任务是对 zxftrans 目录做分类/筛选/索引，就用这个 skill。
---

# zxf-classify：张雪峰转录分类器

## 脚本路径（重要）

所有脚本都在 skill 目录，**用绝对路径调用**，不要复制到项目里：

```bash
CLASSIFY=~/.claude/skills/zxf-classify/scripts/classify.py
PROGRESS=~/.claude/skills/zxf-classify/scripts/progress.py
```

项目目录 `zxftrans_structured/scripts/` 里只放项目自有脚本（如 `validate_json.py`），**不要**把 skill 脚本拷过去——容易版本双份。

## 定位

扫 `/Users/shuo/Documents/Claude/daxue/zxftrans/*.txt`（1000+ 份），给每份打 4 维度标签写入索引，供下游 `zxf-structure` 按清单取样跑结构化。独立运行、增量去重。

**不做**：转录清洗、结构化本身、下游 RAG 接入。

## 触发识别

| 用户说 | 动作 |
|---|---|
| "分类 N 份" / "再分类 N 份" | 从未分类中取 N 份跑（N 解析为整数，默认 20） |
| "全量分类" / "把剩下的都分类了" | 跑完所有未分类 |
| "重分类 BVxxx" | 覆盖指定 BV 的分类记录 |
| "查分类进度" / "分类到哪了" | 只读索引汇报，不跑新任务 |

## 核心流程

### Step 1：确定待跑清单

1. 取清单：`python3 $PROGRESS --list-pending --limit N --strategy <sorted|random|keyword-first>`
   - `sorted`：按文件名字典序（默认，稳定可复现）
   - `random`：随机抽（适合探索整体分布）
   - `keyword-first`：优先抽文件名含"家长/孩子/闺女/儿子/姑娘/女儿/分"的——**冷启动强烈建议**，能把对话类样本前置，避免前 N 份全独白
2. 脚本内部逻辑：Glob `zxftrans/*.txt` → 从文件名提取 BV id → 对比 `index.json.items` → 按 strategy 排序 → 取前 N

### Step 2：跑分类脚本

对每份待分类文件运行：
```bash
python3 $CLASSIFY <txt_file_path>
```

脚本返回 JSON 对象（打分+4 维度标签）。脚本做的事：
- 读文件名打启发式分
- 读前 500 字打内容分
- 字数计算（决定 segment_type）
- 综合判定 content_type / segment_type / signal_hint
- 返回 JSON 写到 stdout

### Step 3：写入索引

`python3 $PROGRESS --update '<json_payload>'` 把结果 merge 进 `classification/index.json`。

**N > 50 时建议分批**（每批 50，避免单次工作量过大）。

### Step 4：汇报

调 `python3 $PROGRESS --report` 输出汇总。

**建议**：如果项目 `structured/` 下已有历史成品（比如之前手工跑过几份），分类完成后再跑一次：

```bash
python3 ~/.claude/skills/zxf-structure/scripts/pipeline.py --reconcile
```

这会把本批新分类里命中历史成品的 BV 自动标为 `processed=done`，避免重复跑。用户看到：
- 本批分类数 / 分布（对话/独白/专题/其他）
- 篇幅分布（片段/整场）
- 对话类 signal 预判
- 累计总数 / 剩余未分类数

## 分类判定规则（脚本内部，参考）

详见 `references/heuristic_v2.md`。关键要点：

- **content_type**：启发式打分后映射到 `对话 / 独白 / 专题 / 其他`
- **segment_type**：字数 ≤8000=片段，>8000=整场
- **signal_hint**：仅 content_type=对话 时填，基于文件名具体程度

## 路径约定

| 路径 | 说明 |
|---|---|
| `/Users/shuo/Documents/Claude/daxue/zxftrans/*.txt` | 转录源（只读） |
| `/Users/shuo/Documents/Claude/daxue/my-advisor-app/knowledge/zxftrans_structured/classification/index.json` | 索引产物 |

索引初次不存在时自动创建。

## 边界

- 非 `.txt` 文件：跳过
- 空文件 / 无 BV id 的文件：跳过并记 `skip_reason`
- 重复 BV（同一 BV 多个文件）：保留最大字数的那份
- `重分类` 场景：先删 `items[BV]` 再跑

## 不做的事

- 不做结构化（那是 zxf-structure 的事）
- 不给出切块建议（整场就打标搁置，等未来 zxf-segment skill）
- 不改启发式评分规则（规则更新需人工改 `scripts/classify.py`）

## 汇报模板

```
分类完成：{本批数}/{本批数} 份

分布：对话 {n} | 独白 {n} | 专题 {n} | 其他 {n}
篇幅：片段 {n} | 整场 {n}
对话类 signal 预判：high {n} | medium {n} | low {n}
累计索引：{classified} / {total_files} 份

未分类剩余：约 {pending} 份
```
