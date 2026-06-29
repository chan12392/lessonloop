#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""harvest_pref.py — LessonLoop HARVEST (사용자선호) 단계.

soft 신호(유저 불만/교정 발화) → LLM 성향추출 → 사용자선호 candidate.

설계(DESIGN §1원칙2 캡처≠품질, §3-b soft):
  - capture.py 가 RE_SOFT 로 잡은 soft 는 맥락 無 단어매칭 → 오탐 다수.
  - regex 1차 게이트(이미 capture 단) → **LLM 2차 게이트**: 오탐 판별 + 성향 명제 추출 동시.
  - LLM 이 is_signal=false 하면 노이즈로 드랍. true 면 candidate.
  - LLM = 옵션(repair.py --api 패턴 재사용). 키 없으면 리포트만.

출력: staging/pref-<slug>.md (l1:사용자선호, provisional) → promote.py 검증.
fail-open, exit 0. cp949 콘솔 한글 깨징 방지.
"""
import sys, os, io, re, json
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPTS = Path(__file__).resolve().parent
try:
    from paths import journal as journal_path, cards_dir
except ImportError:
    ROOT = SCRIPTS.parent
    journal_path = lambda a: ROOT / f"journal-{a}.jsonl"
    cards_dir = lambda: ROOT

# repair.py LLM seam 재사용(중복구현 금지)
try:
    from repair import pick_provider, call_openai, call_anthropic
except ImportError:
    pick_provider = None

MODEL_DEFAULT = os.environ.get("LESSONLOOP_PREF_MODEL", "gpt-4o-mini")
CALLERS = {"openai": call_openai, "anthropic": call_anthropic} if pick_provider else {}


def _agent():
    for i, a in enumerate(sys.argv):
        if a == "--agent" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--agent="):
            return a.split("=", 1)[1]
    return os.environ.get("LESSONLOOP_AGENT", "default")


AGENT = _agent()
JOURNAL = journal_path(AGENT)
STAGING = cards_dir() / "staging"
HSTATE = cards_dir() / f".harvest_pref_state-{AGENT}.json"


def sh(s):
    import hashlib
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def load_hstate():
    try:
        return set(json.loads(io.open(HSTATE, encoding="utf-8").read()))
    except Exception:
        return set()


def save_hstate(s):
    io.open(HSTATE, "w", encoding="utf-8", newline="\n").write(
        json.dumps(sorted(s), ensure_ascii=False))


def collect_soft():
    """journal 의 soft 신호(미처리) 수집."""
    done = load_hstate()
    rows = []
    if not JOURNAL.exists():
        return rows, done
    for ln in io.open(JOURNAL, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            e = json.loads(ln)
        except Exception:
            continue
        if e.get("kind") != "soft":
            continue
        if e.get("sig") in done:
            continue
        rows.append(e)
    return rows, done


PROMPT = """아래는 AI 에이전트와 작업하는 사용자가 표현한 발화다. 이전 에이전트 행동/산출물에 대한 불만·교정·취향 표현일 수 있다.

판단:
(1) 이 발화가 사용자의 *성향·취향·선호*(앞으로 비슷한 상황에서 일관되게 원하는/원치 않는 것)인가?
    아니면 단순 질문·일회성 지시·맥락 없는 잡담(노이즈)인가?
- 불만/교정 = negative, 칭찬/선호 표현 = positive.
- "X 말고 Y로 해", "이런 식 싫어", "항상 이렇게 해줘" = 성향 신호.
- "이거 확인해볼래?", "A 해줘"(단발 지시) = 노이즈.

발화:
«{excerpt}»

출력(JSON):
- 성향 신호면: {{"is_signal": true, "polarity": "negative|positive",
  "l2": "<주제/도메인 한두단어>", "trigger": "<3~6개 literal 토큰, 이 주제 언급시 recall 발동>",
  "rule": "<한 줄 명령형 명세 — 이 성향이 요구하는 행동>",
  "facts": "<발화에서 뽑은 근거 verbatim>",
  "fix": "<구체 행동>", "check": "<셀프체크>"}}
