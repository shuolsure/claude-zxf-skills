"""整场切片：启发式带货过滤 + 规则粗切。

切片流程（见 reports/skill_segment_plan.md）：
  Step 1 启发式过滤（本模块 heuristic_filter）
  Step 2 规则粗切（本模块 rule_split）
  Step 3 LLM 精修（工作包，由 prepare.py 生成，主 agent 处理）
  Step 4 落盘 + 建索引（finalize_segments）

所有字符 offset 一律指向**原文**（未过滤版本），避免下游对不上位置。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import classify as cls_mod
from . import index as idx_mod
from .config import SRC_DIR

# -------- 带货/闲聊关键词 --------

BLACKLIST_HIGH = {
    "苞米", "点赞", "点到", "小黄车", "福袋", "抽奖", "直播间福利",
    "购物车", "下单", "发货", "下播", "交给两位老师", "交给老师",
    "关注咱", "粉丝福利", "榜一", "礼物", "上车", "进直播间", "新朋友",
    "感谢打赏", "感谢礼物", "来个关注",
}

CHITCHAT = {
    "昨天晚上", "我睡了", "我家狗", "我同事", "我老婆", "我孩子学校",
    "回来的路上", "中午吃", "我吃的", "早上起来", "迟到", "起不来", "闹钟",
}

# 话题转换短语（用于规则粗切找切点）
TOPIC_SWITCH = [
    "下一位", "我们接下来", "好来连麦", "换个话题", "另外一个问题",
    "下一个问题", "再来一个", "下一位家长", "下一位老师", "欢迎下一位",
]

# 连麦开场（同时作为切点）
DIALOG_OPENER = [
    "老师我想问", "老师您好", "张老师", "我家孩子", "我闺女", "我儿子",
    "我女儿", "我家娃", "我想咨询",
]

# 列表结构（专题段开头）
LIST_OPENER = [
    r"第一[点个]", r"首先", r"第一[,，]",
]

SEGMENT_MIN_CHAR = 1500       # 片段最小长度（低于合并到邻段）
SEGMENT_MAX_CHAR = 8000       # 超过强制切
HARD_CUT_CHAR = 6000          # 连续无切点时的兜底

# 切句子（中英文标点）
SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])")


# ============================================================
# Step 1: 启发式带货/闲聊过滤
# ============================================================

def _split_sentences(text: str) -> list[tuple[int, int, str]]:
    """返回 [(start, end, sentence)]，offset 相对原文。"""
    out = []
    pos = 0
    for part in SENT_SPLIT_RE.split(text):
        if not part:
            continue
        end = pos + len(part)
        out.append((pos, end, part))
        pos = end
    return out


def _sentence_is_noise(sent: str) -> tuple[bool, str]:
    """单句是否属于噪声。返回 (是否噪声, 原因)。"""
    s = sent.strip()
    if len(s) < 3:
        return False, ""
    for kw in BLACKLIST_HIGH:
        if kw in s:
            return True, f"黑名单:{kw}"
    # 纯闲聊：累积 2 个闲聊词
    hits = [kw for kw in CHITCHAT if kw in s]
    if len(hits) >= 2:
        return True, f"闲聊:{'/'.join(hits)}"
    return False, ""


def heuristic_filter(text: str) -> dict:
    """识别明显噪声段。策略：**保守**，宁放过不误杀。

    连续 ≥3 句被判噪声才整块剔；单句闲聊不处理。
    """
    sents = _split_sentences(text)
    noise_mask = [False] * len(sents)
    reasons: list[str] = [""] * len(sents)
    for i, (_, _, s) in enumerate(sents):
        is_noise, why = _sentence_is_noise(s)
        noise_mask[i] = is_noise
        reasons[i] = why

    # 连续噪声 run-length（≥3 句）
    noise_segments: list[dict] = []
    i = 0
    while i < len(sents):
        if noise_mask[i]:
            j = i
            while j < len(sents) and noise_mask[j]:
                j += 1
            run_len = j - i
            if run_len >= 3:
                noise_segments.append({
                    "start": sents[i][0],
                    "end": sents[j - 1][1],
                    "sentence_count": run_len,
                    "sample_reason": reasons[i],
                    "preview": text[sents[i][0]:sents[i][0] + 80],
                })
            i = j
        else:
            i += 1

    noise_chars = sum(n["end"] - n["start"] for n in noise_segments)
    return {
        "original_length": len(text),
        "noise_segments": noise_segments,
        "noise_char_count": noise_chars,
        "kept_char_count": len(text) - noise_chars,
        "noise_ratio": round(noise_chars / max(1, len(text)), 3),
    }


# ============================================================
# Step 2: 规则粗切
# ============================================================

def _find_cut_points(text: str, noise_segments: list[dict]) -> list[tuple[int, str]]:
    """找候选切点，返回 [(pos, reason)]，pos 为切点在原文的位置。

    规则：
      - 噪声段的起/止 = 天然切点
      - 话题转换短语 = 切点
      - 连麦开场短语 = 切点
      - 列表开头 = 切点
    """
    cuts: list[tuple[int, str]] = [(0, "start")]

    # 噪声段边界
    for ns in noise_segments:
        cuts.append((ns["start"], "noise_start"))
        cuts.append((ns["end"], "noise_end"))

    # 话题词
    for kw in TOPIC_SWITCH:
        start = 0
        while True:
            i = text.find(kw, start)
            if i < 0:
                break
            cuts.append((i, f"topic:{kw}"))
            start = i + len(kw)

    for kw in DIALOG_OPENER:
        start = 0
        while True:
            i = text.find(kw, start)
            if i < 0:
                break
            cuts.append((i, f"dialog:{kw}"))
            start = i + len(kw)

    for pat in LIST_OPENER:
        for m in re.finditer(pat, text):
            cuts.append((m.start(), f"list:{pat}"))

    cuts.append((len(text), "end"))
    cuts.sort(key=lambda x: x[0])
    # 去重（同 pos 保留第一个 reason）
    dedup: list[tuple[int, str]] = []
    seen: set[int] = set()
    for pos, reason in cuts:
        if pos in seen:
            continue
        seen.add(pos)
        dedup.append((pos, reason))
    return dedup


def rule_split(text: str, noise_segments: list[dict]) -> list[dict]:
    """按切点切成候选段，合并过短段，拆开过长段。"""
    cuts = _find_cut_points(text, noise_segments)
    raw: list[dict] = []
    for i in range(len(cuts) - 1):
        start, reason = cuts[i]
        end, _ = cuts[i + 1]
        if end - start < 50:  # 碎屑
            continue
        raw.append({"start": start, "end": end, "reason": reason})

    # 合并过短段到后一段
    merged: list[dict] = []
    carry: dict | None = None
    for seg in raw:
        if carry:
            seg = {"start": carry["start"], "end": seg["end"],
                   "reason": carry["reason"] + "+" + seg["reason"]}
            carry = None
        length = seg["end"] - seg["start"]
        if length < SEGMENT_MIN_CHAR:
            carry = seg
            continue
        merged.append(seg)
    if carry:
        if merged:
            merged[-1]["end"] = carry["end"]
            merged[-1]["reason"] += "+tail"
        else:
            merged.append(carry)

    # 拆过长段：每 HARD_CUT_CHAR 字强制切（切在最近的句末）
    final: list[dict] = []
    for seg in merged:
        length = seg["end"] - seg["start"]
        if length <= SEGMENT_MAX_CHAR:
            final.append(seg)
            continue
        cur = seg["start"]
        while cur < seg["end"]:
            target = min(cur + HARD_CUT_CHAR, seg["end"])
            if target < seg["end"]:
                # 找最近句末
                window = text[target:target + 400]
                m = re.search(r"[。！？!?]", window)
                if m:
                    target = target + m.end()
            final.append({
                "start": cur, "end": target,
                "reason": seg["reason"] + "|hard_cut",
            })
            cur = target

    # 过滤：候选段大部分在 noise_segments 内的直接扔
    noise_ranges = [(n["start"], n["end"]) for n in noise_segments]

    def noise_overlap(s: int, e: int) -> int:
        ov = 0
        for ns, ne in noise_ranges:
            lo = max(s, ns)
            hi = min(e, ne)
            if hi > lo:
                ov += hi - lo
        return ov

    kept: list[dict] = []
    for i, seg in enumerate(final):
        length = seg["end"] - seg["start"]
        ov = noise_overlap(seg["start"], seg["end"])
        if ov / max(1, length) > 0.5:
            continue
        kept.append({
            "idx": len(kept),
            "start": seg["start"],
            "end": seg["end"],
            "char_count": length,
            "split_reason": seg["reason"],
            "needs_refine": length > SEGMENT_MAX_CHAR * 0.75 or "hard_cut" in seg["reason"],
            "preview_head": text[seg["start"]:seg["start"] + 120],
            "preview_tail": text[max(seg["start"], seg["end"] - 120):seg["end"]],
        })
    return kept


# ============================================================
# Step 4: 落盘 + 索引
# ============================================================

def _parent_bv(file_name: str) -> str | None:
    m = re.search(r"BV[A-Za-z0-9]{10}", file_name)
    return m.group(0) if m else None


def _extract_date_prefix(file_name: str) -> str:
    """从 '2024-10-11_15-32-18_xxx_BVxxx.txt' 抽前两段（日期+时间）。"""
    parts = file_name.split("_")
    if len(parts) >= 2 and re.match(r"\d{4}-\d{2}-\d{2}", parts[0]):
        return "_".join(parts[:2])
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


_FN_SAFE_RE = re.compile(r"[\\/:*?\"<>|\s]+")


def _safe_title(title: str, max_len: int = 24) -> str:
    t = _FN_SAFE_RE.sub("", title or "未命名")
    if len(t) > max_len:
        t = t[:max_len]
    return t or "未命名"


def finalize_segments(bv: str, plan: dict) -> dict:
    """把 plan 中的 segments 切出原文，落盘为新 txt，触发 classify。

    plan 结构：
      {
        "segments": [{"title", "start", "end", "content_type_hint"}, ...],
        "noise_report": {...}   # 可选
      }
    """
    it = idx_mod.get(bv)
    if not it:
        return {"ok": False, "error": f"BV {bv} 不在 index"}
    src_path = Path(it["file_path"])
    if not src_path.exists():
        return {"ok": False, "error": f"源文件不存在：{src_path}"}

    original = src_path.read_text(encoding="utf-8", errors="ignore")
    segs = plan.get("segments") or []
    if not segs:
        idx_mod.mark(bv, "skipped", reason="no_content")
        return {"ok": True, "written": 0, "message": "plan 里无有效段，原整场标 skipped(no_content)"}

    date_prefix = _extract_date_prefix(src_path.name)
    written: list[dict] = []

    for i, seg in enumerate(segs, start=1):
        start = int(seg["start"])
        end = int(seg["end"])
        if end <= start or start < 0 or end > len(original):
            continue
        body = original[start:end].strip()
        if len(body) < 500:
            continue
        title = _safe_title(str(seg.get("title", f"片段{i}")))
        seg_bv = f"{bv}S{i:02d}"
        new_name = f"{date_prefix}_{title}_{seg_bv}.txt"
        new_path = SRC_DIR / new_name
        new_path.write_text(body, encoding="utf-8")

        # 直接写进 index
        result = cls_mod.classify_file(new_path)
        if "error" in result:
            continue
        # 附加 parent 元信息；plan 产出的切片一律视作"片段/pending"，
        # 避免因字数超阈值被重判为"整场"后被 skip。
        result["parent_bv"] = bv
        result["segment_idx"] = i
        result["segment_span"] = [start, end]
        result["segment_title"] = seg.get("title") or title
        hint = seg.get("content_type_hint")
        if hint:
            result["segment_content_type_hint"] = hint
            # 若 LLM 给了明确 content_type_hint，优先采纳
            if hint in {"对话", "独白", "专题"}:
                result["content_type"] = hint
        result["segment_type"] = "片段"
        if result.get("processed") != "needs_review":
            result["processed"] = "pending"
            result.pop("skip_reason", None)
        idx_mod.upsert(seg_bv, result)
        written.append({
            "bv": seg_bv, "file": new_name,
            "char_count": result.get("char_count"),
            "content_type": result.get("content_type"),
        })

    # 原整场 mark 为 segmented
    idx_mod.mark(bv, "segmented", reason=f"split_into_{len(written)}_segments")
    return {"ok": True, "parent_bv": bv, "written": len(written), "segments": written}


# ============================================================
# 顶层入口
# ============================================================

def build_segment_plan(bv: str) -> dict:
    """runner 命令 `segment-plan` 的后端：只跑 Step 1/2，返回报告。"""
    it = idx_mod.get(bv)
    if not it:
        return {"error": f"BV {bv} 不在 index"}
    src_path = Path(it["file_path"])
    if not src_path.exists():
        return {"error": f"源文件不存在：{src_path}"}
    text = src_path.read_text(encoding="utf-8", errors="ignore")
    filt = heuristic_filter(text)
    candidates = rule_split(text, filt["noise_segments"])
    return {
        "bv": bv,
        "file": str(src_path),
        "filter": filt,
        "candidates": candidates,
        "candidate_count": len(candidates),
    }
