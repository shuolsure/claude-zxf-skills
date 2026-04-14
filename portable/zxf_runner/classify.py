"""分类逻辑：文件名+内容启发式打分，映射 4 维度标签。

从 claude-code 版 classify.py 迁移，无 LLM 调用，纯启发式。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .config import SEGMENT_CHAR_THRESHOLD

FN_POSITIVE = {"家长": 2, "孩子": 2, "闺女": 2, "儿子": 2, "姑娘": 2, "女儿": 2}
FN_MEDIUM = {"分": 1, "怎么选": 1, "适合": 1}
FN_NEGATIVE = {"段子": -2, "耍帅": -2, "好男人": -2, "价值三百": -2}
FN_MONOLOG_PREFIX = "张雪峰："

CONTENT_POSITIVE = ["老师我想问", "我们家", "我闺女", "我儿子", "我女儿", "我家孩子", "我多少分", "我家"]
CONTENT_NEGATIVE = ["今天给大家", "各位家长", "在直播间", "很多人会问", "如果有家长问"]
CONTENT_LIST_STRUCTURE = [
    r"第[一二三四五六七八九十]",
    r"[一二三四五六七八九]个建议",
    r"[一二三四五六七八九]个误区",
    r"[一二三四五六七八九]问",
]

SIGNAL_HIGH_HINTS_IN_FILENAME = [r"\d{3}", r"强基", r"民办", r"复读", r"选科", r"考研"]
SIGNAL_LOW_HINTS_IN_FILENAME = ["情绪", "焦虑", "吐槽", "崩溃", "抑郁暗恋"]

BV_RE = re.compile(r"BV[A-Za-z0-9]{10}(?:S\d{2})?")


def extract_bv(filename: str) -> str | None:
    m = BV_RE.search(filename)
    return m.group(0) if m else None


def score_filename(fn: str) -> tuple[float, list[str]]:
    score = 0.0
    notes: list[str] = []
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
    notes: list[str] = []
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


def classify_text(fn: str, text: str) -> dict:
    fn_score, fn_notes = score_filename(fn)
    ct_score, ct_notes = score_content(text)
    total = fn_score + ct_score
    has_list = has_list_structure(text)
    char_count = len(text)

    if total >= 2:
        content_type = "对话"
    elif total <= -2 and has_list:
        content_type = "专题"
    elif total <= -2:
        content_type = "独白"
    else:
        content_type = "其他"

    segment_type = "整场" if char_count > SEGMENT_CHAR_THRESHOLD else "片段"

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


def classify_file(path: Path) -> dict:
    fn = path.name
    bv = extract_bv(fn)
    if not bv:
        return {"error": "no_bv_in_filename", "file_name": fn}

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return {"error": f"read_failed:{e}", "bv": bv}

    now = datetime.now().isoformat(timespec="seconds")
    if not text.strip():
        return {
            "bv": bv, "file_name": fn, "file_path": str(path),
            "content_type": "其他", "segment_type": "片段",
            "signal_hint": "N/A", "char_count": 0,
            "processed": "skipped", "skip_reason": "empty_file",
            "classified_at": now,
        }

    cls = classify_text(fn, text)
    return {
        "bv": bv, "file_name": fn, "file_path": str(path),
        **{k: v for k, v in cls.items() if not k.startswith("_")},
        "processed": "skipped" if cls["segment_type"] == "整场" else "pending",
        "skip_reason": "需切块" if cls["segment_type"] == "整场" else None,
        "classified_at": now,
        "_debug": {
            "score": cls["_score"],
            "notes": cls["_score_notes"],
            "has_list": cls["_has_list_structure"],
        },
    }
