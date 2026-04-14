"""结构化流水线：粗修 → 精修 → 校验 → 入库。

设计决策：
- 顺序执行默认；`--parallel N` 用 ThreadPoolExecutor 在 LLM 调用层并发
  （各家 SDK 都是阻塞 IO，ThreadPool 比 asyncio 更简单）
- 每完成一份立刻写盘+校验+mark-done，失败走 needs_review
- 精修批量：每凑满 `refine_batch_size` 份粗修成品，一次性喂 refine LLM
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from . import index as idx_mod
from . import validate as val_mod
from .config import (
    DIALOG_DIR, DIALOG_DRAFT_DIR, MONOLOG_DIR, NEEDS_REVIEW_DIR, ensure_dirs,
)
from .llm import call_llm


JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json_blocks(text: str) -> list[str]:
    """从 LLM 输出里提所有 ```json ... ``` 代码块内容。"""
    return [m.group(1).strip() for m in JSON_FENCE_RE.finditer(text)]


def parse_single_json(text: str) -> dict:
    """提取第一个 json fence 并解析；fence 缺失时退化到整段解析。"""
    blocks = extract_json_blocks(text)
    raw = blocks[0] if blocks else text.strip()
    return json.loads(raw)


@dataclass
class RunResult:
    bv: str
    status: str  # done / needs_review / skipped
    note: str = ""


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _load_prompt(prompts_dir: Path, name: str) -> str:
    return (prompts_dir / name).read_text(encoding="utf-8")


# --------- 粗修（对话 Haiku）---------

def _draft_dialog(bv: str, file_path: str, prompts_dir: Path, model: str) -> dict:
    """调 LLM 粗修单份对话，返回解析后的 JSON dict。"""
    system = _load_prompt(prompts_dir, "phase_dialog_haiku.md")
    text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    user = (
        f"处理 BV: {bv}\n\n"
        f"源文件完整文本：\n---\n{text}\n---\n\n"
        "要求：按 system prompt 产出完整 JSON，用 ```json ... ``` 包裹。"
    )
    out = call_llm(model, system, user)
    return parse_single_json(out)


def _refine_batch(drafts: list[tuple[str, dict]], prompts_dir: Path, model: str) -> dict[str, dict]:
    """一次喂 N 份 draft，返回 {bv: refined_json}。"""
    system = _load_prompt(prompts_dir, "refine.md")
    parts = ["以下是 {} 份粗修 JSON（一次性处理，保证横向一致）：\n".format(len(drafts))]
    for bv, d in drafts:
        parts.append(f"=== {bv} ===\n```json\n{json.dumps(d, ensure_ascii=False, indent=2)}\n```\n")
    parts.append(
        "\n要求：\n"
        "- Step 1: 先输出问题清单（≤500 字纯文本）\n"
        "- Step 2: 输出每份精修 JSON，用 ```json ... ``` 包裹，"
        "且每份 JSON 内部要带 refined_by 字段标明模型名。\n"
        "- 顺序按上面的输入顺序给。\n"
    )
    out = call_llm(model, system, "\n".join(parts))
    blocks = extract_json_blocks(out)
    if len(blocks) < len(drafts):
        raise RuntimeError(f"refine 返回 JSON 数 {len(blocks)} < 预期 {len(drafts)}")
    result: dict[str, dict] = {}
    for (bv, _), raw in zip(drafts, blocks[: len(drafts)]):
        result[bv] = json.loads(raw)
        result[bv].setdefault("refined_by", model)
    return result


def _write_and_validate(payload: dict, path: Path, name: str) -> list[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return val_mod.validate_json(payload, name)


def structure_dialog_batch(
    items: list[dict],
    prompts_dir: Path,
    draft_model: str = "haiku",
    refine_model: str = "sonnet",
    parallel: int = 1,
) -> list[RunResult]:
    """处理对话类一批（粗修并发 + 精修一次性）。"""
    ensure_dirs()
    results: dict[str, RunResult] = {}
    drafts: list[tuple[str, dict]] = []

    def draft_one(it: dict) -> tuple[str, dict | None, str | None]:
        bv = it["bv"]
        try:
            d = _draft_dialog(bv, it["file_path"], prompts_dir, draft_model)
            errs = _write_and_validate(d, DIALOG_DRAFT_DIR / f"{bv}.json", f"draft/{bv}")
            if errs:
                try:
                    d = _draft_dialog(bv, it["file_path"], prompts_dir, draft_model)
                    errs = _write_and_validate(d, DIALOG_DRAFT_DIR / f"{bv}.json", f"draft/{bv}")
                except Exception as e:
                    return bv, None, f"retry_failed:{e}"
                if errs:
                    return bv, None, "validate_failed:" + "; ".join(errs[:3])
            if d.get("skipped") or d.get("dialogue_type") == "non_dialog":
                return bv, d, "skipped_non_dialog"
            return bv, d, None
        except Exception as e:
            return bv, None, f"draft_failed:{e}"

    if parallel > 1:
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            futures = [ex.submit(draft_one, it) for it in items]
            drafted = [f.result() for f in as_completed(futures)]
    else:
        drafted = [draft_one(it) for it in items]

    for bv, d, err in drafted:
        if err == "skipped_non_dialog":
            _log(f"[{bv}] 粗修识别为非对话，skip")
            path = DIALOG_DIR / f"{bv}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            idx_mod.mark(bv, "skipped", reason="non_dialog_detected")
            results[bv] = RunResult(bv, "skipped", "non_dialog")
        elif err is None:
            drafts.append((bv, d))
            _log(f"[{bv}] 粗修通过")
        else:
            _log(f"[{bv}] 粗修失败：{err}")
            if d is not None:
                NEEDS_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
                (NEEDS_REVIEW_DIR / f"{bv}.json").write_text(
                    json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            idx_mod.mark(bv, "needs_review", reason=err)
            results[bv] = RunResult(bv, "needs_review", err)

    if not drafts:
        return list(results.values())

    # 精修
    try:
        refined = _refine_batch(drafts, prompts_dir, refine_model)
    except Exception as e:
        _log(f"精修批量失败，尝试单份：{e}")
        refined = {}
        for bv, d in drafts:
            try:
                refined.update(_refine_batch([(bv, d)], prompts_dir, refine_model))
            except Exception as e2:
                _log(f"[{bv}] 精修单份失败：{e2}")
                idx_mod.mark(bv, "needs_review", reason=f"refine_failed:{e2}")
                results[bv] = RunResult(bv, "needs_review", f"refine_failed:{e2}")

    for bv, payload in refined.items():
        errs = _write_and_validate(payload, DIALOG_DIR / f"{bv}.json", f"refined/{bv}")
        if errs:
            _log(f"[{bv}] 精修校验失败：{errs[:3]}")
            NEEDS_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
            (NEEDS_REVIEW_DIR / f"{bv}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            idx_mod.mark(bv, "needs_review", reason="refine_validate_failed")
            results[bv] = RunResult(bv, "needs_review", "refine_validate_failed")
        else:
            idx_mod.mark(bv, "done")
            results[bv] = RunResult(bv, "done")
            _log(f"[{bv}] 精修入库")

    return list(results.values())


# --------- 独白/专题 ---------

def _run_monolog_one(bv: str, file_path: str, prompts_dir: Path, model: str) -> dict:
    system = _load_prompt(prompts_dir, "phase_monolog.md")
    text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    user = (
        f"处理 BV: {bv}\n\n"
        f"源文件完整文本：\n---\n{text}\n---\n\n"
        "要求：按 system prompt 产出 JSON，用 ```json ... ``` 包裹。"
    )
    out = call_llm(model, system, user)
    return parse_single_json(out)


def structure_monolog_batch(
    items: list[dict],
    prompts_dir: Path,
    model: str = "sonnet",
    parallel: int = 1,
) -> list[RunResult]:
    ensure_dirs()
    results: list[RunResult] = []

    def process(it: dict) -> RunResult:
        bv = it["bv"]
        try:
            payload = _run_monolog_one(bv, it["file_path"], prompts_dir, model)
        except Exception as e:
            try:
                payload = _run_monolog_one(bv, it["file_path"], prompts_dir, model)
            except Exception as e2:
                _log(f"[{bv}] 独白抽取失败：{e2}")
                idx_mod.mark(bv, "needs_review", reason=f"monolog_failed:{e2}")
                return RunResult(bv, "needs_review", f"monolog_failed:{e2}")

        errs = _write_and_validate(payload, MONOLOG_DIR / f"{bv}.json", f"monolog/{bv}")
        if errs:
            _log(f"[{bv}] 独白校验失败：{errs[:3]}")
            NEEDS_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
            (NEEDS_REVIEW_DIR / f"{bv}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            idx_mod.mark(bv, "needs_review", reason="monolog_validate_failed")
            return RunResult(bv, "needs_review", "monolog_validate_failed")
        idx_mod.mark(bv, "done")
        _log(f"[{bv}] 独白入库")
        return RunResult(bv, "done")

    if parallel > 1:
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            futures = [ex.submit(process, it) for it in items]
            for f in as_completed(futures):
                results.append(f.result())
    else:
        for it in items:
            results.append(process(it))
    return results
