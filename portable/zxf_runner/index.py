"""classification/index.json 读写、清单筛选、状态回写。"""
from __future__ import annotations

import json
import random
import re
from datetime import datetime
from pathlib import Path

from .config import INDEX_PATH, SRC_DIR

BV_RE = re.compile(r"BV[A-Za-z0-9]{10}")
KEYWORD_FIRST_HINTS = ["家长", "孩子", "闺女", "儿子", "姑娘", "女儿", "分"]


def load_index() -> dict:
    if not INDEX_PATH.exists():
        return {"version": "v1", "last_updated": None, "counts": {}, "items": {}}
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def save_index(idx: dict) -> None:
    idx["last_updated"] = datetime.now().isoformat(timespec="seconds")
    idx["counts"] = compute_counts(idx["items"])
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")


def compute_counts(items: dict) -> dict:
    keys = [
        "对话", "独白", "专题", "其他",
        "片段", "整场",
        "high_candidate", "medium_candidate", "low_candidate",
        "pending", "skipped", "done", "needs_review",
    ]
    counts: dict = {"total": len(items)}
    for k in keys:
        counts[k] = 0
    for it in items.values():
        for field in ("content_type", "segment_type", "signal_hint", "processed"):
            v = it.get(field)
            if v in counts:
                counts[v] += 1
    return counts


def scan_all_txt() -> dict[str, tuple[str, int]]:
    """返回 {bv: (file_path, size)}，重复 BV 保留最大字数那份。"""
    out: dict[str, tuple[str, int]] = {}
    for p in SRC_DIR.glob("*.txt"):
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


def list_pending(limit: int | None, strategy: str = "sorted") -> list[dict]:
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
        def score(it):
            name = Path(it["file_path"]).name
            hits = sum(1 for kw in KEYWORD_FIRST_HINTS if kw in name)
            return (-hits, it["file_path"])
        pending.sort(key=score)
    else:
        pending.sort(key=lambda x: x["file_path"])
    if limit is not None:
        pending = pending[:limit]
    return pending


def list_structured(
    content_types: set[str] | None,
    segment_type: str | None,
    processed: str | None,
    limit: int | None,
) -> list[dict]:
    """按条件筛选 index.items 返回清单。"""
    idx = load_index()
    out: list[dict] = []
    for bv, it in idx["items"].items():
        if content_types and it.get("content_type") not in content_types:
            continue
        if segment_type and it.get("segment_type") != segment_type:
            continue
        if processed and it.get("processed") != processed:
            continue
        out.append({"bv": bv, **it})
    out.sort(key=lambda x: x.get("file_path") or "")
    if limit:
        out = out[:limit]
    return out


def upsert(bv: str, payload: dict) -> None:
    idx = load_index()
    idx["items"][bv] = payload
    save_index(idx)


def mark(bv: str, status: str, reason: str | None = None) -> None:
    idx = load_index()
    it = idx["items"].get(bv)
    if not it:
        raise KeyError(f"BV {bv} 不在 index")
    it["processed"] = status
    if reason:
        it["skip_reason"] = reason
    it["processed_at"] = datetime.now().isoformat(timespec="seconds")
    save_index(idx)


def remove(bv: str) -> bool:
    idx = load_index()
    existed = idx["items"].pop(bv, None) is not None
    save_index(idx)
    return existed


def get(bv: str) -> dict | None:
    return load_index()["items"].get(bv)
