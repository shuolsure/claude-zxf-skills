"""JSON 白名单校验。规则从 config/whitelist.yaml 读入。"""
from __future__ import annotations

import json
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None  # type: ignore


_RULES: dict | None = None


def load_rules(path: Path) -> dict:
    global _RULES
    if yaml is None:
        raise RuntimeError("缺 pyyaml 依赖：pip install pyyaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    _RULES = {
        "profile_type": set(data["profile_type"]),
        "recommendation_type": set(data["recommendation_type"]),
        "knowledge_type": set(data["knowledge_type"]),
        "dialogue_type": set(data["dialogue_type"]),
        "signal": set(data["signal"]),
        "limits": data["limits"],
    }
    return _RULES


def _rules() -> dict:
    if _RULES is None:
        raise RuntimeError("validate.load_rules() 未调用")
    return _RULES


def validate_json(payload: dict, name: str = "(inline)") -> list[str]:
    """返回错误清单；空列表=通过。"""
    r = _rules()
    errs: list[str] = []

    if payload.get("skipped"):
        if payload.get("dialogue_type") != "non_dialog":
            errs.append(f"[{name}] skipped=True 但 dialogue_type 非 non_dialog")
        return errs

    dt = payload.get("dialogue_type")
    pv = payload.get("processing_version", "")
    is_monolog = pv.startswith("v1_phase_monolog") or dt == "non_dialog"
    if not is_monolog and dt not in r["dialogue_type"]:
        errs.append(f"[{name}] dialogue_type={dt!r} 不在枚举 {r['dialogue_type']}")

    sig = payload.get("effective_signal")
    if sig is not None and sig not in r["signal"]:
        errs.append(f"[{name}] effective_signal={sig!r} 不在枚举")

    allow_new_type = "type_new_reason" in (payload.get("signal_notes", "") or "")
    lim = r["limits"]

    for i, p in enumerate(payload.get("profile_tags", []) or []):
        v = p.get("value", "")
        if len(v) > lim["profile_value"]:
            errs.append(f"[{name}] profile_tags[{i}].value 超长 {len(v)}>{lim['profile_value']}: {v!r}")
        t = p.get("type", "")
        if t not in r["profile_type"] and not allow_new_type:
            errs.append(f"[{name}] profile_tags[{i}].type={t!r} 不在白名单且未声明 type_new_reason")

    for i, rec in enumerate(payload.get("recommendation_tags", []) or []):
        v = rec.get("value", "")
        if len(v) > lim["recommendation_value"]:
            errs.append(f"[{name}] recommendation_tags[{i}].value 超长 {len(v)}>{lim['recommendation_value']}: {v!r}")
        t = rec.get("type", "")
        if t not in r["recommendation_type"] and not allow_new_type:
            errs.append(f"[{name}] recommendation_tags[{i}].type={t!r} 不在白名单")

    for i, k in enumerate(payload.get("knowledge_tags", []) or []):
        v = k.get("value", "")
        if len(v) > lim["knowledge_value"]:
            errs.append(f"[{name}] knowledge_tags[{i}].value 超长 {len(v)}>{lim['knowledge_value']}: {v!r}")
        t = k.get("type", "")
        if t not in r["knowledge_type"] and not allow_new_type:
            errs.append(f"[{name}] knowledge_tags[{i}].type={t!r} 不在知识白名单")

    for i, q in enumerate(payload.get("quotes", []) or []):
        v = q.get("value", "")
        if len(v) > lim["quote_value"]:
            errs.append(f"[{name}] quotes[{i}].value 超长 {len(v)}>{lim['quote_value']}: {v!r}")
        if q.get("type") != "金句":
            errs.append(f"[{name}] quotes[{i}].type 必须为 '金句'")

    return errs


def validate_file(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"[{path.name}] JSON 不合法: {e}"]
    return validate_json(data, path.name)
