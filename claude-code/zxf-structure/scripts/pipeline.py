#!/usr/bin/env python3
"""
zxf-structure 流水线辅助脚本。

子命令：
  --precheck                            检查索引/prompt/校验脚本是否齐全
  --list [filters]                       从 index.json 取清单
  --bv BVxxx                             单份模式（与 --list 互斥）
  --mark-done BV --status S [--reason R]  回写 processed
  --report                               汇报累计状态

过滤器（与 --list 搭配）：
  --content-type 对话|独白|专题|其他|对话+独白+专题
  --segment-type 片段|整场
  --processed pending|done|skipped|needs_review
  --limit N
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(
    "/Users/shuo/Documents/Claude/daxue/my-advisor-app/knowledge/zxftrans_structured"
)
INDEX_PATH = BASE / "classification" / "index.json"
PROMPT_FILES = [
    BASE / "prompts" / "phase_dialog_prompt_haiku.md",
    BASE / "prompts" / "refine_prompt.md",
    BASE / "prompts" / "phase_monolog_prompt.md",
]
VALIDATOR = BASE / "scripts" / "validate_json.py"
DIALOG_DIR = BASE / "structured" / "phase_dialog"
MONOLOG_DIR = BASE / "structured" / "phase_monolog"
NEEDS_REVIEW_DIR = BASE / "structured" / "_needs_review"


def load_index() -> dict:
    if not INDEX_PATH.exists():
        print(json.dumps({"error": "index_not_found", "path": str(INDEX_PATH)}))
        sys.exit(1)
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def save_index(idx: dict) -> None:
    idx["last_updated"] = datetime.now().isoformat(timespec="seconds")
    INDEX_PATH.write_text(
        json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def cmd_precheck() -> None:
    problems = []
    if not INDEX_PATH.exists():
        problems.append(f"missing index: {INDEX_PATH}")
    else:
        idx = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        if not idx.get("items"):
            problems.append("index empty — run zxf-classify first")
    for p in PROMPT_FILES:
        if not p.exists():
            problems.append(f"missing prompt: {p}")
    if not VALIDATOR.exists():
        problems.append(f"missing validator: {VALIDATOR}")
    if problems:
        print(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps({"ok": True}))


def parse_content_type_filter(s: str | None) -> set[str] | None:
    if not s:
        return None
    return set(x.strip() for x in s.split("+") if x.strip())


def cmd_list(args) -> None:
    idx = load_index()
    ct_filter = parse_content_type_filter(args.content_type)
    items = []
    for bv, it in idx["items"].items():
        if ct_filter and it.get("content_type") not in ct_filter:
            continue
        if args.segment_type and it.get("segment_type") != args.segment_type:
            continue
        if args.processed and it.get("processed") != args.processed:
            continue
        items.append({
            "bv": bv,
            "file_path": it.get("file_path"),
            "file_name": it.get("file_name"),
            "content_type": it.get("content_type"),
            "segment_type": it.get("segment_type"),
            "signal_hint": it.get("signal_hint"),
            "char_count": it.get("char_count"),
            "processed": it.get("processed"),
        })
    items.sort(key=lambda x: x["file_path"] or "")
    if args.limit:
        items = items[: args.limit]
    print(json.dumps({"count": len(items), "items": items}, ensure_ascii=False))


def cmd_bv(bv: str) -> None:
    idx = load_index()
    it = idx["items"].get(bv)
    if not it:
        print(json.dumps({"error": "bv_not_in_index", "bv": bv}))
        sys.exit(1)
    print(json.dumps(it, ensure_ascii=False))


def cmd_mark_done(bv: str, status: str, reason: str | None) -> None:
    idx = load_index()
    it = idx["items"].get(bv)
    if not it:
        print(json.dumps({"error": "bv_not_in_index", "bv": bv}))
        sys.exit(1)
    it["processed"] = status
    if reason:
        it["skip_reason"] = reason
    it["processed_at"] = datetime.now().isoformat(timespec="seconds")
    save_index(idx)
    print(json.dumps({"ok": True, "bv": bv, "status": status}, ensure_ascii=False))


def count_jsons(d: Path) -> int:
    return len(list(d.glob("*.json"))) if d.exists() else 0


def scan_existing_bvs(d: Path) -> list[str]:
    if not d.exists():
        return []
    import re
    bvs = []
    for p in d.glob("*.json"):
        m = re.search(r"BV[A-Za-z0-9]{10}", p.name)
        if m:
            bvs.append(m.group(0))
    return bvs


def cmd_reconcile() -> None:
    """扫 structured/ 下现有成品，把对应 BV 在 index 里标 processed=done。
    不在 index 的 BV 只报告，不自动创建（原文可能已不存在，让用户决定）。
    """
    idx = load_index()
    items = idx["items"]

    dialog_bvs = scan_existing_bvs(DIALOG_DIR)
    monolog_bvs = scan_existing_bvs(MONOLOG_DIR)
    review_bvs = scan_existing_bvs(NEEDS_REVIEW_DIR)

    updated = {"done": [], "needs_review": [], "orphan": []}

    for bv in dialog_bvs + monolog_bvs:
        if bv not in items:
            updated["orphan"].append(bv)
            continue
        if items[bv].get("processed") != "done":
            items[bv]["processed"] = "done"
            items[bv]["processed_at"] = datetime.now().isoformat(timespec="seconds")
            updated["done"].append(bv)

    for bv in review_bvs:
        if bv not in items:
            updated["orphan"].append(bv)
            continue
        if items[bv].get("processed") != "needs_review":
            items[bv]["processed"] = "needs_review"
            updated["needs_review"].append(bv)

    save_index(idx)
    print(json.dumps({
        "ok": True,
        "marked_done": len(updated["done"]),
        "marked_needs_review": len(updated["needs_review"]),
        "orphan_bvs_not_in_index": updated["orphan"],
        "hint": "orphan BV 表示成品存在但分类 index 无记录——用 zxf-classify 扫一遍或手工补" if updated["orphan"] else None,
    }, ensure_ascii=False, indent=2))


def cmd_report() -> None:
    idx = load_index()
    items = idx["items"]
    pending_dialog = sum(
        1 for it in items.values()
        if it.get("content_type") == "对话"
        and it.get("segment_type") == "片段"
        and it.get("processed") == "pending"
    )
    pending_monolog = sum(
        1 for it in items.values()
        if it.get("content_type") in {"独白", "专题"}
        and it.get("segment_type") == "片段"
        and it.get("processed") == "pending"
    )
    dialog_done = count_jsons(DIALOG_DIR)
    monolog_done = count_jsons(MONOLOG_DIR)
    needs_review = count_jsons(NEEDS_REVIEW_DIR)

    lines = [
        f"累计成品：phase_dialog {dialog_done} 份 | phase_monolog {monolog_done} 份",
        f"MVP 进度：{dialog_done + monolog_done} / 150",
        f"needs_review：{needs_review} 份",
        "",
        f"索引剩余：对话片段 pending {pending_dialog} | 独白片段 pending {pending_monolog}",
    ]
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--precheck", action="store_true")
    g.add_argument("--list", action="store_true")
    g.add_argument("--bv", type=str)
    g.add_argument("--mark-done", type=str, help="BV to mark")
    g.add_argument("--report", action="store_true")
    g.add_argument("--reconcile", action="store_true", help="扫现有 structured/ 成品回填 index")

    ap.add_argument("--content-type", type=str, help="对话|独白|专题|其他；用 + 连多选")
    ap.add_argument("--segment-type", type=str)
    ap.add_argument("--processed", type=str)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--status", type=str, help="mark-done 用")
    ap.add_argument("--reason", type=str)

    args = ap.parse_args()

    if args.precheck:
        cmd_precheck()
    elif args.list:
        cmd_list(args)
    elif args.bv:
        cmd_bv(args.bv)
    elif args.mark_done:
        if not args.status:
            print(json.dumps({"error": "--status required"}))
            sys.exit(1)
        cmd_mark_done(args.mark_done, args.status, args.reason)
    elif args.report:
        cmd_report()
    elif args.reconcile:
        cmd_reconcile()


if __name__ == "__main__":
    main()
