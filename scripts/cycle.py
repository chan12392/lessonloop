#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cycle.py — LessonLoop 자동 사이클 (SessionStart 훅).

직전 세션들에서 capture(Stop훅)가 쌓은 journal 누적분을 세션 시작 시 처리:
  harvest → refine(트리아지) → promote --auto(ready 만 승격) → 새 카드 라이브.

- journal 비면 무동작(조용).
- **fail-open**: 절대 세션 시작 방해 안 함(exit 0).
- ready(특이주제 고신뢰)만 자동 승격 — lint_weak/needs_human 은 staging 에서 수동/refiner 대기.
- 승격분 있으면 SessionStart additionalContext 로 한 줄 알림.
"""
import sys, os, subprocess, re, json
from pathlib import Path

# stdout UTF-8 강제 — additionalContext 메시지(한글/em-dash) cp949 콘솔 출력 예외 →
# except 삼김 → 세션 시작 알림 조용히 누락. 다른 훅과 동일 하드닝.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 경로 진실원천
SCRIPTS = Path(__file__).resolve().parent
try:
    from paths import journal as journal_path, cards_dir
except ImportError:
    # 하위 호환: 단돈 실행 시 fallback
    ROOT = SCRIPTS.parent
    journal_path = lambda a: ROOT / f"journal-{a}.jsonl"
    cards_dir = lambda: ROOT

def _agent():
    for i, a in enumerate(sys.argv):
        if a == "--agent" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--agent="):
            return a.split("=", 1)[1]
    return os.environ.get("LESSONLOOP_AGENT", "default")


AGENT = _agent()
JOURNAL = journal_path(AGENT)
PY = sys.executable
ROOT = cards_dir()   # 하위 호환: build_index.py 경로


def run(args):
    try:
        return subprocess.run(
            [PY, "-X", "utf8", str(ROOT / "scripts" / args[0])] + args[1:] + ["--agent", AGENT],
            capture_output=True, text=True, encoding="utf-8", timeout=90,
        )
    except Exception:
        return None


def main():
    if not JOURNAL.exists() or JOURNAL.stat().st_size == 0:
        return
    # 성향(사용자선호) 중심 — objective(기술) 카드는 폐기(2026-06-29)했으므로
    # harvest.py/refine.py(=objective_fail 경로) 호출 제거. soft→pref 카드만.
    hp = run(["harvest_pref.py"])
    pref_soft = pref_staged = 0
    if hp and hp.stdout:
        ms = re.search(r"soft 신호 (\d+)개", hp.stdout)
        if ms:
            pref_soft = int(ms.group(1))
        mp = re.search(r"staged=(\d+)", hp.stdout)
        if mp:
            pref_staged = int(mp.group(1))
    pr = run(["promote.py", "--auto"])
    promoted = 0
    if pr and pr.stdout:
        m = re.search(r"promoted=(\d+)", pr.stdout)
        if m:
            promoted = int(m.group(1))

    # feedback/repair(=objective 약한카드 정비)는 성향 전환에 무관 + 폐기카드 잔류
    # recall_log 로 노이즈(폐기한 기술카드 slug 가 weak 로 보고됨) → cycle서 제거.
    # feedback.py/repair.py 코드 자체는 보존(objective 복귀 시 복원 가능).
    if promoted > 0 or pref_soft > 0 or pref_staged > 0:
        parts = []
        if promoted > 0:
            parts.append(f"지난 세션 실패에서 새 교훈 카드 {promoted}개 라이브(자동 승격). recall이 행동 직전 적용.")
        if pref_staged > 0:
            parts.append(f"사용자선호(soft) 후보 {pref_staged}개 카드화(자동 승격).")
        elif pref_soft > 0:
            parts.append(f"사용자선호 soft 신호 {pref_soft}개 대기(self-witness — harvest_pref --api 또는 직접 카드화).")
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "[LessonLoop] " + " ".join(parts),
            }
        }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
