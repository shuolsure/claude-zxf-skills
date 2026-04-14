"""全局配置（路径 + 常量）。

环境变量优先级高于默认值。需要迁移数据目录时，
设置 ZXF_SRC_DIR 和 ZXF_OUT_DIR 即可，不用改代码。
"""
from __future__ import annotations

import os
from pathlib import Path

# 源转录目录（只读）
SRC_DIR = Path(os.environ.get(
    "ZXF_SRC_DIR",
    "/Users/shuo/Documents/Claude/daxue/zxftrans",
))

# 产物根目录（index.json / structured/ 都在这下面）
OUT_DIR = Path(os.environ.get(
    "ZXF_OUT_DIR",
    "/Users/shuo/Documents/Claude/daxue/my-advisor-app/knowledge/zxftrans_structured",
))

INDEX_PATH = OUT_DIR / "classification" / "index.json"
DIALOG_DRAFT_DIR = OUT_DIR / "structured" / "phase_dialog_draft"
DIALOG_DIR = OUT_DIR / "structured" / "phase_dialog"
MONOLOG_DIR = OUT_DIR / "structured" / "phase_monolog"
NEEDS_REVIEW_DIR = OUT_DIR / "structured" / "_needs_review"

# Prompt 目录：默认用 runner 自带，可通过 --prompts-dir 覆盖
PROMPTS_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "prompts"
CONFIG_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "config"

SEGMENT_CHAR_THRESHOLD = 8000
VALUE_MAX_LEN = 12
KNOWLEDGE_VALUE_MAX_LEN = 40
QUOTE_VALUE_MAX_LEN = 30


def ensure_dirs() -> None:
    for d in (INDEX_PATH.parent, DIALOG_DRAFT_DIR, DIALOG_DIR, MONOLOG_DIR, NEEDS_REVIEW_DIR):
        d.mkdir(parents=True, exist_ok=True)
