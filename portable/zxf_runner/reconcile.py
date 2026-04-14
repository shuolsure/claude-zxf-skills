"""回填：扫 structured/ 下已有成品，标记 index 里的 processed 状态。"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .config import DIALOG_DIR, MONOLOG_DIR, NEEDS_REVIEW_DIR
from .index import load_index, save_index

BV_RE = re.compile(r"BV[A-Za-z0-9]{10}")


def scan_existing_bvs(d: Path) -> list[str]:
    if not d.exists():
        return []
    out: list[str] = []
    for p in d.glob("*.json"):
        m = BV_RE.search(p.name)
        if m:
            out.append(m.group(0))
    return out


def reconcile() -> dict:
    idx = load_index()
    items = idx["items"]
    result = {"done": [], "needs_review": [], "orphan": []}

    for bv in scan_existing_bvs(DIALOG_DIR) + scan_existing_bvs(MONOLOG_DIR):
        if bv not in items:
            result["orphan"].append(bv)
            continue
        if items[bv].get("processed") != "done":
            items[bv]["processed"] = "done"
            items[bv]["processed_at"] = datetime.now().isoformat(timespec="seconds")
            result["done"].append(bv)

    for bv in scan_existing_bvs(NEEDS_REVIEW_DIR):
        if bv not in items:
            result["orphan"].append(bv)
            continue
        if items[bv].get("processed") != "needs_review":
            items[bv]["processed"] = "needs_review"
            result["needs_review"].append(bv)

    save_index(idx)
    return {
        "marked_done": len(result["done"]),
        "marked_needs_review": len(result["needs_review"]),
        "orphan_bvs_not_in_index": result["orphan"],
        "hint": ("orphan BV 表示成品存在但分类 index 无记录——跑一次 classify 补扫"
                 if result["orphan"] else None),
    }
