#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""feedback.py — LessonLoop ③ FEEDBACK + 거버너 (offline 배치, 제로인프라).

recall_log-{agent}.jsonl(카드 발화 연료) ↔ journal-{agent}.jsonl(실패) 을
**action_sig(asig) 공존**으로 join → 약한카드 / 죽은카드 / HEALTH 산출 → 거버너 mode.

DESIGN §12 (LOCK):
  HEALTH = 1 - 재발률   (재발 = canonical 카드 있는 sig 가 또 실패)
  거버너(히스테리시스 + 콜드스타트 floor):
    카드<30 or recall-eligible 행동<50  → A (워밍업)
    HEALTH >= 0.85                       → B (유지보수)
    HEALTH <= 0.70                       → A (품질구축)
    else                                 → hold (이전 mode 유지)

join 의미 — *시간순서 엄격 비교 안 함*:
  recall = PreToolUse(행동 직전 발화), capture = Stop(실패 후). 한 invocation 내
  fire.t < fail.t 는 훅 semantics 로 보장. cross-session A-fire→B-fail 도 정당한 재발.
  asig 가 양쪽 로그에 공존 = "이 행동모양에 카드가 발화 가능한데 또 실패" = 약한카드 신호.
  (capture entry 에 timestamp 없음 → capture 무수정 원칙. 엄격 temporal 은 옵션 후순위.)

출력:
  .feedback_state-{agent}.json   — 상세(health/weak/dead/mode), runtime data(gitignore)
  stdout                         — 기계가독 요약(cycle 이 parse): "feedback weak=N dead=M health=0.xx mode=X"

**fail-open**: 모든 예외 stderr + exit 0 (로그 분석이 세션/사이클 막으면 안 됨).
자동조치 ❌ — 보고만(약한카드 노출 → self-witness/refine 입력). prune ❌(휴면 vs 폐기 구분 불가).
"""
import sys, os, io, json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
try:
    from paths import journal as journal_path, recall_log, cards_dir, cards_dir_cards
except ImportError:
    ROOT = SCRIPTS.parent
    journal_path = lambda a: ROOT / f"journal-{a}.jsonl"
    recall_log = lambda a: ROOT / f"recall_log-{a}.jsonl"
    cards_dir = lambda: ROOT
    cards_dir_cards = lambda: ROOT / "cards"

# 거버너 임계값(DESIGN §12 시작값, 실데이터 튜닝)
MIN_CARDS = 30
MIN_ELIGIBLE = 50
HEALTH_B = 0.85   # 이상 → B
HEALTH_A = 0.70   # 이하 → A
WEAK_RECUR = 2    # recur_count 임계(1회는 노이즈 허용)


def _agent():
    for i, a in enumerate(sys.argv):
        if a == "--agent" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--agent="):
            return a.split("=", 1)[1]
    return os.environ.get("LESSONLOOP_AGENT", "default")


def _load_jsonl(path):
    """jsonl → list[dict]. 깨진 라인 skip(robust). 빈/없음 → []."""
    out = []
    if not path.exists():
        return out
    try:
        for ln in io.open(path, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
    except Exception:
        return []
    return out


def _card_slugs():
    """canonical 카드 slug 집합 = cards/*.md stem. 인덱스 csv(파생물) 아님."""
    try:
        return sorted(p.stem for p in cards_dir_cards().glob("*.md"))
    except Exception:
        return []


def _prior_mode(state_path):
    try:
        d = json.loads(state_path.read_text(encoding="utf-8"))
        return d.get("mode", "A")
    except Exception:
        return "A"


def compute(fires, fails, slugs, prior_mode):
    """순수함수(I/O 무관) — join + HEALTH + 거버너. 테스트 직접 호출.
    fires : [{asig, fired:[slug], ...}]  (recall_log events)
    fails : [{asig, kind, ...}]          (objective_fail 만 caller 가 선별)
    slugs : [str]                         (canonical 카드 slug)
    prior_mode : 'A'|'B'                  (hold 시 유지할 이전 mode)
    → state dict."""
    fire_count = {}                       # slug -> 전체 발화수
    fired_asigs = set()
    fire_events_by_asig = {}              # asig -> [fire-event,...]
    for f in fires:
        asig = f.get("asig")
        fired = f.get("fired") or []
        if not asig:
            continue
        fired_asigs.add(asig)
        fire_events_by_asig.setdefault(asig, []).append(f)
        for s in fired:
            fire_count[s] = fire_count.get(s, 0) + 1

    fail_asigs = set(e.get("asig") for e in fails if e.get("asig"))
    recur_asigs = fired_asigs & fail_asigs   # 양쪽 공존 = 카드 있었는데 또 실패

    recur_count = {}
    for asig in recur_asigs:
        for f in fire_events_by_asig.get(asig, []):
            for s in (f.get("fired") or []):
                recur_count[s] = recur_count.get(s, 0) + 1
    weak = []
    for s, c in recur_count.items():
        if c >= WEAK_RECUR:
            fc = fire_count.get(s, 0)
            weak.append({
                "slug": s, "fire_count": fc, "recur_count": c,
                "recur_rate": round(c / fc, 2) if fc else 0.0,
            })
    weak.sort(key=lambda x: -x["recur_count"])

    dead_candidates = [s for s in slugs if s not in fire_count]

    n_fail = len(fail_asigs)
    n_recur = len(recur_asigs)
    recurrence_rate = (n_recur / n_fail) if n_fail else 0.0
    health = round(1.0 - recurrence_rate, 3)

    n_cards = len(slugs)
    n_eligible = len(fired_asigs)
    coldstart = (n_cards < MIN_CARDS) or (n_eligible < MIN_ELIGIBLE)
    if coldstart:
        mode = "A"
    elif health >= HEALTH_B:
        mode = "B"
    elif health <= HEALTH_A:
        mode = "A"
    else:
        mode = prior_mode

    return {
        "health": health,
        "recurrence_rate": round(recurrence_rate, 3),
        "mode": mode,
        "coldstart": coldstart,
        "n_cards": n_cards,
        "n_eligible": n_eligible,
        "n_fail_asigs": n_fail,
        "n_recur_asigs": n_recur,
        "weak": weak,
        "dead_candidates": dead_candidates,
        "n_dead": len(dead_candidates),
    }


def main():
    agent = _agent()
    JOURNAL = journal_path(agent)
    RECALL = recall_log(agent)
    STATE = cards_dir() / f".feedback_state-{agent}.json"

    fires = _load_jsonl(RECALL)          # [{t,tool,asig,fired:[slug],score}]
    fails = [e for e in _load_jsonl(JOURNAL) if e.get("kind") == "objective_fail"]
    slugs = _card_slugs()
    state = compute(fires, fails, slugs, _prior_mode(STATE))
    state["agent"] = agent

    try:
        STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception as ex:
        sys.stderr.write("[lessonloop] feedback state write failed: %s\n" % ex)

    # stdout — cycle 이 parse 하는 기계가독 라인 + 사람 요약
    print("feedback weak=%d dead=%d health=%.2f mode=%s coldstart=%s n_cards=%d n_eligible=%d"
          % (len(state["weak"]), state["n_dead"], state["health"], state["mode"],
             state["coldstart"], state["n_cards"], state["n_eligible"]))
    if state["weak"]:
        names = ", ".join(w["slug"] for w in state["weak"][:8])
        print("weak_cards: " + names)
    return state


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        sys.stderr.write("[lessonloop] feedback fail-open: %s\n" % ex)
    sys.exit(0)
