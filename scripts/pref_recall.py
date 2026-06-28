#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pref_recall.py — LessonLoop 사용자선호 context-time recall.

CC UserPromptSubmit / Hermes pre_llm_call 양쪽 대응(런타임 자동분기).

유저 프롬프트(=작업 도메인 신호) → l1:사용자선호 카드의 trigger exact 매칭 → 관련 선호 주입.

tool-time recall(recall_hook)과 분리:
  - recall_hook = 기술/행동 카드, 행동(tool) 직전.
  - pref_recall = 사용자선호 카드, 프롬프트(도메인 진입) 시점.
  ※ 프롬프트-*유사도*(시맨틱) ❌ = §6 거부패턴. 여기는 exact-trigger + 개수채점 + 임계 → 스팸 아님.

런타임 분기(hook_event_name):
  CC     UserPromptSubmit → payload.prompt → additionalContext 주입.
  Hermes pre_llm_call     → extra.user_message → {"context":..} 주입.
    (pre_llm_call 은 tool_name/tool_input=null, user_message 는 extra 안에)

fail-open(절대 프롬프트 차단 안 함). 매칭 없으면 무출력.
"""
import sys, json, csv, io, re, math
from pathlib import Path

# 경로 진실원천
SCRIPTS = Path(__file__).resolve().parent
try:
    from paths import index_file
except ImportError:
    # 하위 호환: 단돈 실행 시 fallback
    ROOT = SCRIPTS.parent
    index_file = lambda: ROOT / "lessons_index.csv"

INDEX = index_file()   # 이식성: 하드코딩 제거
MAX_CARDS = 2
MIN_TOKEN_LEN = 2
SCORE_MIN = 1.2
STOP_TOKENS = {
    "the", "and", "for", "그리고", "그러면", "근데", "해줘", "하자", "가자", "이거",
    "지금", "다음", "작업", "진행", "해서", "해야", "있는", "없는", "좀", "걸", "거",
}


def tokenize_trigger(trig):
    out = set()
    for t in re.split(r"\s+", (trig or "").replace('"', "").strip()):
        tl = t.lower()
        if len(tl) >= MIN_TOKEN_LEN and tl not in STOP_TOKENS:
            out.add(tl)
        if "-" in tl:
            for sub in tl.split("-"):
                if len(sub) >= MIN_TOKEN_LEN and sub not in STOP_TOKENS:
                    out.add(sub)
    return out


_ALNUM = re.compile(r"^[a-z0-9]+$")
_HAN = re.compile(r"^[가-힣]+$")


def token_matches(token, text):
    """영숫자(html)=좌우 영숫자 경계(우측 한글 조사 허용: 'html로' 매칭).
    한글(바탕화면)=조사 부착 흔함 → substring('바탕화면에' 매칭). 구분자=substring."""
    if not text:
        return False
    if _ALNUM.match(token):
        return re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", text) is not None
    if _HAN.match(token):
        return token in text
    return token in text


def _is_hermes(payload):
    """런타임 감지: hook_event_name 값. Hermes=pre_llm_call 등. CC=UserPromptSubmit."""
    ev = (payload.get("hook_event_name") or "").lower()
    if ev in ("pre_tool_call", "pre_llm_call", "post_tool_call"):
        return True
    if ev in ("pretooluse", "userpromptsubmit"):
        return False
    return "cwd" in payload


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    hermes = _is_hermes(payload)
    # CC=payload.prompt / Hermes pre_llm_call=extra.user_message(tool_name/tool_input=null).
    if hermes:
        prompt = ((payload.get("extra") or {}).get("user_message") or "").lower()
    else:
        prompt = (payload.get("prompt") or "").lower()
    if not prompt.strip() or not INDEX.exists():
        return
    try:
        rows = [r for r in csv.DictReader(io.open(INDEX, encoding="utf-8"))
                if r.get("l1") == "사용자선호"]
    except Exception:
        return
    if not rows:
        return

    # IDF 안 씀 — pref 코퍼스가 작아(N=2~) IDF 무의미. 매칭 토큰 *개수* 로 채점:
    # 도메인 토큰 2개+ 겹침 = 관련(단일 우연 차단), 단 매우 특이한 단일 토큰(≥8자)은 단독 허용.
    MIN_HITS = 2
    scored = []
    for r in rows:
        toks = tokenize_trigger(r.get("trigger", ""))
        hit = [t for t in toks if token_matches(t, prompt)]
        if len(hit) >= MIN_HITS or any(len(t) >= 8 for t in hit):
            scored.append((len(hit), r))
    if not scored:
        return
    scored.sort(key=lambda x: -x[0])

    lines = ["[LESSONLOOP — 사용자선호 (이 도메인 작업 시 유의)]"]
    for score, r in scored[:MAX_CARDS]:
        lines.append(f"• {r['rule']}  (선호: {r['slug']})")
    text = "\n".join(lines)
    if hermes:
        # Hermes pre_llm_call: {"context":..} → user message 에 주입(prompt cache 보존).
        print(json.dumps({"context": text}, ensure_ascii=False))
    else:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": text,
            }
        }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
