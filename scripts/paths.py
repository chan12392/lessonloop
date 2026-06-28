#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paths.py — LessonLoop 경로 진실원천.

lessonloop.json(journal_dir/cards_dir) 읽어 경로 제공.
모든 스크립트가 이 모듈 경유 → 단일 진실원천.
"""
from __future__ import annotations
import sys, os, json
from pathlib import Path

# 기본값 = 스크립트 위치 기반(ROOT)
DEFAULT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = DEFAULT_ROOT / "lessonloop.json"


def _load_config():
    """lessonloop.json 로드. 없으면 빈 dict."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def journal_dir():
    """journal(capture 출력) 경로. 기본=ROOT."""
    cfg = _load_config()
    path = cfg.get("journal_dir")
    if path:
        return Path(path)
    return DEFAULT_ROOT


def cards_dir():
    """cards/lessons_index 경로. 기본=ROOT."""
    cfg = _load_config()
    path = cfg.get("cards_dir")
    if path:
        return Path(path)
    return DEFAULT_ROOT


def journal(agent: str) -> Path:
    """journal-{agent}.jsonl 경로."""
    return journal_dir() / f"journal-{agent}.jsonl"


def recall_log(agent: str) -> Path:
    """recall_log-{agent}.jsonl 경로."""
    return cards_dir() / f"recall_log-{agent}.jsonl"


def index_file() -> Path:
    """lessons_index.csv 경로."""
    return cards_dir() / "lessons_index.csv"


def cards_dir_cards() -> Path:  # cards 하위 디렉토리
    """cards/ 경로."""
    return cards_dir() / "cards"


def sync_dir() -> Path | None:
    """journal 미러(공유FS) 경로. capture 가 journal-<agent>.jsonl 을 이 폴더에도 복제.
    lessonloop.json 의 sync_dir 키. None 이면 미러 없음."""
    cfg = _load_config()
    path = cfg.get("sync_dir")
    return Path(path) if path else None


# 하위 호환: ROOT 노출 (기존 코드가 import 후 ROOT 쓰던 경우)
ROOT = DEFAULT_ROOT
