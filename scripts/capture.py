#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""capture.py — LessonLoop CAPTURE 단계 (Stop훅, 순수 스크립트, 제로모델).

Stop훅 stdin(JSON): {transcript_path, session_id, ...}
  → 직전 체크포인트 이후 *새 turn* 만 스캔(세션별 line offset 기억)
  → 객관 실패(tool_result is_error) + soft(유저 불만 텍스트) → journal.jsonl append
  → 모델 토큰 0, **절대 block 안 함**(fail-open, exit 0).

신호(DESIGN §3):
  (a) 객관 실패 = message.content[].type=='tool_result' & is_error==true (결정적) ★
      실패한 tool_use(id 매칭)에서 trigger 토큰 기계추출.
      환경노이즈(timeout/interrupt/안전차단)는 env 태그로 분리(교훈 아님).
  (b) soft = 유저 텍스트의 불만 표현 → auto-write 금지, 저널에 hint 로깅만.

저널 = raw 무손실 보장. 품질 게이트(증류·검증)는 harvest/promote(다운스트림).
1회차 객관실패 = 즉시 저널 → harvest가 candidate 화 → 2회차엔 recall이 막음.
"""
import sys, os, io, re, json, hashlib
from pathlib import Path

# 경로 진실원천
SCRIPTS = Path(__file__).resolve().parent
try:
    from paths import journal_dir, journal as journal_path, cards_dir, recall_log, sync_dir
except ImportError:
    # 하위 호환: 단독 실행 시 fallback
    ROOT = SCRIPTS.parent
    journal_dir = lambda: ROOT
    journal_path = lambda a: ROOT / f"journal-{a}.jsonl"
    cards_dir = lambda: ROOT
    recall_log = lambda a: ROOT / f"recall_log-{a}.jsonl"
    sync_dir = lambda: None

def _agent():
    """--agent X / --agent=X / env LESSONLOOP_AGENT / 'default'. per-agent 로그 분리."""
    for i, a in enumerate(sys.argv):
        if a == "--agent" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--agent="):
            return a.split("=", 1)[1]
    return os.environ.get("LESSONLOOP_AGENT", "default")


def _sync_dir():
    """--sync-dir X / --sync-dir=X / env LESSONLOOP_SYNC_DIR.
    journal 을 공유FS(예: 구글드라이브) 동기화 폴더에 *미러* → 다른 머신 수집기가 읽음.
    로컬 JOURNAL 은 dedup 기준(불변), 미러는 append-only 복제(fail-open)."""
    for i, a in enumerate(sys.argv):
        if a == "--sync-dir" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--sync-dir="):
            return a.split("=", 1)[1]
    sd = os.environ.get("LESSONLOOP_SYNC_DIR")
    if sd:
        return sd
    p = sync_dir()                # lessonloop.json 진실원천 (재설치 없이 런타임 변경)
    return str(p) if p else None


AGENT = _agent()
JOURNAL = journal_path(AGENT)                    # per-agent (멀티에이전트 수집 분리)
STATE = journal_dir() / f".capture_state-{AGENT}.json"
_SD = _sync_dir()
if _SD and not Path(_SD).is_absolute():
    sys.stderr.write(
        "[lessonloop] sync_dir 이 상대경로('%s') — 실행 cwd 기준(Hermes=AppData 등)으로 잡힘.\n"
        "  잘못된 위치에 미러 쓰일 수 있음. config_set.py --sync-dir '<절대경로>' 로 수정 권장.\n" % _SD
    )
SYNC_JOURNAL = (Path(_SD) / f"journal-{AGENT}.jsonl") if _SD else None  # 미러(공유FS) or None

# 환경/비교훈 노이즈 — 객관 실패지만 카드감 아님(인프라·안전·사용자중단)
ENV_NOISE = re.compile(
    r"exit code (143|137|130)\b|timed out|interrupted|keyboardinterrupt"
    r"|protected from removal|blocked\.|operation was cancel",
    re.I,
)
# 예외 시그니처 (trigger 의 핵심 토큰)
RE_EXC = re.compile(r"\b([A-Z][A-Za-z]*(?:Error|Exception|Warning))\b")
RE_EXIT = re.compile(r"exit code (\d+)", re.I)
# 유저 불만 (soft) — 한국어/영어
RE_SOFT = re.compile(
    r"아니야|아닌데|틀렸|틀린|별로|맘에\s?안|마음에\s?안|뭐야|이상해|왜\s?이래"
    r"|잘못(?:했|됐|된)|다시\s?해|hold on|that'?s wrong|not what",
    re.I,
)
STOP_TOK = {
    # 진짜 무의미한 연결어/셸 군더더기만. 행동동사(open·read·python·ssh)·exe·확장자는
    # *남김* — 그게 예방 trigger. 흔함은 recall의 IDF 가 query 시점에 깎음(stopword 아님).
    "the", "and", "for", "with", "run", "cd", "ls", "echo", "cat", "sudo",
    "import", "from", "print", "true", "false", "none", "null", "code",
    "exit", "file", "path", "x", "utf8", "out-null", "dev", "var", "tmp",
    "mnt", "etc", "bin", "usr", "opt",
    # 경로 세그먼트 노이즈(거의 모든 명령 등장 → 무가치)
    "users", "home", "temp", "appdata", "documents", "desktop", "drive",
    "ubuntu", "local", "programs", "project", "scripts",
    # heredoc 본문 잔여 식별자(trim_inline 후에도 가끔 누수)
    "eof", "base", "exp", "rows", "io.open", "json.load", "csv.dictreader",
}
# 명령 안 인라인 코드 경계 — 여기부터 뒤는 토큰화 안 함(본문 노이즈 차단)
RE_INLINE = re.compile(r"\s-c\s|\s-e\s|<<|\spython3?\s*-|\spy\s+-X\s+utf8\s+-")
# soft 오탐 가드 — 요약/시스템/caveat 라인은 유저 불만 아님
RE_SOFT_SKIP = re.compile(
    r"continued from a previous|this session|<system-reminder|caveat:|local-command|"
    r"compacted|summary below", re.I,
)


def sh(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def action_sig(tool, inp: dict) -> str:
    """행동 단위 join 키 — recall_hook 과 *동일 공식*(예외 무관).
    recall(카드 띄움) ↔ capture(실패) 를 이 키로 join → 약한/죽은카드 산출."""
    prim = ""
    for k in ("command", "file_path", "path"):
        v = inp.get(k) if isinstance(inp, dict) else None
        if isinstance(v, str) and v:
            prim = v
            break
    return hashlib.sha1((str(tool) + "|" + prim)[:200].lower().encode("utf-8")).hexdigest()[:12]


def trim_inline(cmd: str) -> str:
    """명령에서 인라인 스크립트 본문 잘라냄 → exe·플래그·파일만 남김."""
    m = RE_INLINE.search(cmd or "")
    return cmd[:m.start()] if m else (cmd or "")


RE_EXT = re.compile(r"\.[a-z0-9]{1,5}\b")


def fallback_ext(text: str):
    """trigger 비었을 때(인라인 본문에 모양 숨음) 확장자로 폴백.
    파일타입은 특이+저노이즈 → recall 발동 가능. 명령+에러excerpt 에서 추출."""
    out = []
    for m in RE_EXT.findall((text or "").lower()):
        if m not in out and m not in (".py", ".sh", ".txt"):   # 너무 흔한 건 제외
            out.append(m)
    return out[:4]


def mech_tokens(text: str, limit: int = 8):
    """명령/경로에서 의미 토큰 기계추출 — 플래그·서브명령·확장자·식별자."""
    out = []
    seen = set()
    for raw in re.split(r"[\s=,;:'\"`()|<>{}\[\]]+", text or ""):
        t = raw.strip().lower()
        if not t:
            continue
        # 확장자 보존(.bat .ps1) / 하이픈플래그 보존(-rf --force)
        if re.fullmatch(r"\.[a-z0-9]{1,5}", t) or re.fullmatch(r"-{1,2}[a-z][\w-]*", t):
            cand = t
        else:
            cand = re.sub(r"^[./\\~]+", "", t)            # 경로 prefix 제거
            cand = re.sub(r"[./\\].*$", "", cand) if "/" in cand or "\\" in cand else cand
            if re.fullmatch(r"[a-z0-9_.-]{3,}", cand) is None:
                continue
        if cand in STOP_TOK or cand in seen or len(cand) < 2:
            continue
        seen.add(cand)
        out.append(cand)
        if len(out) >= limit:
            break
    return out


def tool_input_text(inp: dict) -> str:
    if not isinstance(inp, dict):
        return ""
    return " ".join(
        str(inp.get(k, "")) for k in ("command", "file_path", "path", "description")
        if isinstance(inp.get(k), (str, int))
    )


def result_text(block) -> str:
    c = block.get("content")
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return str(c or "")


def scan(lines):
    """jsonl 라인들 → 이벤트 리스트. tool_use_id → 호출 매핑으로 실패 페어링."""
    calls = {}          # tool_use_id -> (name, input)
    events = []
    for o in lines:
        if o.get("type") != "assistant" and o.get("type") != "user":
            continue
        msg = o.get("message") or {}
        cont = msg.get("content")
        if not isinstance(cont, list):
            # 유저 평문(soft 스캔)
            if o.get("type") == "user" and isinstance(cont, str):
                _soft(cont, events)
            continue
        for b in cont:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "tool_use":
                calls[b.get("id")] = (b.get("name", "?"), b.get("input") or {})
            elif bt == "tool_result" and b.get("is_error"):
                _objective(b, calls, events)
            elif bt == "text" and o.get("type") == "user":
                _soft(b.get("text", ""), events)
    return events


def _objective(block, calls, events):
    txt = result_text(block)
    name, inp = calls.get(block.get("tool_use_id"), ("?", {}))
    env = bool(ENV_NOISE.search(txt))
    exc = RE_EXC.search(txt)
    exm = RE_EXIT.search(txt)
    sig_src = (name + "|" + tool_input_text(inp) + "|" + (exc.group(1) if exc else (exm.group(0) if exm else txt[:60]))).lower()
    # trigger 는 인라인 스크립트 본문 제외(exe·플래그·파일·확장자만)
    trig_text = " ".join(filter(None, (
        trim_inline(str(inp.get("command", ""))),
        str(inp.get("file_path", "")), str(inp.get("path", "")),
    )))
    trig = mech_tokens(trig_text)
    if not trig:                              # 인라인 스크립트 → 모양이 본문에 숨음, 확장자 폴백
        trig = fallback_ext(str(inp.get("command", "")) + " " + txt)
    # 예외클래스는 trigger 에 넣지 않음 — 미래 위험행동은 예외이름을 안 담음(예방 발동 X).
    # 예외는 error 필드(군집/제목/l2)에만. trigger = 행동모양(open·.jsonl·python·ssh).
    events.append({
        "sig": sh(sig_src),
        "asig": action_sig(name, inp),
        "kind": "env_fail" if env else "objective_fail",
        "tool": name,
        "trigger": trig[:8],
        "error": (exc.group(1) if exc else (exm.group(0) if exm else "")),
        "excerpt": re.sub(r"\s+", " ", txt)[:240],
    })


def _soft(text, events):
    if not text or not RE_SOFT.search(text) or RE_SOFT_SKIP.search(text):
        return
    m = RE_SOFT.search(text)
    events.append({
        "sig": sh("soft|" + text[:80]),
        "kind": "soft",
        "tool": "",
        "trigger": [],
        "error": "",
        "excerpt": re.sub(r"\s+", " ", text)[:200],
        "hit": m.group(0),
    })


# ---------- state / journal ----------
def load_state():
    try:
        return json.loads(io.open(STATE, encoding="utf-8").read())
    except Exception:
        return {}


def save_state(st):
    io.open(STATE, "w", encoding="utf-8", newline="\n").write(json.dumps(st, ensure_ascii=False))


def existing_sigs():
    sigs = {}
    if not JOURNAL.exists():
        return sigs
    for ln in io.open(JOURNAL, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            e = json.loads(ln)
            sigs[e.get("sig")] = e.get("count", 1)
        except Exception:
            pass
    return sigs


def _write_lines(path, fresh):
    """이벤트 리스트 → 파일 append(utf-8/LF, BOM 없음)."""
    with io.open(path, "a", encoding="utf-8", newline="\n") as f:
        for e in fresh:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def append_events(events, sid):
    """이벤트 → journal append (sig dedup, 신규만). sync-dir 설정시 미러도."""
    if not events:
        return
    seen = set(existing_sigs().keys())    # 이미 저널에 있는 sig(재발) — 신규만 append
    fresh = []
    for e in events:
        s = e["sig"]
        if s in seen:
            continue
        seen.add(s)                       # 같은 배치 내 중복까지 방지
        e["count"] = 1
        e["session"] = sid
        fresh.append(e)
    if fresh:
        _write_lines(JOURNAL, fresh)               # 메인(로컬, dedup 기준)
        if SYNC_JOURNAL is not None:               # 미러(공유FS) — fail-open, 절대 block X
            try:
                SYNC_JOURNAL.parent.mkdir(parents=True, exist_ok=True)
                _write_lines(SYNC_JOURNAL, fresh)
            except Exception as ex:
                sys.stderr.write("[lessonloop] sync-dir mirror failed: %s\n" % ex)


def hermes_event(payload):
    """Hermes post_tool_call 페이로드 → 이벤트(객관실패만). transcript 불필요.
    extra: result/status('ok'|'error'|'blocked')/error_type/error_message."""
    extra = payload.get("extra") or {}
    status = str(extra.get("status") or "").lower()
    if status != "error":                 # ok/blocked = 비교훈
        return None
    name = payload.get("tool_name") or "?"
    inp = payload.get("tool_input") or {}
    err = str(extra.get("error_type") or "")
    msg = str(extra.get("error_message") or extra.get("result") or "")
    if not err:                            # error_type 없으면 메시지에서 예외클래스 추출
        m = RE_EXC.search(msg)
        err = m.group(1) if m else ""
    env = bool(ENV_NOISE.search(msg))
    trig_text = " ".join(filter(None, (
        trim_inline(str(inp.get("command", ""))),
        str(inp.get("file_path", "")), str(inp.get("path", "")),
    )))
    trig = mech_tokens(trig_text) or fallback_ext(str(inp.get("command", "")) + " " + msg)
    sig_src = (name + "|" + tool_input_text(inp) + "|" + (err or msg[:60])).lower()
    return {
        "sig": sh(sig_src),
        "asig": action_sig(name, inp),
        "kind": "env_fail" if env else "objective_fail",
        "tool": name,
        "trigger": trig[:8],
        "error": err,
        "excerpt": re.sub(r"\s+", " ", msg)[:240],
    }


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    sid = payload.get("session_id") or "default"

    # 런타임 감지: Claude Code(Stop, transcript) vs Hermes(post_tool_call, 직접 페이로드)
    tp = payload.get("transcript_path") or ""
    if tp and os.path.exists(tp):
        # ---- Claude Code: transcript 증분 스캔 ----
        raw = io.open(tp, encoding="utf-8").read().splitlines()
        st = load_state()
        start = int(st.get(sid, 0))
        if start > len(raw):              # transcript 재작성(compaction/회전) → 오프셋 무효, 처음부터
            start = 0
        new = raw[start:]
        st[sid] = len(raw)
        parsed = []
        for ln in new:
            ln = ln.strip()
            if ln:
                try:
                    parsed.append(json.loads(ln))
                except Exception:
                    pass
        append_events(scan(parsed), sid)
        save_state(st)
    else:
        # ---- Hermes: post_tool_call 페이로드에서 단일 이벤트 ----
        ev = hermes_event(payload)
        if ev:
            append_events([ev], sid)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)          # 절대 block 안 함
