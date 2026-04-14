"""CLI 入口：python -m zxf_runner <command> [args]

两种工作模式：

A. 自驱模式（Mode A，需 API key）— runner 调 LLM 全流程：
  structure

B. 工作包模式（Mode B，推荐，**无需 API key**）— runner 拆活，主 agent 调 LLM：
  prepare-dialog-draft / prepare-dialog-refine / prepare-monolog / check / finalize

两模式共用：classify / reconcile / report / precheck
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import classify as cls_mod
from . import index as idx_mod
from . import prepare as prep_mod
from . import reconcile as rec_mod
from . import segment as seg_mod
from . import validate as val_mod
from .config import (
    CONFIG_DIR_DEFAULT, DIALOG_DIR, INDEX_PATH, MONOLOG_DIR,
    NEEDS_REVIEW_DIR, PROMPTS_DIR_DEFAULT, SRC_DIR, ensure_dirs,
)


def _emit(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _load_whitelist(args) -> None:
    val_mod.load_rules(Path(args.whitelist_config))


# ---------- 共用 ----------

def cmd_precheck(args) -> int:
    problems = []
    if not SRC_DIR.exists():
        problems.append(f"源目录不存在：{SRC_DIR}")
    for name in ("phase_dialog_haiku.md", "refine.md", "phase_monolog.md"):
        if not (Path(args.prompts_dir) / name).exists():
            problems.append(f"缺 prompt：{name}")
    if not Path(args.whitelist_config).exists():
        problems.append(f"缺 whitelist 配置：{args.whitelist_config}")
    _emit({"ok": not problems, "problems": problems})
    return 0 if not problems else 1


def cmd_classify(args) -> int:
    pending = idx_mod.list_pending(args.limit, args.strategy)
    processed = 0
    for it in pending:
        result = cls_mod.classify_file(Path(it["file_path"]))
        if "error" in result:
            print(f"[skip] {it['bv']}: {result['error']}", file=sys.stderr)
            continue
        idx_mod.upsert(result["bv"], result)
        processed += 1
        if processed % 5 == 0:
            print(f"[classify] 进度 {processed}/{len(pending)}", file=sys.stderr)
    _emit({"classified": processed, "requested": len(pending), "strategy": args.strategy})
    return 0


def cmd_reconcile(args) -> int:
    _emit(rec_mod.reconcile())
    return 0


def cmd_report(args) -> int:
    idx = idx_mod.load_index()
    items = idx["items"]
    counts = idx.get("counts") or idx_mod.compute_counts(items)
    dialog_done = len(list(DIALOG_DIR.glob("*.json"))) if DIALOG_DIR.exists() else 0
    monolog_done = len(list(MONOLOG_DIR.glob("*.json"))) if MONOLOG_DIR.exists() else 0
    nr = len(list(NEEDS_REVIEW_DIR.glob("*.json"))) if NEEDS_REVIEW_DIR.exists() else 0

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

    _emit({
        "index_total": counts.get("total", 0),
        "classification_distribution": {
            k: counts.get(k, 0) for k in ("对话", "独白", "专题", "其他", "片段", "整场")
        },
        "processed": {
            k: counts.get(k, 0) for k in ("pending", "done", "skipped", "needs_review")
        },
        "structured_products": {
            "phase_dialog": dialog_done,
            "phase_monolog": monolog_done,
            "needs_review": nr,
        },
        "pending_candidates": {
            "dialog_segment": pending_dialog,
            "monolog_segment": pending_monolog,
        },
    })
    return 0


# ---------- Mode B: 工作包 ----------

def _select_items(content_types: set[str], bv: str | None, limit: int) -> list[dict]:
    if bv:
        it = idx_mod.get(bv)
        if not it:
            return []
        return [{"bv": bv, **it}]
    return idx_mod.list_structured(
        content_types=content_types, segment_type="片段",
        processed="pending", limit=limit,
    )


def cmd_prepare_dialog_draft(args) -> int:
    ensure_dirs()
    prompts_dir = Path(args.prompts_dir)
    items = _select_items({"对话"}, args.bv, args.limit)
    if not items:
        _emit({"packets": [], "message": "没有待处理对话"})
        return 0
    packets = [prep_mod.build_dialog_draft_packet(it, prompts_dir) for it in items]
    _emit({
        "stage": "dialog_draft",
        "count": len(packets),
        "instructions": (
            "对每个 packet：用你的 AI 能力按 system_prompt 理解 user_content 里的原文，"
            "产出完整 JSON，Write 到 target_path（覆盖）。写完后逐份调 "
            "`python -m zxf_runner check --path <target_path>`。全部通过后，"
            "调 `python -m zxf_runner prepare-dialog-refine --bvs <bv1,bv2,...>` 进入精修。"
        ),
        "packets": packets,
    })
    return 0


def cmd_prepare_dialog_refine(args) -> int:
    ensure_dirs()
    prompts_dir = Path(args.prompts_dir)
    bvs = [b.strip() for b in args.bvs.split(",") if b.strip()]
    if not bvs:
        _emit({"error": "--bvs 不能为空"})
        return 2
    try:
        packet = prep_mod.build_dialog_refine_packet(
            bvs, prompts_dir, args.refine_model_name,
        )
    except FileNotFoundError as e:
        _emit({"error": str(e), "hint": "先跑 prepare-dialog-draft 并把 draft 写盘"})
        return 1
    packet["instructions"] = (
        "按 system_prompt 处理 user_content 里的 N 份 draft，每份精修后 Write 到对应 targets[i].target_path。"
        "逐份调 `check --path`；全通过后逐份调 `finalize --bv <bv> --status done`。"
    )
    _emit(packet)
    return 0


def cmd_prepare_monolog(args) -> int:
    ensure_dirs()
    prompts_dir = Path(args.prompts_dir)
    items = _select_items({"独白", "专题"}, args.bv, args.limit)
    if not items:
        _emit({"packets": [], "message": "没有待处理独白/专题"})
        return 0
    packets = [prep_mod.build_monolog_packet(it, prompts_dir) for it in items]
    _emit({
        "stage": "monolog",
        "count": len(packets),
        "instructions": (
            "对每个 packet：按 system_prompt 产 JSON → Write 到 target_path → "
            "check → finalize --status done。独白无精修阶段。"
        ),
        "packets": packets,
    })
    return 0


def cmd_check(args) -> int:
    _load_whitelist(args)
    path = Path(args.path)
    if not path.exists():
        _emit({"ok": False, "errors": [f"文件不存在：{path}"]})
        return 1
    errs = val_mod.validate_file(path)
    _emit({
        "ok": not errs,
        "path": str(path),
        "errors": errs,
        "hint": ("校验失败：修正 JSON 后重写并再跑 check；"
                 "如两次仍失败，调 finalize --status needs_review --reason") if errs else None,
    })
    return 0 if not errs else 1


def cmd_finalize(args) -> int:
    bv = args.bv
    status = args.status
    if status not in {"done", "needs_review", "skipped"}:
        _emit({"error": "--status 必须是 done / needs_review / skipped"})
        return 2
    try:
        idx_mod.mark(bv, status, reason=args.reason)
    except KeyError as e:
        _emit({"error": str(e)})
        return 1
    _emit({"ok": True, "bv": bv, "status": status})
    return 0


# ---------- 整场切片（Mode B）----------

def cmd_segment_plan(args) -> int:
    ensure_dirs()
    plan = seg_mod.build_segment_plan(args.bv)
    _emit(plan)
    return 0 if "error" not in plan else 1


def cmd_prepare_segment_refine(args) -> int:
    ensure_dirs()
    prompts_dir = Path(args.prompts_dir)
    item = idx_mod.get(args.bv)
    if not item:
        _emit({"error": f"BV {args.bv} 不在 index"})
        return 1
    item = {"bv": args.bv, **item}
    plan = seg_mod.build_segment_plan(args.bv)
    if "error" in plan:
        _emit(plan)
        return 1
    packet = prep_mod.build_segment_refine_packet(plan, item, prompts_dir)
    packet["instructions"] = (
        "按 system_prompt 读完整原文+候选段，产最终切片 plan JSON，"
        "Write 到 target_path，然后调 `finalize-segment --bv <bv> --plan-json <target_path>`。"
    )
    _emit(packet)
    return 0


def cmd_finalize_segment(args) -> int:
    ensure_dirs()
    plan_path = Path(args.plan_json)
    if not plan_path.exists():
        _emit({"ok": False, "error": f"plan 文件不存在：{plan_path}"})
        return 1
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _emit({"ok": False, "error": f"plan JSON 解析失败：{e}"})
        return 1
    result = seg_mod.finalize_segments(args.bv, plan)
    _emit(result)
    return 0 if result.get("ok") else 1


# ---------- Mode A（原自驱模式，需 API key）----------

def cmd_structure(args) -> int:
    try:
        from . import llm as llm_mod
        from . import structure as str_mod
    except RuntimeError as e:
        _emit({"error": f"Mode A 依赖缺失：{e}"})
        return 1
    llm_mod.load_models(Path(args.models_config))
    _load_whitelist(args)
    prompts_dir = Path(args.prompts_dir)

    if args.content_type == "dialog":
        content_types = {"对话"}
    else:
        content_types = {"独白", "专题"}
    items = _select_items(content_types, args.bv, args.limit)
    if not items:
        _emit({"message": "没有待处理项"})
        return 0

    all_results = []
    batch = max(1, args.batch_size)
    for start in range(0, len(items), batch):
        chunk = items[start : start + batch]
        if args.content_type == "dialog":
            rs = str_mod.structure_dialog_batch(
                chunk, prompts_dir,
                draft_model=args.draft_model,
                refine_model=args.refine_model,
                parallel=args.parallel,
            )
        else:
            rs = str_mod.structure_monolog_batch(
                chunk, prompts_dir,
                model=args.monolog_model, parallel=args.parallel,
            )
        all_results.extend(rs)
        print(f"[structure] 批 {start // batch + 1}: 完成 {len(rs)} 份", file=sys.stderr)

    summary: dict[str, int] = {"done": 0, "needs_review": 0, "skipped": 0}
    for r in all_results:
        summary[r.status] = summary.get(r.status, 0) + 1
    _emit({
        "total": len(all_results),
        "summary": summary,
        "results": [{"bv": r.bv, "status": r.status, "note": r.note} for r in all_results],
    })
    return 0


# ---------- parser ----------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m zxf_runner")
    ap.add_argument("--prompts-dir", default=str(PROMPTS_DIR_DEFAULT))
    ap.add_argument("--models-config", default=str(CONFIG_DIR_DEFAULT / "models.yaml"))
    ap.add_argument("--whitelist-config", default=str(CONFIG_DIR_DEFAULT / "whitelist.yaml"))

    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("precheck"); p.set_defaults(func=cmd_precheck)

    p = sub.add_parser("classify", help="分类待处理 txt")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--strategy", choices=["sorted", "random", "keyword-first"], default="keyword-first")
    p.set_defaults(func=cmd_classify)

    p = sub.add_parser("reconcile"); p.set_defaults(func=cmd_reconcile)
    p = sub.add_parser("report"); p.set_defaults(func=cmd_report)

    # ---- Mode B ----
    p = sub.add_parser("prepare-dialog-draft", help="[Mode B] 出对话粗修工作包")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--bv", type=str, default=None)
    p.set_defaults(func=cmd_prepare_dialog_draft)

    p = sub.add_parser("prepare-dialog-refine", help="[Mode B] 出对话精修工作包")
    p.add_argument("--bvs", required=True, help="逗号分隔的 BV 列表（需已有粗修 draft）")
    p.add_argument("--refine-model-name", default="主 agent",
                   help="填进产物 refined_by 字段")
    p.set_defaults(func=cmd_prepare_dialog_refine)

    p = sub.add_parser("prepare-monolog", help="[Mode B] 出独白/专题工作包")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--bv", type=str, default=None)
    p.set_defaults(func=cmd_prepare_monolog)

    p = sub.add_parser("check", help="[Mode B] 校验单份 JSON 文件")
    p.add_argument("--path", required=True)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("finalize", help="[Mode B] 回写 index processed 状态")
    p.add_argument("--bv", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--reason", default=None)
    p.set_defaults(func=cmd_finalize)

    # ---- 整场切片 ----
    p = sub.add_parser("segment-plan", help="[Mode B] 整场启发式过滤+规则粗切，出候选段报告")
    p.add_argument("--bv", required=True)
    p.set_defaults(func=cmd_segment_plan)

    p = sub.add_parser("prepare-segment-refine", help="[Mode B] 出整场精修工作包")
    p.add_argument("--bv", required=True)
    p.set_defaults(func=cmd_prepare_segment_refine)

    p = sub.add_parser("finalize-segment", help="[Mode B] 按 plan 切原文、落盘、建索引")
    p.add_argument("--bv", required=True)
    p.add_argument("--plan-json", required=True)
    p.set_defaults(func=cmd_finalize_segment)

    # ---- Mode A ----
    p = sub.add_parser("structure", help="[Mode A] 需 API key，runner 自调 LLM")
    p.add_argument("--content-type", choices=["dialog", "monolog"], default="dialog")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--bv", type=str)
    p.add_argument("--batch-size", type=int, default=5)
    p.add_argument("--draft-model", default="haiku")
    p.add_argument("--refine-model", default="sonnet")
    p.add_argument("--monolog-model", default="sonnet")
    p.add_argument("--parallel", type=int, default=1)
    p.set_defaults(func=cmd_structure)

    return ap


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
