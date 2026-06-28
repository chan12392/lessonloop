#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repair.py — LessonLoop 약한카드(재발) LLM 재작성 seam (옵션, 기본 OFF).

FEEDBACK 가 잡은 **약한카드**(recall 이 떴는데 같은 행동이 또 실패 = rule 이 불충분)의
rule/fix 를 LLM 이 재작성 → `staging/<slug>.repair.md` (검토 게이트, canonical 덮어쓰기❌).

왜 옵션인가(카드 minimal-means-before-new-infra): 제로인프라 코어는 recall 에스컬레이션
(recall_hook, 결정적·키불필요)으로 충분. rule *내용* 정제는 이해가 필요 → LLM 플러그인.
키 없으면 graceful 스킵(지어내기 금지). 에이전트/사람이 직접 고쳐도 됨(self-witness).

provider 자동감지(OpenAI-compatible 우선):
  ANTHROPIC_API_KEY[+ANTHROPIC_BASE_URL] → /v1/messages
  OPENAI_API_KEY [+OPENAI_BASE_URL]      → /v1/chat/completions  (z.ai/glm 호환)
  둘 다 없음                              → 스킵 + 안내, exit 0

⚠ 이 스크립트의 LLM 호출부는 **API 키 있어야 라이브 검증 가능**.
  --show-prompt 로 프롬프트 생성·카드파싱은 키 없이 검증됨.

사용:
  py scripts/repair.py --agent baekho --show-prompt   # dry-run(프롬프트만 출력)
  py scripts/repair.py --agent baekho                 # 키 있으면 호출→staging
"""
import sys, os, io, re, json, urllib.request, urllib.error
from pathlib import Path

# cp949 콘솔(Win)에서 em-dash/한글 깨짐 방지 — 기본 인코딩 가정 금지(file-encoding-bom-newline).
# 호출자가 -X utf8 주면 no-op, 아니면 utf-8 강제.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "staging"
CARDS = ROOT / "cards"

MODEL_OPENAI = os.environ.get("LESSONLOOP_REPAIR_MODEL_OPENAI", "gpt-4o-mini")
MODEL_ANTHROPIC = os.environ.get("LESSONLOOP_REPAIR_MODEL_ANTHROPIC", "claude-haiku-4-5-20251001")


def _agent():
    for i, a in enumerate(sys.argv):
        if a == "--agent" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--agent="):
            return a.split("=", 1)[1]
    return os.environ.get("LESSONLOOP_AGENT", "default")


def parse_card(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, ""
    fm = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"^([a-z0-9_]+):\s*(.*)$", line)
        if mm:
            v = mm.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            fm[mm.group(1)] = v
    return fm, m.group(2)


def build_prompt(fm, body, recur):
    """약한카드 재작성 프롬프트 — 현재 rule/fix + 재발데이터 주고 더 날카로운 rule 요청."""
    return (
        "아래는 AI 에이전트가 반복적으로 실패한 교훈 카드다. recall 경고가 떴는데도 "
        f"같은 행동이 {recur}회 재발했다 = 현재 rule이 행동을 막기에 불충분.\n\n"
        f"[trigger] {fm.get('trigger','')}\n"
        f"[현재 rule] {fm.get('rule','')}\n"
        f"[enforce] {fm.get('enforce','')}  [severity] {fm.get('severity','')}\n\n"
        f"[본문]\n{body.strip()}\n\n"
        "과제: 이 재발을 막도록 rule을 더 구체적·실행가능하게 재작성하라.\n"
        "- 행동 '직전'에 확인할 수 있는 구체적 조건/체크를 포함 (추상 금지)\n"
        "- 1~2문장. 원인 단언이 아닌 '하기 전에 X 확인' 형태\n"
        "출력은 JSON 한 객체만: {\"rule\": \"...\", \"fix\": \"...\", \"check\": \"...\"}\n"
        "fix/check 는 짧게. 본문 사실 위반/과장 금지."
    )


def call_openai(prompt, model, api_key, base_url):
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model, "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": "Bearer " + api_key, "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def call_anthropic(prompt, model, api_key, base_url):
    url = base_url.rstrip("/") + "/messages"
    body = json.dumps({
        "model": model, "max_tokens": 600, "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["content"][0]["text"]


def pick_provider():
    """(caller, kwargs) or None. OpenAI-compatible 우선(z.ai/glm 호환 많음)."""
    k = os.environ.get("OPENAI_API_KEY")
    if k:
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        return ("openai", {"api_key": k, "base_url": base, "model": MODEL_OPENAI})
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
        return ("anthropic", {"api_key": k, "base_url": base, "model": MODEL_ANTHROPIC})
    return None


CALLERS = {"openai": call_openai, "anthropic": call_anthropic}


def main():
    show = "--show-prompt" in sys.argv
    agent = _agent()
    state_path = ROOT / f".feedback_state-{agent}.json"
    if not state_path.exists():
        print("repair: .feedback_state-%s.json 없음 — 먼저 feedback.py 실행." % agent)
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        print("repair: state 파일 깨짐 — 스킵.")
        return
    weak = state.get("weak") or []
    if not weak:
        print("repair: 약한카드 없음 — 정비 대상 없음.")
        return

    prov = pick_provider()
    if not prov and not show:
        print("repair: LLM API 키 없음(OPENAI_API_KEY 또는 ANTHROPIC_API_KEY).")
        print("  --show-prompt 로 프롬프트 확인 가능. 키 설정 시 staging/ 에 재작성안 출력.")
        return

    caller = None
    if prov:
        caller = CALLERS[prov[0]]
    STAGING.mkdir(exist_ok=True)
    n = 0
    for w in weak:
        slug = w["slug"]
        recur = w.get("recur_count", 0)
        cpath = CARDS / f"{slug}.md"
        if not cpath.exists():
            print("  skip %s — 카드 파일 없음" % slug)
            continue
        fm, body = parse_card(cpath.read_text(encoding="utf-8"))
        prompt = build_prompt(fm, body, recur)
        if show:
            print("=== PROMPT [%s] recur=%d ===" % (slug, recur))
            print(prompt)
            print()
            continue
        try:
            raw = caller(prompt, **prov[1])
            proposed = json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, KeyError, Exception) as ex:
            sys.stderr.write("  [repair] %s LLM 호출 실패(safe-skip): %s\n" % (slug, ex))
            continue
        # 검토 게이트 — canonical 덮어쓰기❌, staging 에 제안 저장
        out = STAGING / f"{slug}.repair.md"
        out.write_text(
            "---\nslug: %s\nrepair_of: canonical\nrecur_count: %s\nproposed_by: %s\n---\n\n"
            "## proposed_rule\n%s\n\n## proposed_fix\n%s\n\n## proposed_check\n%s\n"
            % (slug, recur, prov[0], proposed.get("rule", ""), proposed.get("fix", ""),
               proposed.get("check", "")), encoding="utf-8")
        print("  → %s 재작성안 staging/%s.repair.md (검토 후 promote)" % (slug, slug))
        n += 1
    if not show:
        print("repair: %d/%d 카드 재작성안 staging 출력. 사람/에이전트 검토 → promote." % (n, len(weak)))


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        sys.stderr.write("[lessonloop] repair fail-open: %s\n" % ex)
    sys.exit(0)
