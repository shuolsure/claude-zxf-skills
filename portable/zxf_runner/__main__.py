"""CLI 入口：python -m zxf_runner <command> [args]

子命令：
  classify  — 分类待处理 txt
  structure — 结构化（对话粗修+精修 / 独白）
  reconcile — 扫现有成品回填 index
  report    — 汇报进度
  precheck  — 环境检查
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import classify as cls_mod
from . import index as idx_mod
from . import llm as llm_mod
from . import reconcile as rec_mod
from . import structure as str_mod
from . import validate as val_mod
from .config import (
    CONFIG_DIR_DEFAULT, DIALOG_DIR, INDEX_PATH, MONOLOG_DIR,
    NEEDS_REVIEW_DIR, PROMPTS_DIR_DEFAULT, SRC_DIR,
)


def _emit(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _load_configs(args) -> None:
    llm_mod.load_models(Path(args.models_config))
    val_mod.load_rules(Path(args.whitelist_config))


def cmd_precheck(args) -> int:
    problems = []
    if not SRC_DIR.exists():
        problems.append(f"源目录不存在：{SRC_DIR}")
    for name in ("phase_dialog_haiku.md", "refine.md", "phase_monolog.md"):
        if not (Path(args.prompts_dir) / name).exists():
            problems.append(f"缺 prompt：{name}")
    if not Path(args.models_config).exists():
        problems.append(f"缺 models 配置：{args.models_config}")
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


def cmd_structure(args) -> int:
    _load_configs(args)
    prompts_dir = Path(args.prompts_dir)

    if args.bv:
        it = idx_mod.get(args.bv)
        if not it:
            _emit({"error": f"BV {args.bv} 不在 index"})
            return 1
        items = [{"bv": args.bv, **it}]
    else:
        if args.content_type == "dialog":
            content_types = {"对话"}
        elif args.content_type == "monolog":
            content_types = {"独白", "专题"}
        else:
            _emit({"error": "--content-type 必须是 dialog 或 monolog（或用 --bv 单跑）"})
            return 2
        items = idx_mod.list_structured(
            content_types=content_types,
            segment_type="片段",
            processed="pending",
            limit=args.limit,
        )

    if not items:
        _emit({"message": "没有待处理项"})
        return 0

    all_results = []
    batch = max(1, args.batch_size)
    for start in range(0, len(items), batch):
        chunk = items[start : start + batch]
        if args.content_type == "dialog" or (args.bv and (idx_mod.get(args.bv) or {}).get("content_type") == "对话"):
            rs = str_mod.structure_dialog_batch(
                chunk, prompts_dir,
                draft_model=args.draft_model,
                refine_model=args.refine_model,
                parallel=args.parallel,
            )
        else:
            rs = str_mod.structure_monolog_batch(
                chunk, prompts_dir,
                model=args.monolog_model,
                parallel=args.parallel,
            )
        all_results.extend(rs)
        print(f"[structure] 批 {start // batch + 1}: 完成 {len(rs)} 份",
              file=sys.stderr)

    summary: dict[str, int] = {"done": 0, "needs_review": 0, "skipped": 0}
    for r in all_results:
        summary[r.status] = summary.get(r.status, 0) + 1
    _emit({
        "total": len(all_results),
        "summary": summary,
        "results": [{"bv": r.bv, "status": r.status, "note": r.note} for r in all_results],
    })
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


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m zxf_runner")
    ap.add_argument("--prompts-dir", default=str(PROMPTS_DIR_DEFAULT),
                    help="Prompt 目录（默认用 runner 自带）")
    ap.add_argument("--models-config", default=str(CONFIG_DIR_DEFAULT / "models.yaml"))
    ap.add_argument("--whitelist-config", default=str(CONFIG_DIR_DEFAULT / "whitelist.yaml"))

    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("precheck", help="检查环境/prompt/配置是否齐全")
    p.set_defaults(func=cmd_precheck)

    p = sub.add_parser("classify", help="分类待处理 txt")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--strategy", choices=["sorted", "random", "keyword-first"], default="keyword-first")
    p.set_defaults(func=cmd_classify)

    p = sub.add_parser("structure", help="跑结构化流水线")
    p.add_argument("--content-type", choices=["dialog", "monolog"], default="dialog")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--bv", type=str, help="单份模式")
    p.add_argument("--batch-size", type=int, default=5,
                   help="每批 LLM 精修的份数（默认 5，保证横向一致）")
    p.add_argument("--draft-model", default="haiku", help="粗修模型别名")
    p.add_argument("--refine-model", default="sonnet", help="精修模型别名")
    p.add_argument("--monolog-model", default="sonnet", help="独白模型别名")
    p.add_argument("--parallel", type=int, default=1, help="LLM 调用并发度（ThreadPool）")
    p.set_defaults(func=cmd_structure)

    p = sub.add_parser("reconcile", help="扫现有成品回填 index")
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser("report", help="汇报进度")
    p.set_defaults(func=cmd_report)

    return ap


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
