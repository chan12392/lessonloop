#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_feedback.py — feedback.compute() 순수함수 e2e (I/O 무관, 빠르고 결정적).

합성 fires/fails/slugs 로 join·HEALTH·거버너·경계 검증.
실행: py scripts/test_feedback.py   (exit 0=전부 PASS, 1=어딘가 FAIL)
"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from feedback import compute  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, detail))


def weak_slugs(state):
    return {w["slug"] for w in state["weak"]}


# ── 1. WEAK: 같은 asig 에 카드 발화 + 반복 실패 → 약한카드, HEALTH 낮음, mode A ──
print("[1] WEAK")
fires = [
    {"asig": "X1", "fired": ["cardC"]},
    {"asig": "X1", "fired": ["cardC"]},   # cardC on X1 두번째
    {"asig": "X1", "fired": ["cardC"]},   # 세번째 → recur 3
]
fails = [{"asig": "X1", "kind": "objective_fail"}]
st = compute(fires, fails, ["cardC"], prior_mode="A")
check("cardC weak (recur>=2)", "cardC" in weak_slugs(st), str(weak_slugs(st)))
check("recur_count==3", next((w["recur_count"] for w in st["weak"] if w["slug"] == "cardC"), None) == 3, str(st["weak"]))
check("health=0.0 (1 fail asig, 1 recur)", st["health"] == 0.0, str(st["health"]))
check("mode=A (health<=0.70)", st["mode"] == "A", st["mode"])

# ── 2. HEALTHY: 발화만, 실패 0 → recur 0, HEALTH=1.0, mode B (coldstart 탈출) ──
print("[2] HEALTHY")
slugs35 = ["c%02d" % i for i in range(35)]           # 35 카드 ≥ 30
fires = [{"asig": "h%02d" % i, "fired": [slugs35[i % 35]]} for i in range(55)]  # 55 eligible ≥ 50
st = compute(fires, [], slugs35, prior_mode="A")
check("no weak (no fails)", weak_slugs(st) == set(), str(weak_slugs(st)))
check("health=1.0 (0 fails)", st["health"] == 1.0, str(st["health"]))
check("mode=B (health>=0.85, no coldstart)", st["mode"] == "B", "%s coldstart=%s" % (st["mode"], st["coldstart"]))
check("not coldstart", st["coldstart"] is False, str(st["coldstart"]))

# ── 3. DEAD: 카드 존재 but 발화 0 → dead_candidates (flag only, prune 불가=순수함수) ──
print("[3] DEAD")
fires = [{"asig": "z1", "fired": ["alive"]}]
st = compute(fires, [], ["alive", "dormantA", "dormantB"], prior_mode="A")
check("dormant in dead_candidates", set(["dormantA", "dormantB"]).issubset(set(st["dead_candidates"])), str(st["dead_candidates"]))
check("alive NOT dead", "alive" not in st["dead_candidates"], str(st["dead_candidates"]))
check("n_dead==2", st["n_dead"] == 2, str(st["n_dead"]))

# ── 4. COLDSTART: 카드<30 → mode A floor (HEALTH 무관) ──
print("[4] COLDSTART")
fires = [{"asig": "x1", "fired": ["c0"]}] * 60       # eligible 충분해도
fails = [{"asig": "x1", "kind": "objective_fail"}]    # health=0 이라도
st = compute(fires, fails, ["c0"], prior_mode="B")    # 카드 1 < 30
check("coldstart (cards<30)", st["coldstart"] is True, str(st["coldstart"]))
check("mode=A floor overrides health", st["mode"] == "A", "%s health=%s" % (st["mode"], st["health"]))

# ── 5. PRECISION(시간역순 대체): fired-only / fail-only → 약한카드 아님 ──
print("[5] PRECISION (공존 경계)")
# asig F1 = fire 만(fail 없음), asig F2 = fail 만(fire 없음=카드 없었음)
fires = [{"asig": "F1", "fired": ["cardSolo"]}, {"asig": "F1", "fired": ["cardSolo"]}]
fails = [{"asig": "F2", "kind": "objective_fail"}, {"asig": "F2", "kind": "objective_fail"}]
st = compute(fires, fails, ["cardSolo"], prior_mode="A")
check("cardSolo NOT weak (its asig never failed)", "cardSolo" not in weak_slugs(st), str(weak_slugs(st)))
check("F2 fail without fire → not recur", st["n_recur_asigs"] == 0, "n_recur=%d" % st["n_recur_asigs"])
check("health=1.0 (no recur among fails)", st["health"] == 1.0, str(st["health"]))

