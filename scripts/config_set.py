#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""config_set.py — LessonLoop 경로 설정 변경 코어.

lessonloop.json 에 journal_dir/cards_dir 쓰기.
CC 스킬·Hermes 스킬·재설치 전부 이거 호출 → 단일 진실원천.
"""
from __future__ import annotations
import sys, json
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent.parent / "lessonloop.json"


def _load():
    """기존 config 로드. 없으면 빈 dict."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(cfg):
    """config 쓰기 (indent=2, ensure_ascii=False)."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def set_journal_dir(path: str):
    """journal_dir 설정."""
    cfg = _load()
    cfg["journal_dir"] = path
    _save(cfg)


def set_cards_dir(path: str):
    """cards_dir 설정."""
    cfg = _load()
    cfg["cards_dir"] = path
    _save(cfg)


def set_sync_dir(path: str):
    """sync_dir 설정 — capture 가 journal-<agent>.jsonl 을 이 공유FS 폴더에 미러.
    다른 머신 수집기가 읽음. 빈 문자열/None → 미러 끔(키 제거).
    ⚠ 반드시 절대경로 — 상대경로는 capture 실행 cwd(Hermes=AppData/Local/hermes 등)
    기준으로 잡혀 잘못된 위치에 미러 쓰임(실제 사고 2026-06-28)."""
    cfg = _load()
    if path:
        if not Path(path).is_absolute():
            print(f"  ⚠ 상대경로입니다: {path}")
            print(f"    capture 가 실행 cwd(Hermes=AppData/Local/hermes 등) 기준으로 잡아")
            print(f"    → 잘못된 위치에 미러가 쓰일 수 있음. 절대경로 권장.")
            print(f"    예) C:/Users/<user>/Desktop/.../sync  (Windows, 정슬래시)")
        cfg["sync_dir"] = path
    else:
        cfg.pop("sync_dir", None)
    _save(cfg)


def get_journal_dir() -> str | None:
    """journal_dir 조회."""
    return _load().get("journal_dir")


def get_cards_dir() -> str | None:
    """cards_dir 조회."""
    return _load().get("cards_dir")


def get_sync_dir() -> str | None:
    """sync_dir 조회."""
    return _load().get("sync_dir")


def reset_to_defaults():
    """경로 전부 기본값(ROOT)으로 리셋."""
    cfg = _load()
    cfg.pop("journal_dir", None)
    cfg.pop("cards_dir", None)
    cfg.pop("sync_dir", None)
    _save(cfg)


def show():
    """현재 설정 출력."""
    cfg = _load()
    jd = cfg.get("journal_dir")
    cd = cfg.get("cards_dir")
    sd = cfg.get("sync_dir")
    root = str(Path(__file__).resolve().parent.parent)
    print("LessonLoop 경로 설정:")
    print(f"  journal_dir: {jd or f'(기본: {root})'}")
    print(f"  cards_dir:   {cd or f'(기본: {root})'}")
    print(f"  sync_dir:    {sd or '(미러 없음)'}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="LessonLoop 경로 설정")
    ap.add_argument("--journal-dir", help="journal 경로")
    ap.add_argument("--cards-dir", help="cards/lessons_index 경로")
    ap.add_argument("--sync-dir", default=None,
                    help="journal 미러 경로(공유FS). 빈 문자열 '' = 미러 끔")
    ap.add_argument("--reset", action="store_true", help="기본값으로 리셋")
    ap.add_argument("--show", action="store_true", help="현재 설정 출력")
    args = ap.parse_args()

    did = args.reset or args.journal_dir or args.cards_dir or (args.sync_dir is not None)
    if args.show:
        show()
    elif not did:
        ap.print_help()
    else:
        if args.reset:
            reset_to_defaults()
            print("경로 설정 리셋 완료 (기본값 ROOT).")
        if args.journal_dir:
            set_journal_dir(args.journal_dir)
            print(f"journal_dir 설정: {args.journal_dir}")
        if args.cards_dir:
            set_cards_dir(args.cards_dir)
            print(f"cards_dir 설정: {args.cards_dir}")
        if args.sync_dir is not None:
            set_sync_dir(args.sync_dir)
            print(f"sync_dir 설정: {args.sync_dir or '(미러 끔)'}")
