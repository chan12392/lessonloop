#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""harvest.py — LessonLoop HARVEST 단계 (저널 → staging candidate).

journal.jsonl → 군집(빈도게이트) → sanitize → 템플릿 rule → staging/<id>.md
  → promote.py 가 검증·canonical 화.

빈도게이트(DESIGN §3, 1회실수→다음차단):
  - objective_fail : **1회 = 즉시** candidate (얄짤없음)
  - soft           : N회 게이트 — 리포트만(auto-write 금지, §3-b NUDGE)
  - env_fail       : skip(비교훈)

sanitize(promote validate 통과 = 방어선): 날짜·절대경로·봇명 토큰 제거.
rule 작성:
  - 기본 = 제로인프라 템플릿(provisional, trigger 정확 + raw excerpt 동봉).
  - 옵션 = LLM refine(--llm, 별도 플러그인 seam). 채택자 API키 강제 ❌.
처리한 sig 는 .harvest_state.json 에 기록 → 재실행 중복 staging 방지.
"""
import sys, os, io, re, json, hashlib
from pathlib import Path

# 경로 진실원천
SCRIPTS = Path(__file__).resolve().parent
try:
    from paths import journal as journal_path, cards_dir, cards_dir_cards
except ImportError:
    # 하위 호환: 단돈 실행 시 fallback
    ROOT = SCRIPTS.parent
    journal_path = lambda a: ROOT / f"journal-{a}.jsonl"
    cards_dir = lambda: ROOT
    cards_dir_cards = lambda: ROOT / "cards"

def _agent():
    for i, a in enumerate(sys.argv):
        if a == "--agent" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--agent="):
            return a.split("=", 1)[1]
    return os.environ.get("LESSONLOOP_AGENT", "default")


AGENT = _agent()
JOURNAL = journal_path(AGENT)                      # per-agent 수집분 처리
STAGING = cards_dir() / "staging"                  # staging/cards 는 공유(순차 처리)
HSTATE = cards_dir() / f".harvest_state-{AGENT}.json"

SOFT_GATE = 2           # soft 신호 N회 이상 재발해야 리포트(노이즈 차단)
TRIG_MAX = 6

# 예외/신호 → (l2, severity, rule, trigger_hint).
# trigger_hint = 큐레이션된 *주제* 토큰(=손으로 박은 이해). 있으면 기계 trigger 를 덮어씀
#   → lesson 주제 ≠ 그때 명령토큰 케이스의 trigger↔rule 불일치 결정적 해소.
# 빈 hint = 일반행동 lesson(인코딩·dict접근 = 특이 주제토큰 없음) → 기계/ext 폴백에 맡김.
EXC_MAP = {
    "unicodedecodeerror": ("인코딩", "medium", "파일 읽을 때 인코딩 명시(encoding=) — 기본값 가정 말고 utf-8/cp949 확인", ""),
    "unicodeencodeerror": ("인코딩", "medium", "출력/쓰기 인코딩 명시 — 콘솔 cp949 깨짐은 -X utf8 또는 PYTHONUTF8", ""),
    "keyerror":           ("데이터", "medium", "dict 키 접근 전 존재 확인(.get·in) — 스키마 가정 금지", ""),
    "indexerror":         ("데이터", "medium", "시퀀스 인덱싱 전 길이 확인 — 빈 결과 경계 처리", ""),
    "typeerror":          ("데이터", "medium", "타입 가정 전 isinstance/None 체크", ""),
    "valueerror":         ("데이터", "low", "파싱/변환 입력 검증 후 처리", ""),
    "attributeerror":     ("데이터", "medium", "객체 속성 접근 전 None/타입 확인", ""),
    "filenotfounderror":  ("경로", "medium", "파일 접근 전 존재 확인 + 경로 폴백", ""),
    "modulenotfounderror":("환경", "medium", "임포트 전 의존성/venv 확인 — pip install 또는 venv 활성", "modulenotfounderror import"),
}
# 신호 키워드(예외 없을 때) → (l2, severity, rule, trigger_hint).
SIG_MAP = {
    "exit code 49": ("런타임", "high", "Windows `python`은 Store stub(exit 49) — `py` 런처 쓰기", "python"),
    "ssh":          ("인프라", "low", "원격 명령 실패 시 ConnectTimeout/키/호스트부터 확인", "ssh"),
    "robocopy":     ("인프라", "low", "robocopy exit<8은 성공(1=복사됨) — 0 비교로 실패판정 금지", "robocopy"),
}

RE_DATE_TOK = re.compile(r"\d{4}-\d{2}-\d{2}|\d{4}|bak-\d")
RE_PATHY = re.compile(r"[/\\]|^[a-z]:$|appdata|users|home|temp")
BANNED = {"myagent", "assistant"}  # add your own agent/bot names to scrub them from captured cards


def sh(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def sanitize_trigger(toks):
    out = []
    for t in toks:
        tl = str(t).lower()
        if RE_DATE_TOK.search(tl) or RE_PATHY.search(tl):
            continue
        if tl in BANNED or len(tl) < 2:
            continue
        if tl not in out:
            out.append(tl)
    return out[:TRIG_MAX]


COMMON_ACTION = {"open", "read", "write", "get", "load", "run", "cat", "ls", "put"}


def cluster_key(e):
    """재발 군집키. 예외클래스 있으면 그걸로(같은 타입 병합),
    없으면(exit code 류) tool+첫 행동토큰 — 'exit code N' 충돌 방지."""
    err = (e.get("error") or "").lower()
    if err in EXC_MAP:
        return err
    toks = [t for t in (e.get("trigger") or []) if not t.startswith("-")]
    return (e.get("tool", "?").lower() + ":" + (toks[0] if toks else err or "?"))


def enforce_for(trig):
    """특이 토큰(확장자·플래그·특정 exe) 있으면 recall 잘 발동 → hook.
    흔한 행동(open·read)만이면 recall 약함 → manual(lint/상시 영역).
    ※ 현재 enforce 는 메타데이터 — recall 은 trigger+IDF 로 발동. 라우팅은 향후 lint/자동화용."""
    for t in trig:
        if re.match(r"\.[a-z0-9]+$", t) or t.startswith("-") or (len(t) >= 3 and t not in COMMON_ACTION):
            return "hook"
    return "manual"


def classify(ev):
    """이벤트 → (l2, severity, rule, enforce). 매핑 없으면 generic."""
    err = (ev.get("error") or "").lower()
    if err in EXC_MAP:
        l2, sev, rule, hint = EXC_MAP[err]
        return l2, sev, rule, hint
    exc = (ev.get("excerpt") or "").lower()
    for key, (l2, sev, rule, hint) in SIG_MAP.items():
        if key in err or key in exc or any(key in t for t in ev.get("trigger", [])):
            return l2, sev, rule, hint
    return "런타임", "medium", None, ""   # generic → 템플릿 + 기계 trigger fallback


def template_rule(ev, rule):
    if rule:
        return rule
    sub = ev.get("error") or ev.get("tool") or "작업"
    return f"{sub} 관련 과거 실패 있었음 — 행동 전 facts 확인 후 진행 (provisional, 정제 필요)"


def emit_candidate(ev, count):
    l2, sev, rule_raw, hint = classify(ev)
    # 큐레이션 hint 있으면 trigger 덮어씀(주제↔rule 정렬). 없으면 기계 trigger(행동모양).
    trig = sanitize_trigger(hint.split() if hint else ev.get("trigger", []))
    if not trig:                              # trigger 비면 recall 불가 → 스킵
        return None, "trigger 없음(sanitize 후)"
    # subject = harvest 가 *아는* 주제 성격(refine 트리아지 입력 — 토큰추측 능가):
    #   distinctive(hint 있음=특이주제) / generic(맵매치+hint없음=일반행동) / unknown(맵 미매치)
    subject = "distinctive" if hint else ("generic" if rule_raw else "unknown")
    enforce = "lint" if subject == "generic" else enforce_for(trig)
    rule = template_rule(ev, rule_raw)
    cid = sh(rule + "|" + " ".join(trig))
    slug = re.sub(r"[^a-z0-9가-힣]+", "-", (ev.get("error") or l2).lower()).strip("-") or "card"
    slug = f"{slug}-{cid[:6]}"
    excerpt = (ev.get("excerpt") or "").replace("\n", " ")[:300]
    fm = [
        "---",
        f"l1: 기술",
        f"l2: {l2}",
        f'trigger: "{" ".join(trig)}"',
        f"rule: {rule}",
        f"enforce: {enforce}",
        f"severity: {sev}",
        f"sources: {count}",
        f"subject: {subject}",
        f"recall_weak: {'true' if subject == 'generic' else 'false'}",
        "status: candidate",
        "provisional: true",
        f"writer_model: harvest-template",
        f"slug: {slug}",
        "---",
        "## facts",
        f"원시 신호({ev.get('tool','')} / {ev.get('error','')}): {excerpt}",
        "## fix",
        "(정제 필요 — LLM refine 또는 에이전트가 실제 수정 기입)",
        "## check",
        "(self-check 미정)",
    ]
    path = STAGING / f"{slug}.md"
    io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(fm) + "\n")
    return path, None


def load_hstate():
    try:
        return set(json.loads(io.open(HSTATE, encoding="utf-8").read()))
    except Exception:
        return set()


def save_hstate(s):
    io.open(HSTATE, "w", encoding="utf-8", newline="\n").write(json.dumps(sorted(s), ensure_ascii=False))


def main():
    if not JOURNAL.exists():
        print("journal 없음 — 할 일 없음.")
        return
    STAGING.mkdir(exist_ok=True)
    done = load_hstate()
    rows = []
    for ln in io.open(JOURNAL, encoding="utf-8"):
        ln = ln.strip()
        if ln:
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass

    # soft 빈도 집계(게이트용)
    soft_count = {}
    for e in rows:
        if e.get("kind") == "soft":
            soft_count[e.get("hit", "")] = soft_count.get(e.get("hit", ""), 0) + 1

    # objective 군집: 같은 (error 또는 첫 trigger) = 재발 카운트
    clusters = {}     # key -> {ev, count, sigs}
    for e in rows:
        if e.get("kind") != "objective_fail":
            continue
        if e.get("sig") in done:
            continue
        key = cluster_key(e)
        c = clusters.setdefault(key, {"ev": e, "count": 0, "sigs": []})
        c["count"] += 1
        c["sigs"].append(e.get("sig"))

    staged, skipped, soft_rep = 0, [], []
    for key, c in clusters.items():
        path, why = emit_candidate(c["ev"], c["count"])
        if path:
            for s in c["sigs"]:
                done.add(s)
            staged += 1
            print(f"  STAGE [{key}] x{c['count']} -> {path.name}")
        else:
            skipped.append((key, why))

    # soft 리포트(게이트 통과분만, 카드 아님)
    for e in rows:
        if e.get("kind") != "soft" or e.get("sig") in done:
            continue
        if soft_count.get(e.get("hit", ""), 0) >= SOFT_GATE:
            soft_rep.append(e)
            done.add(e["sig"])

    save_hstate(done)
    print(f"\nstaged={staged} skipped={len(skipped)} soft_reported={len(soft_rep)}")
    for k, why in skipped:
        print(f"  SKIP [{k}]: {why}")
    if soft_rep:
        print("\nsoft 후보(유저 불만 재발 — 수동 검토, auto-card 안 함):")
        for e in soft_rep:
            print(f"  - {e.get('hit')!r}: «{e.get('excerpt','')[:60]}»")
    if staged:
        print(f"\n다음: `py scripts/promote.py` 로 staging 검증·canonical 화.")


if __name__ == "__main__":
    main()