- 노이즈면: {{"is_signal": false, "reason": "<왜 노이즈인지>"}}
"""


def build_prompt(excerpt):
    return PROMPT.format(excerpt=(excerpt or "").replace("\n", " ")[:400])


def emit_candidate(extract, ev):
    """LLM 추출결과 → 사용자선호 candidate 카드."""
    trig = re.sub(r"\s+", " ", (extract.get("trigger") or "")).strip()
    rule = (extract.get("rule") or "").strip()
    if not trig or not rule:
        return None, "trigger/rule 비어 추출 실패"
    l2 = (extract.get("l2") or "일반").strip() or "일반"
    polarity = "negative" if extract.get("polarity") == "negative" else "positive"
    cid = sh(rule + "|" + trig)
    slug = re.sub(r"[^a-z0-9가-힣]+", "-", l2.lower()).strip("-") or "pref"
    slug = f"pref-{slug}-{cid[:6]}"
    src = ev.get("excerpt", "").replace("\n", " ")[:300]
    fm = [
        "---",
        "l1: 사용자선호",
        f"l2: {l2}",
        f'trigger: "{trig}"',
        f"rule: {rule}",
        "enforce: manual",
        f"severity: {'high' if polarity == 'negative' else 'low'}",
        "sources: 1",
        "recall_when: context",
        f"polarity: {polarity}",
        "status: candidate",
        "provisional: true",
        "writer_model: harvest_pref-llm",
        f"slug: {slug}",
        "---",
        "## facts",
        f"유저 발화(soft 신호): «{src}»",
        f"추출 근거: {extract.get('facts','')}",
        "## fix",
        extract.get("fix") or rule,
        "## check",
        extract.get("check") or "(미정 — 큐레이션 시 기입)",
    ]
    path = STAGING / f"{slug}.md"
    io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(fm) + "\n")
    return path, None


def main():
    use_api = "--api" in sys.argv
    dry = "--dry-run" in sys.argv
    soft, done = collect_soft()
    if not soft:
        print("harvest_pref: 처리할 soft 신호 없음.")
        return
    print("harvest_pref: soft 신호 %d개 수집." % len(soft))

    if dry:
        for e in soft:
            print("  [DRY] «%s»" % e.get("excerpt", "")[:80])
        print("  --api 로 실제 추출.")
        return

    if not use_api or not pick_provider:
        print("harvest_pref: LLM 없음(--api 또는 키). 리포트만:")
        for e in soft:
            print("  - «%s»" % e.get("excerpt", "")[:80])
        return

    prov = pick_provider()
    if not prov:
        print("harvest_pref: LLM 키 없음(OPENAI_API_KEY/ANTHROPIC_API_KEY). 리포트만.")
        for e in soft:
            print("  - «%s»" % e.get("excerpt", "")[:80])
        return
    # z.ai/glm 등 커스텀 모델: LESSONLOOP_PREF_MODEL 로 덮어쓰기
    prov[1]["model"] = os.environ.get("LESSONLOOP_PREF_MODEL", prov[1].get("model", MODEL_DEFAULT))
    caller = CALLERS[prov[0]]
    STAGING.mkdir(exist_ok=True)

    staged = dropped = errors = 0
    for e in soft:
        try:
            raw = caller(build_prompt(e.get("excerpt", "")), **prov[1])
            # JSON 추출(response_format 미지원 provider 대비 방어)
            m = re.search(r"\{[\s\S]*\}", raw or "")
            extract = json.loads(m.group(0)) if m else {}
        except Exception as ex:
            sys.stderr.write("  [harvest_pref] LLM 실패(safe-skip): %s\n" % ex)
            errors += 1
            continue
        if not extract.get("is_signal"):
            dropped += 1
            done.add(e["sig"])
            print("  DROP(노이즈): «%s» — %s" % (e.get("excerpt", "")[:60], extract.get("reason", "")))
            continue
        path, why = emit_candidate(extract, e)
        if path:
            done.add(e["sig"])
            staged += 1
            print("  STAGE → %s  [%s] %s" % (path.name, extract.get("polarity"), extract.get("rule", "")[:50]))
        else:
            sys.stderr.write("  [harvest_pref] emit 실패: %s\n" % why)

    save_hstate(done)
    print("\nstaged=%d dropped=%d errors=%d" % (staged, dropped, errors))
    if staged:
        print("다음: `py scripts/promote.py` 로 검증·canonical 화.")


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        sys.stderr.write("[lessonloop] harvest_pref fail-open: %s\n" % ex)
    sys.exit(0)
