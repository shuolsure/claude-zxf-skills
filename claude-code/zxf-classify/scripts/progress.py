#!/usr/bin/env python3
"""
zxf-classify 索引维护脚本。

子命令：
  --list-pending [--limit N]   列出未分类 BV（从 zxftrans/*.txt 扣除已在 index 的）
  --update <json>              把单份分类结果 merge 进 index.json
  --report                     输出累计汇报（分布/篇幅/signal/剩余）
  --remove <BV>                删除指定 BV（用于重分类场景）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ZXFTRANS_DIR = Path("/Users/shuo/Documents/Claude/daxue/zxftrans")
INDEX_PATH = Path(
    "/Users/shuo/Documents/Claude/daxue/my-advisor-app/knowledge/"
    "zxftrans_structured/classification/index.json"
)

BV_RE = re.compile(r"BV[A-Za-z0-9]{10}")

# keyword-first 策略用：文件名含这些词优先（对话类候选）
KEYWORD_FIRST_HINTS = ["家长", "孩子", "闺女", "儿子", "姑娘", "女儿", "分"]


def load_index() -> dict:
    if not INDEX_PATH.exists():
        return {
            "version": "v1",
            "last_updated": None,
            "counts": {},
            "items": {},
        }
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def save_index(idx: dict) -> None:
    idx["last_updated"] = datetime.now().isoformat(timespec="seconds")
    idx["counts"] = compute_counts(idx["items"])
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def compute_counts(items: dict) -> dict:
    counts = {
        "total": len(items),
        "对话": 0, "独白": 0, "专题": 0, "其他": 0,
        "片段": 0, "整场": 0,
        "high_candidate": 0, "medium_candidate": 0, "low_candidate": 0,
        "pending": 0, "skipped": 0, "done": 0, "needs_review": 0,
    }
    for it in items.values():
        ct = it.get("content_type")
        if ct in counts:
            counts[ct] += 1
        st = it.get("segment_type")
        if st in counts:
            counts[st] += 1
        sh = it.get("signal_hint")
        if sh in counts:
            counts[sh] += 1
        pr = it.get("processed")
        if pr in counts:
            counts[pr] += 1
    return counts


def scan_all_txt() -> dict:
    """返回 {bv: (file_path, char_size_approx)}，重复 BV 保留最大字数那份。"""
    out = {}
    for p in ZXFTRANS_DIR.glob("*.txt"):
        m = BV_RE.search(p.name)
        if not m:
            continue
        bv = m.group(0)
        try:
            sz = p.stat().st_size
        except OSError:
            sz = 0
        if bv not in out or sz > out[bv][1]:
            out[bv] = (str(p), sz)
    return out


def cmd_list_pending(limit: int | None, strategy: str = "sorted") -> None:
    import random
    idx = load_index()
    classified = set(idx["items"].keys())
    all_files = scan_all_txt()
    pending = [
        {"bv": bv, "file_path": fp, "size": sz}
        for bv, (fp, sz) in all_files.items()
        if bv not in classified
    ]

    if strategy == "random":
        random.shuffle(pending)
    elif strategy == "keyword-first":
        def score(item):
            name = Path(item["file_path"]).name
            hits = sum(1 for kw in KEYWORD_FIRST_HINTS if kw in name)
            return (-hits, item["file_path"])
        pending.sort(key=score)
    else:  # sorted
        pending.sort(key=lambda x: x["file_path"])

    if limit is not None:
        pending = pending[:limit]
    print(json.dumps({
        "total_files": len(all_files),
        "classified": len(classified),
        "pending_returned": len(pending),
        "pending": pending,
    }, ensure_ascii=False))


def cmd_update(payload: str) -> None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"bad_json:{e}"}))
        sys.exit(1)

    bv = data.get("bv")
    if not bv:
        print(json.dumps({"error": "missing_bv"}))
        sys.exit(1)

    idx = load_index()
    idx["items"][bv] = data
    save_index(idx)
    print(json.dumps({"ok": True, "bv": bv, "total": len(idx["items"])}, ensure_ascii=False))


def cmd_remove(bv: str) -> None:
    idx = load_index()
    removed = idx["items"].pop(bv, None)
    save_index(idx)
    print(json.dumps({"ok": True, "bv": bv, "removed": removed is not None}, ensure_ascii=False))


def cmd_report() -> None:
    idx = load_index()
    all_files = scan_all_txt()
    counts = idx.get("counts") or compute_counts(idx["items"])
    classified = len(idx["items"])
    total = len(all_files)
    pending = total - classified

    lines = [
        f"累计索引：{classified} / {total} 份",
        f"未分类剩余：{pending} 份",
        "",
        f"分布：对话 {counts.get('对话', 0)} | 独白 {counts.get('独白', 0)} | "
        f"专题 {counts.get('专题', 0)} | 其他 {counts.get('其他', 0)}",
        f"篇幅：片段 {counts.get('片段', 0)} | 整场 {counts.get('整场', 0)}",
        f"对话 signal：high {counts.get('high_candidate', 0)} | "
        f"medium {counts.get('medium_candidate', 0)} | low {counts.get('low_candidate', 0)}",
        f"processed：pending {counts.get('pending', 0)} | done {counts.get('done', 0)} | "
        f"skipped {counts.get('skipped', 0)} | needs_review {counts.get('needs_review', 0)}",
    ]
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list-pending", action="store_true")
    g.add_argument("--update", type=str, help="JSON payload of one classification result")
    g.add_argument("--remove", type=str, help="BV to remove")
    g.add_argument("--report", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--strategy", choices=["sorted", "random", "keyword-first"], default="sorted")
    args = ap.parse_args()

    if args.list_pending:
        cmd_list_pending(args.limit, args.strategy)
    elif args.update:
        cmd_update(args.update)
    elif args.remove:
        cmd_remove(args.remove)
    elif args.report:
        cmd_report()


if __name__ == "__main__":
    main()
