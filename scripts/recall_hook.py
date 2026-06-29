#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recall_hook.py — LessonLoop tool-time recall (CC PreToolUse / Hermes pre_tool_call).

stdin(JSON): {tool_name, tool_input:{command/file_path/content/...}, hook_event_name?, cwd?}
→ 행동 신호에서 trigger 토큰 exact 매칭 → 매칭 카드 rule 주입/차단.

런타임 자동분기(hook_event_name 으로):
  CC     PreToolUse   → additionalContext 주입(부드러운 경고, 차단X). SCORE_MIN=1.2, top-2.
  Hermes pre_tool_call → {"decision":"block","reason":rule}(강제차단). SCORE_MIN=2.5, top-1.
    block 만 지원(Hermes는 allow-with-context 없음) → 강제라 오탐 치명, 임계 높임.

신호 가중:
  HIGH = command·file_path·path·description (풀 IDF 가중)
  LOW  = content·new_string·old_string (0.5 가중; 산문파일 .md/.txt 는 skip)
  → "개념을 *논의*하는 산문"이 "*수행*"처럼 잡히는 메타-노이즈 차단.

매칭 = 로컬 lessons_index.csv trigger 컬럼 substring + IDF. 모델 토큰 0. fail-open.
"""
import sys, os, json, csv, io, re, math, time, hashlib
from pathlib import Path

# stdin/stdout UTF-8 강제(Win cp949 콘솔에서 한글 command/file_path/content 깨짐 →
# trigger 매칭 실패/조용히 fail 방지). pref_recall 동일 하드닝. file-encoding-bom-newline 계열.
try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 경로 진실원천
SCRIPTS = Path(__file__).resolve().parent
try:
    from paths import index_file, recall_log as recall_log_path, cards_dir
except ImportError:
    # 하위 호환: 단돏 실행 시 fallback
    ROOT = SCRIPTS.parent
    index_file = lambda: ROOT / "lessons_index.csv"
    recall_log_path = lambda a: ROOT / f"recall_log-{a}.jsonl"
    cards_dir = lambda: ROOT


def _weak_set():
    """FEEDBACK 산출 .feedback_state-{agent}.json 의 약한카드 slug→recur_count.
    에스컬레이션: recall 이 떴는데도 같은 행동이 또 실패한(재발) 카드는 강제등급 올림.
    파일 없음/깨징 → 빈 dict(fail-open, 일반 recall 유지). 모듈 로드시 1회 캐시."""
    d = {}
    try:
        st = json.loads((cards_dir() / f".feedback_state-{AGENT}.json").read_text(encoding="utf-8"))
        for w in st.get("weak") or []:
            if w.get("slug"):
                d[w["slug"]] = w.get("recur_count", 0)
    except Exception:
        pass
    return d

def _agent():
    """--agent X / --agent=X / env LESSONLOOP_AGENT / 'default'. per-agent 로그 분리."""
    for i, a in enumerate(sys.argv):
        if a == "--agent" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--agent="):
            return a.split("=", 1)[1]
    return os.environ.get("LESSONLOOP_AGENT", "default")


AGENT = _agent()
INDEX = index_file()                               # cards/index 는 공유
RECALL_LOG = recall_log_path(AGENT)                 # 로그는 per-agent (FEEDBACK 연료)
WEAK = _weak_set()                                  # FEEDBACK 약한카드(재발) — AGENT 정의 후 호출
MAX_CARDS = 2
MIN_TOKEN_LEN = 3
SCORE_MIN = 1.2          # 가중점수 미만이면 주입 안 함 (무관 명령 차단)
LOW_WEIGHT = 0.5         # content 매칭 가중
PROSE_EXT = (".md", ".txt", ".markdown")
STOP_TOKENS = {
    "the", "and", "for", "path", "file", "data", "json", "code", "git",
    "status", "import", "exe", "sync", "list", "name", "config", "run",
    "app", "test", "value", "result", "오류", "에러", "확인",
}


def _join(ti, keys):
    return " ".join(ti[k] for k in keys if isinstance(ti.get(k), str)).lower()


def action_sig(tool: str, ti: dict) -> str:
    """행동 단위 join 키 — capture 와 동일 공식(예외 무관). 약한/죽은카드 산출용."""
    prim = ""
    for k in ("command", "file_path", "path"):
        if isinstance(ti.get(k), str) and ti[k]:
            prim = ti[k]
            break
    return hashlib.sha1((str(tool) + "|" + prim)[:200].lower().encode("utf-8")).hexdigest()[:12]


def high_text(ti: dict) -> str:
    return _join(ti, ("command", "file_path", "path", "description"))


def low_text(ti: dict) -> str:
    fp = (ti.get("file_path") or ti.get("path") or "").lower()
    if fp.endswith(PROSE_EXT):      # 산문 = content 매칭 skip (메타-노이즈 차단)
        return ""
    return _join(ti, ("content", "new_string", "old_string"))


def tokenize_trigger(trig: str):
    raw = re.split(r"\s+", trig.replace('"', "").strip())
    out = set()
    for t in raw:
        tl = t.lower()
        if len(tl) >= MIN_TOKEN_LEN and tl not in STOP_TOKENS:
            out.add(tl)
        if "-" in tl:                # 하이픈 복합어 분해 (pm2-jlist→pm2,jlist)
            for sub in tl.split("-"):
                if len(sub) >= MIN_TOKEN_LEN and sub not in STOP_TOKENS:
                    out.add(sub)
    return out


_WORD = re.compile(r"^[a-z0-9가-힣]+$")


def token_matches(token: str, text: str) -> bool:
    """순수 영숫자 토큰은 word-boundary 매칭(lesson ⊄ lessonloop),
    구분자 포함 토큰(.bat, sed -i, <<, -f)은 substring 매칭."""
    if not text:
        return False
    if _WORD.match(token):
        return re.search(r"(?<![a-z0-9가-힣])" + re.escape(token) + r"(?![a-z0-9가-힣])", text) is not None
    return token in text


def build_idf(rows):
    N = len(rows) or 1
    df = {}
    for r in rows:
        for t in tokenize_trigger(r.get("trigger", "")):
            df[t] = df.get(t, 0) + 1
    return {t: math.log((N + 1) / (c + 0.5)) for t, c in df.items()}


def _is_hermes(payload):
    """런타임 감지: hook_event_name 값으로. Hermes=pre_tool_call 등 소문자스네이크.
    CC=PreToolUse/UserPromptSubmit. 명시 값 없으면 cwd 키(Hermes만)로 fallback."""
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
    ti = payload.get("tool_input") or {}
    hermes = _is_hermes(payload)
    # Hermes pre_tool_call = block(강제차단). 오탐 치명 → 임계 높게·1개만.
    score_min = 2.5 if hermes else SCORE_MIN
    max_cards = 1 if hermes else MAX_CARDS
    high, low = high_text(ti), low_text(ti)
    if not (high.strip() or low.strip()) or not INDEX.exists():
        return
    try:
        rows = list(csv.DictReader(io.open(INDEX, encoding="utf-8")))
    except Exception:
        return

    # 사용자선호 카드는 context-time(pref_recall, UserPromptSubmit) 담당 → tool-time 제외
    rows = [r for r in rows if r.get("l1") != "사용자선호"]
    idf = build_idf(rows)
    scored = []
    for r in rows:
        toks = tokenize_trigger(r.get("trigger", ""))
        hh = [t for t in toks if token_matches(t, high)]
        ll = [t for t in toks if token_matches(t, low) and not token_matches(t, high)]
        if hh or ll:
            score = sum(idf.get(t, 0) for t in hh) + LOW_WEIGHT * sum(idf.get(t, 0) for t in ll)
            if score > 0:
                scored.append((score, r))

    # tool 기반 트리거 — trigger_tools frontmatter 에 tool 명시된 카드는 토큰 무관 발화.
    # (토큰 매칭 불가한 툴-사용패턴 예: Write/Edit 전 Read 누락 — 경로 토큰과 무관)
    tool = payload.get("tool_name") or ""
    token_slugs = {r.get("slug", "") for _, r in scored}
    for r in rows:
        tt = [t.strip() for t in (r.get("trigger_tools") or "").split(",") if t.strip()]
        if tool and tool in tt and r.get("slug", "") not in token_slugs:
            scored.append((score_min + 0.01, r))   # sentinel: 임계 통과(노이즈가드 유지)
    if not scored:
        return
    scored.sort(key=lambda x: -x[0])
    if scored[0][0] < score_min:
        return

    top = [(s, r) for s, r in scored[:max_cards] if s >= score_min]
    if not top:
        return
    fired = [r.get("slug", "") for _, r in top]
    weak_fired = [(r.get("slug", ""), r) for _, r in top if r.get("slug") in WEAK]

    if hermes:
        # Hermes pre_tool_call: block 반환. message=rule → 모델이 tool error로 수신, 다음 시도 반영.
        # CC additionalContext(부드러운주입)와 달리 강제 1회차단이므로 임계 2.5로 오탐 가드.
        r = top[0][1]
        recur = WEAK.get(r.get("slug", ""))
        warn = f"  ⚠ 이 카드는 최근 {recur}회 재발(recall이 떴는데 또 실패) — rule 적용 필수.\n" if recur else ""
        reason = (f"[LessonLoop 관련 교훈 — 이 행동 직전]\n{warn}"
                  f"• {r['rule']}  (카드: {r['slug']})")
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    else:
        # Claude Code PreToolUse: additionalContext 주입(차단 아님, 부드러운 경고).
        # 에스컬레이션: 재발 약한카드 포함 시 강제확인 헤더 + 카드별 ⚠재발N회 태그.
        if weak_fired:
            lines = ["[LESSONLOOP — 🔴 재발 카드 경고: 아래 rule, 같은 행동이 반복 실패함 — 반드시 적용 후 진행]"]
        else:
            lines = ["[LESSONLOOP — 행동 직전 관련 교훈]"]
        for _, r in top:
            recur = WEAK.get(r.get("slug", ""))
            tag = f"  (⚠재발{recur}회, 강제적용)" if recur else ""
            lines.append(f"• {r['rule']}  (카드: {r['slug']}){tag}")
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "\n".join(lines),
            }
        }, ensure_ascii=False))

    # FEEDBACK 연료: 띄운 카드 + action_sig 기록 (best-effort, fail-open)
    try:
        tool = payload.get("tool_name") or ""
        rec = {"t": int(time.time()), "tool": tool,
               "asig": action_sig(tool, ti), "fired": fired,
               "score": round(scored[0][0], 2)}
        with io.open(RECALL_LOG, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
