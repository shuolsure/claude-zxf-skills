#!/usr/bin/env python3
"""
zxf-classify 核心脚本：对单份 txt 做 4 维度分类。

用法：python3 classify.py <txt_file_path>
输出：JSON 到 stdout，包含 bv/content_type/segment_type/signal_hint/char_count/...
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

# ===== 启发式 v2 评分规则 =====
FN_POSITIVE = {
    "家长": 2, "孩子": 2, "闺女": 2, "儿子": 2, "姑娘": 2, "女儿": 2,
}
FN_MEDIUM = {"分": 1, "怎么选": 1, "适合": 1}
FN_NEGATIVE = {
    "段子": -2, "耍帅": -2, "好男人": -2, "价值三百": -2,
}
FN_MONOLOG_PREFIX = "张雪峰："  # -3

CONTENT_POSITIVE = ["老师我想问", "我们家", "我闺女", "我儿子", "我女儿", "我家孩子", "我多少分", "我家"]
CONTENT_NEGATIVE = ["今天给大家", "各位家长", "在直播间", "很多人会问", "如果有家长问"]
CONTENT_LIST_STRUCTURE = [
    r"第[一二三四五六七八九十]",
    r"[一二三四五六七八九]个建议",
    r"[一二三四五六七八九]个误区",
    r"[一二三四五六七八九]问",
]

# signal_hint 判定（仅对话类）
SIGNAL_HIGH_HINTS_IN_FILENAME = [
    r"\d{3}", # 三位数分数 430/656/680
    r"强基", r"民办", r"复读", r"选科", r"考研",
]
SIGNAL_LOW_HINTS_IN_FILENAME = ["情绪", "焦虑", "吐槽", "崩溃", "抑郁暗恋"]

SEGMENT_CHAR_THRESHOLD = 8000


def extract_bv(filename: str) -> str | None:
    m = re.search(r"BV[A-Za-z0-9]{10}", filename)
    return m.group(0) if m else None


def score_filename(fn: str) -> tuple[float, list[str]]:
    """
    独白前缀 `张雪峰：` 默认 -3；但若文件名同时含家长关键词（孩子/家长等），
    说明运营起标题时的 `张雪峰：` 只是频道前缀，真内容是连麦 —— 降权为 -1。
    """
    score = 0.0
    notes = []
    has_positive = any(kw in fn for kw in FN_POSITIVE)

    if FN_MONOLOG_PREFIX in fn:
        if has_positive:
            score -= 1
            notes.append(f"-1:含'{FN_MONOLOG_PREFIX}'前缀（与家长词共现，降权）")
        else:
            score -= 3
            notes.append(f"-3:含'{FN_MONOLOG_PREFIX}'前缀")

    for kw, v in FN_POSITIVE.items():
        if kw in fn:
            score += v
            notes.append(f"+{v}:文件名含'{kw}'")
            break
    for kw, v in FN_MEDIUM.items():
        if kw in fn and FN_MONOLOG_PREFIX not in fn:
            score += v
            notes.append(f"+{v}:文件名含'{kw}'")
            break
    for kw, v in FN_NEGATIVE.items():
        if kw in fn:
            score += v
            notes.append(f"{v}:文件名含'{kw}'")
    return score, notes


def score_content(text: str) -> tuple[float, list[str]]:
    score = 0.0
    notes = []
    snippet = text[:500]
    for kw in CONTENT_POSITIVE:
        if kw in snippet:
            score += 1
            notes.append(f"+1:开头含'{kw}'")
            break
    for kw in CONTENT_NEGATIVE:
        if kw in snippet:
            score -= 1
            notes.append(f"-1:开头含'{kw}'")
            break
    return score, notes


def has_list_structure(text: str) -> bool:
    head = text[:2000]
    for pat in CONTENT_LIST_STRUCTURE:
        if len(re.findall(pat, head)) >= 2:
            return True
    return False


def classify(fn: str, text: str) -> dict:
    fn_score, fn_notes = score_filename(fn)
    ct_score, ct_notes = score_content(text)
    total = fn_score + ct_score
    has_list = has_list_structure(text)
    char_count = len(text)

    # content_type
    if total >= 2:
        content_type = "对话"
    elif total <= -2 and has_list:
        content_type = "专题"
    elif total <= -2:
        content_type = "独白"
    else:
        content_type = "其他"

    # segment_type
    segment_type = "整场" if char_count > SEGMENT_CHAR_THRESHOLD else "片段"

    # signal_hint
    if content_type == "对话":
        if any(re.search(p, fn) for p in SIGNAL_HIGH_HINTS_IN_FILENAME):
            signal_hint = "high_candidate"
        elif any(kw in fn for kw in SIGNAL_LOW_HINTS_IN_FILENAME):
            signal_hint = "low_candidate"
        else:
            signal_hint = "medium_candidate"
    else:
        signal_hint = "N/A"

    return {
        "content_type": content_type,
        "segment_type": segment_type,
        "signal_hint": signal_hint,
        "char_count": char_count,
        "_score": total,
        "_score_notes": fn_notes + ct_notes,
        "_has_list_structure": has_list,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: classify.py <txt_path>", file=sys.stderr)
        sys.exit(2)

    path = Path(sys.argv[1])
    if not path.exists():
        print(json.dumps({"error": "file_not_found", "path": str(path)}))
        sys.exit(1)

    fn = path.name
    bv = extract_bv(fn)
    if not bv:
        print(json.dumps({"error": "no_bv_in_filename", "file_name": fn}))
        sys.exit(1)

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(json.dumps({"error": f"read_failed:{e}", "bv": bv}))
        sys.exit(1)

    if not text.strip():
        result = {
            "bv": bv, "file_name": fn, "file_path": str(path),
            "content_type": "其他", "segment_type": "片段",
            "signal_hint": "N/A", "char_count": 0,
            "processed": "skipped", "skip_reason": "empty_file",
            "classified_at": datetime.now().isoformat(timespec="seconds"),
        }
    else:
        cls = classify(fn, text)
        result = {
            "bv": bv, "file_name": fn, "file_path": str(path),
            **{k: v for k, v in cls.items() if not k.startswith("_")},
            "processed": "skipped" if cls["segment_type"] == "整场" else "pending",
            "skip_reason": "需切块" if cls["segment_type"] == "整场" else None,
            "classified_at": datetime.now().isoformat(timespec="seconds"),
            "_debug": {
                "score": cls["_score"],
                "notes": cls["_score_notes"],
                "has_list": cls["_has_list_structure"],
            },
        }

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
