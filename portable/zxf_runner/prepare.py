"""工作包生成：把"待处理清单 + prompt + 原文"拼成 JSON 丢给主 agent。

主 agent 用自己的 LLM 处理工作包、写盘，然后调 `check` 校验 / `finalize` 入库。
整个过程 runner 不调任何 LLM，所以不需要 API key。
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import DIALOG_DIR, DIALOG_DRAFT_DIR, MONOLOG_DIR


def _read_prompt(prompts_dir: Path, name: str) -> str:
    return (prompts_dir / name).read_text(encoding="utf-8")


def _read_text(file_path: str) -> str:
    return Path(file_path).read_text(encoding="utf-8", errors="ignore")


def build_dialog_draft_packet(item: dict, prompts_dir: Path) -> dict:
    """一份对话的粗修工作包。"""
    bv = item["bv"]
    system = _read_prompt(prompts_dir, "phase_dialog_haiku.md")
    user = (
        f"处理 BV: {bv}\n\n"
        f"源文件完整文本：\n---\n{_read_text(item['file_path'])}\n---\n\n"
        "要求：按 system prompt 产出完整 JSON。"
        "**只**输出一个 ```json ... ``` 代码块，不要任何其他说明文字。"
    )
    return {
        "bv": bv,
        "stage": "dialog_draft",
        "system_prompt": system,
        "user_content": user,
        "target_path": str(DIALOG_DRAFT_DIR / f"{bv}.json"),
        "next_step": "写完调 `check --path <target_path>`；通过后把 BV 加入精修批次",
    }


def build_dialog_refine_packet(
    bvs: list[str],
    prompts_dir: Path,
    refine_model_name: str = "主 agent 自身",
) -> dict:
    """把 N 份 draft 打包成一个精修工作包。"""
    system = _read_prompt(prompts_dir, "refine.md")
    parts = [f"以下是 {len(bvs)} 份粗修 JSON（请一次性处理，保证横向一致）：\n"]
    targets = []
    for bv in bvs:
        draft_path = DIALOG_DRAFT_DIR / f"{bv}.json"
        if not draft_path.exists():
            raise FileNotFoundError(f"缺粗修 draft: {draft_path}")
        draft = draft_path.read_text(encoding="utf-8")
        parts.append(f"=== {bv} ===\n```json\n{draft}\n```\n")
        targets.append({"bv": bv, "target_path": str(DIALOG_DIR / f"{bv}.json")})

    parts.append(
        "\n要求：\n"
        "- Step 1: 先输出一段问题清单纯文本（≤500 字），注明每份的改动点\n"
        "- Step 2: 按顺序输出每份精修后的 JSON，每份用 ```json ... ``` 包裹\n"
        f"- 每份 JSON 里加 `refined_by: \"{refine_model_name}\"` 和 `refine_notes`（一句话说明改了啥）\n"
        "- 不要交叉顺序，不要省略"
    )

    return {
        "stage": "dialog_refine",
        "system_prompt": system,
        "user_content": "\n".join(parts),
        "targets": targets,
        "refine_model_name": refine_model_name,
        "next_step": "写每份到对应 target_path，逐份调 `check`；全过后逐份调 `finalize --status done`",
    }


def build_monolog_packet(item: dict, prompts_dir: Path) -> dict:
    bv = item["bv"]
    system = _read_prompt(prompts_dir, "phase_monolog.md")
    user = (
        f"处理 BV: {bv}\n\n"
        f"源文件完整文本：\n---\n{_read_text(item['file_path'])}\n---\n\n"
        "要求：按 system prompt 产出 JSON，**只**输出一个 ```json ... ``` 代码块。"
    )
    return {
        "bv": bv,
        "stage": "monolog",
        "system_prompt": system,
        "user_content": user,
        "target_path": str(MONOLOG_DIR / f"{bv}.json"),
        "next_step": "写完调 `check --path <target_path>`；通过后调 `finalize --bv <bv> --status done`",
    }