# ── 6. HYSTERESIS hold: 밴드 내(0.70<health<0.85) → 이전 mode 유지 ──
print("[6] HYSTERESIS hold")
# 10 fail asig, 2 recur → 재발률 0.2, health 0.8 (0.70<0.8<0.85 밴드)
fires = [{"asig": "r1", "fired": ["c"]}, {"asig": "r2", "fired": ["c"]}]
fails = [{"asig": "r%d" % i, "kind": "objective_fail"} for i in range(1, 11)]  # r1..r10, r1/r2=recur
# coldstart 회피 위해 35 카드 + 50 eligible 보강
fires += [{"asig": "e%02d" % i, "fired": ["c%d" % (i % 35)]} for i in range(50)]
slugs = ["c%d" % i for i in range(35)]
st_b = compute(fires, fails, slugs, prior_mode="B")
st_a = compute(fires, fails, slugs, prior_mode="A")
check("band health 0.80", abs(st_b["health"] - 0.8) < 0.001, str(st_b["health"]))
check("hold keeps prior B", st_b["mode"] == "B", st_b["mode"])
check("hold keeps prior A", st_a["mode"] == "A", st_a["mode"])

# ── 7. PRECISION — excerpt↔topic overlap (trigger-overlap 필터) ──
print("[7] PRECISION (excerpt↔topic)")
# cardReal(topic=foo) 는 실패 excerpt 에 foo → 진짜 재발 → weak
# cardNoise(topic=baz) 는 실패 excerpt 에 baz 없음 → trigger-overlap → 제외
ct = {"cardReal": {"foo", "bar"}, "cardNoise": {"baz"}}
fires = [
    {"asig": "P1", "fired": ["cardReal"]}, {"asig": "P1", "fired": ["cardReal"]},
    {"asig": "P2", "fired": ["cardNoise"]}, {"asig": "P2", "fired": ["cardNoise"]},
]
fails = [{"asig": "P1", "kind": "objective_fail"}, {"asig": "P2", "kind": "objective_fail"}]
fe = {"P1": "UnicodeDecodeError foo bar trace", "P2": "syntax error near unexpected token"}
st = compute(fires, fails, ["cardReal", "cardNoise"], "A", ct, fe)
check("cardReal weak (topic in excerpt)", "cardReal" in weak_slugs(st), str(weak_slugs(st)))
check("cardNoise NOT weak (trigger-overlap, no topic)", "cardNoise" not in weak_slugs(st), str(weak_slugs(st)))
check("only P1 recur (P2 filtered)", st["n_recur_asigs"] == 1, "n_recur=%d" % st["n_recur_asigs"])
# 같은 데이터에 precision 인자 없으면(legacy) 둘 다 약한카드(co-occurrence)
st2 = compute(fires, fails, ["cardReal", "cardNoise"], "A")
check("legacy(no precision) keeps both", {"cardReal", "cardNoise"} == weak_slugs(st2), str(weak_slugs(st2)))

# ── 8. VERDICT — agent repair_verdict(루프 클로저) ──
print("[8] VERDICT (repair_verdict 존중)")
ct = {"cardFix": {"foo"}}
fires = [{"asig": "V1", "fired": ["cardFix"]}, {"asig": "V1", "fired": ["cardFix"]}]
fails = [{"asig": "V1", "kind": "objective_fail"}]
fe = {"V1": "foo error trace"}
# verdict 없으면 약한카드(정상 recur, topic 겹침)
st = compute(fires, fails, ["cardFix"], "A", ct, fe)
check("no verdict → cardFix weak", "cardFix" in weak_slugs(st), str(weak_slugs(st)))
# agent 가 already-fixed 판정 → 제외(재플래그 방지)
st2 = compute(fires, fails, ["cardFix"], "A", ct, fe, {"cardFix": "already-fixed"})
check("already-fixed verdict → excluded", "cardFix" not in weak_slugs(st2), str(weak_slugs(st2)))
check("trigger-overlap verdict → excluded",
      "cardFix" not in weak_slugs(compute(fires, fails, ["cardFix"], "A", ct, fe, {"cardFix": "trigger-overlap"})))

print("\n==== %d PASS / %d FAIL ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
