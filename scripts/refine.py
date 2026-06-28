#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""refine.py — LessonLoop REFINE 단계 (harvest → promote 사이 품질 트리아지).

staging candidate 를 *어떤 메커니즘에 속하는지* 판정(없는 이해를 지어내지 않음):

  1) 특이 주제 trigger(python·ssh·.bat) → trigger-recall 적합 → ready (promote)
  2) 일반행동(open·dict접근 = 특이토큰 없음) → recall 약함 → enforce=lint 로 라우팅
       + recall_weak:true (상시-조언 영역; trigger-recall 부적합을 정직히 표시)
  3) 빈 trigger / 모호 → needs_human:true → staging 보류(이해주체 = LLM/에이전트 대기)

제로인프라 결정적 트리아지. 실제 rule/fix *재작성*(이해 필요)은 옵션 refiner seam:
  --llm  : 환경에 OPENAI/ANTHROPIC 키 있으면 배치 정제(미구현 = 정직 스텁, 지어내기 금지).
  기본   : 트리아지만. 에이전트가 needs_human 카드를 직접 정제(self-witness)해도 됨.

frontmatter 에 triage 결과를 in-place 기록. promote 가 needs_human 은 건너뜀.
"""
import sys, io, re, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "staging"

# 일반행동(특이 주제토큰 아님) — 이것만이면 recall 약함 → lint 영역
COMMON_ACTION = {"open", "read", "write", "get", "load", "run", "cat", "ls",
                 "put", "find", "copy", "move", "list", "set"}
FIX_PLACEHOLDER = "정제 필요"


def parse_front(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return None, None, None
    fm, order = {}, []
    for line in m.group(1).splitlines():
        mm = re.match(r"^([a-z0-9_]+):\s*(.*)$", line)
        if not mm:
            continue
        k, v = mm.group(1), mm.group(2).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        fm[k] = v
        order.append(k)
    return fm, order, m.group(2)


def trig_tokens(trig):
    return [t for t in re.split(r"\s+", (trig or "").replace('"', "").strip()) if t]


def is_distinctive(toks):
    """특이 주제 토큰 하나라도 있나 — 확장자·플래그·특정 exe·일반행동 아닌 식별자."""
    for t in toks:
        tl = t.lower()
        if re.match(r"\.[a-z0-9]+$", tl) or tl.startswith("-"):
            return True
        if len(tl) >= 3 and tl not in COMMON_ACTION:
            return True
    return False


def only_extensions(toks):
    return bool(toks) and all(re.match(r"\.[a-z0-9]+$", t.lower()) for t in toks)


def triage(fm, body):
    """(verdict, reason, patch) — verdict ∈ ready|lint_weak|needs_human.

    1순위 = harvest 의 subject(아는 주제성격). 기계 토큰추측은 'tech'(우연)와
    'robocopy'(진짜주제)를 못 가리므로, subject 가 있으면 그걸 신뢰.
    """
    toks = trig_tokens(fm.get("trigger", ""))
    rule = (fm.get("rule") or "").strip()
    if not rule or len(rule) < 8:
        return "needs_human", "rule 부실 — 이해주체가 작성 필요", {}
    if not toks:
        return "needs_human", "trigger 없음 — recall 불가, 이해주체가 주제 부여 필요", {}

    subj = fm.get("subject", "")
    if subj == "distinctive":
        return "ready", "특이 주제(hint 정렬) → trigger-recall 적합", {"status": "candidate"}
    if subj == "generic":
        return "lint_weak", "일반행동 클래스(맵이 특이주제 없다고 판정) — recall 약함, lint 영역", \
            {"enforce": "lint", "recall_weak": "true"}
    if subj == "unknown":
        return "needs_human", "미지 클래스 — 주제 불명, 이해주체가 trigger·rule 정렬 필요", {}

    # subject 없음(구버전 카드) → 토큰 휴리스틱 폴백
    if is_distinctive(toks) and not only_extensions(toks):
        return "ready", "특이 주제 trigger(휴리스틱) → trigger-recall 적합", {"status": "candidate"}
    return "lint_weak", "일반행동/확장자만(휴리스틱) — recall 약함, lint 영역", \
        {"enforce": "lint", "recall_weak": "true"}


def apply_patch(text, fm, order, patch, verdict, reason):
    """frontmatter in-place 갱신 — triage 메타 주입(append 아님)."""
    for k, v in patch.items():
        fm[k] = v
    fm["triage"] = verdict
    # 기존 라인 교체 + 신규 키 추가
    lines = text.split("\n")
    out, seen = [], set()
    # frontmatter 영역만 처리(비정상 파일 → 손대지 않고 원문 반환, promote/cycle fail-open)
    if not lines or lines[0].strip() != "---":
        return text
    closing = None
    for k in range(1, len(lines)):
        if lines[k].strip() == "---":
            closing = k
            break
    if closing is None:                   # 닫는 --- 없음 → 원문 반환
        return text
    out.append("---")
    j = 1
    while j < closing:
        mm = re.match(r"^([a-z0-9_]+):", lines[j])
        if mm and mm.group(1) in fm:
            k = mm.group(1)
            if k not in seen:
                out.append(f"{k}: {fm[k]}" if k != "trigger" else f'trigger: "{fm[k]}"')
                seen.add(k)
        else:
            out.append(lines[j])
        j += 1
    for k in ("enforce", "recall_weak", "triage"):
        if k in fm and k not in seen:
            out.append(f"{k}: {fm[k]}")
            seen.add(k)
    out.append("---")
    out.append(f"<!-- refine: {verdict} — {reason} -->")
    out.extend(lines[closing + 1:])
    return "\n".join(out)


def main():
    use_llm = "--llm" in sys.argv
    if use_llm:
        print("REFINE --llm: 옵션 refiner 미구성(API키/플러그인 없음). 지어내기 금지 → 트리아지만 수행.")
    cands = sorted(STAGING.glob("*.md"))
    if not cands:
        print("staging 비어있음.")
        return
    tri = {"ready": 0, "lint_weak": 0, "needs_human": 0}
    rows = []
    for p in cands:
        text = io.open(p, encoding="utf-8").read()
        fm, order, body = parse_front(text)
        if fm is None:
            continue
        verdict, reason, patch = triage(fm, body or "")
        tri[verdict] += 1
        new = apply_patch(text, fm, order, patch, verdict, reason)
        io.open(p, "w", encoding="utf-8", newline="\n").write(new.rstrip() + "\n")
        rows.append((verdict, p.name, fm.get("trigger", ""), reason))

    print("=== REFINE 트리아지 ===")
    for v in ("ready", "lint_weak", "needs_human"):
        for verdict, name, trig, reason in rows:
            if verdict == v:
                mark = {"ready": "✓", "lint_weak": "~", "needs_human": "?"}[v]
                print(f"  {mark} [{v}] {name}  trig=\"{trig}\"")
                print(f"      → {reason}")
    print(f"\nready={tri['ready']} lint_weak={tri['lint_weak']} needs_human={tri['needs_human']}")
    if tri["needs_human"]:
        print("needs_human → 이해주체(LLM/에이전트)가 trigger 주제·rule 정제 후 재-refine.")
    if tri["ready"] or tri["lint_weak"]:
        print("ready+lint_weak → `py scripts/promote.py` 로 canonical 화 (promote 가 needs_human 은 보류).")


if __name__ == "__main__":
    main()
