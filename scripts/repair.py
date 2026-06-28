#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repair.py — LessonLoop 약한카드(재발) 정비. **기본 = 구동 중인 에이전트(LLM) self-witness**.

FEEDBACK 가 잡은 약한카드(recall 이 떴는데 같은 행동이 또 실패)의 rule/fix 를
**API 키 없이** 정비한다 — 쓰고 있는 에이전트 본신이 한다.

두 모드:
  기본(무인자)  → `staging/repair-tasks-<agent>.md` 출력.
                  recall_log↔journal 기계 join 으로 각 약한카드의 재발 증거(excerpt)를 모아
                  한 파일에 정리 + RULE_SPEC.md 포인터. 구동 에이전트가 이 파일을 읽고
                  RULE_SPEC(§0 A/B/C 판정)에 따라 판단·재작성(cards/<slug>.md 직접 편집).
                  **키 불필요. 일반 사용자 기본 경로.**
  --api (옵션)  → provider 자동재작성(OPENAI-compat[z.ai/glm 호환] / ANTHROPIC).
                  키 있을 때만. 결과는 staging/<slug>.repair.md (검토게이트).

왜 기본이 self-witness 인가: repair 판정(A=rule부실 / B=trigger-overlap / C=이미수정됨)은
이해가 필요 → LLM 본인이 해야. 키-연결 스크립트는 이 판정을 못 함. RULE_SPEC.md 가
낮은 등급 모델도 품질 내도록 처방.

cp949 콘솔(Win) 한글/em-dash 깨징 방지로 stdout utf-8 재구성(file-encoding-bom-newline).
"""
import sys, os, io, re, json, urllib.request, urllib.error
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "staging"
CARDS = ROOT / "cards"
try:
    from paths import journal as journal_path, recall_log as recall_log_path
except ImportError:
    journal_path = lambda a: ROOT / f"journal-{a}.jsonl"
    recall_log_path = lambda a: ROOT / f"recall_log-{a}.jsonl"

MODEL_OPENAI = os.environ.get("LESSONLOOP_REPAIR_MODEL_OPENAI", "gpt-4o-mini")
MODEL_ANTHROPIC = os.environ.get("LESSONLOOP_REPAIR_MODEL_ANTHROPIC", "claude-haiku-4-5-20251001")


def _agent():
    for i, a in enumerate(sys.argv):
        if a == "--agent" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--agent="):
            return a.split("=", 1)[1]
    return os.environ.get("LESSONLOOP_AGENT", "default")


def _load_jsonl(path):
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


def _evidence(agent):
    """recall_log(asig→fired slugs) × journal(asig→objective_fail excerpts) join.
    → {slug: [failure excerpts]} = 각 카드가 발화한 asig 들이 *또 실패한* 증거."""
    fired_by_asig = {}
    for f in _load_jsonl(recall_log_path(agent)):
        asig = f.get("asig")
        if asig:
            fired_by_asig.setdefault(asig, set()).update(f.get("fired") or [])
    fail_excerpt = {}
    for e in _load_jsonl(journal_path(agent)):
        if e.get("kind") == "objective_fail" and e.get("asig"):
            fail_excerpt.setdefault(e["asig"], []).append(e.get("excerpt", ""))
    ev = {}
    for asig, slugs in fired_by_asig.items():
        if asig in fail_excerpt:
            for s in slugs:
                ev.setdefault(s, []).extend(fail_excerpt[asig])
    return ev


def emit_tasks(agent, weak, evidence):
    """구동 에이전트용 self-witness task 파일 출력 — 재발 증거 + RULE_SPEC 포인터."""
    STAGING.mkdir(exist_ok=True)
    out = STAGING / f"repair-tasks-{agent}.md"
    lines = [
        "# Repair tasks — agent %s" % agent,
        "",
        "<!-- self-witness: RULE_SPEC.md §0 먼저 읽고 각 카드 A(rule부실)/B(trigger-overlap)/"
        "C(이미수정됨) 판정. A만 재작성. B/C는 repair_verdict 표시만. cards/<slug>.md 직접 편집 후 "
        "build_index.py 실행. -->",
        "",
        "약한카드 %d개. 재발 증거(excerpt)로 판정 근거 삼을 것." % len(weak),
        "",
    ]
    for w in weak:
        slug = w["slug"]
        recur = w.get("recur_count", 0)
        cpath = CARDS / f"{slug}.md"
        rule = "(카드 파일 없음)" if not cpath.exists() else parse_card(cpath.read_text(encoding="utf-8"))[0].get("rule", "")
        exs = evidence.get(slug, [])
        lines.append("## %s  (recur=%d)" % (slug, recur))
        lines.append("- **현재 rule**: %s" % rule)
        if exs:
            lines.append("- **재발 증거** (이 카드가 발화한 행동이 또 실패):")
            for ex in exs[:3]:
                lines.append("  - %s" % (ex[:160].replace("\n", " ")))
        else:
            lines.append("- **재발 증거**: (excerpt 없음 — trigger-overlap 가능성)")
        lines.append("- → RULE_SPEC §0 판정 → A면 rule 재작성 / B·C면 `repair_verdict:` 표시")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ---------- 옵션: --api 자동재작성 ----------
def build_prompt(fm, body, recur):
    return (
        "아래는 AI 에이전트가 반복적으로 실패한 교훈 카드다. recall 경고가 떴는데도 "
        f"같은 행동이 {recur}회 재발했다. RULE_SPEC.md §0~4 에 따라 판정·재작성하라.\n\n"
        f"[trigger] {fm.get('trigger','')}\n[현재 rule] {fm.get('rule','')}\n\n[본문]\n{body.strip()}\n\n"
        "과제: 재발을 막도록 rule 을 '행동 직전 확인' 형태 1~2문장으로 재작성."
        " 단, 실패 원인이 rule 과 무관하면(trigger-overlap) rule 말고 {\"verdict\":\"trigger-overlap\","
        " \"reason\":\"...\"} 만 출력. 같으면 JSON: {\"rule\":\"...\",\"fix\":\"...\",\"check\":\"...\"}"
    )


def call_openai(prompt, model, api_key, base_url):
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({"model": model, "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"}}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"]


def call_anthropic(prompt, model, api_key, base_url):
    url = base_url.rstrip("/") + "/messages"
    body = json.dumps({"model": model, "max_tokens": 600, "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))["content"][0]["text"]


def pick_provider():
    k = os.environ.get("OPENAI_API_KEY")
    if k:
        return ("openai", {"api_key": k, "base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"), "model": MODEL_OPENAI})
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return ("anthropic", {"api_key": k, "base_url": os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"), "model": MODEL_ANTHROPIC})
    return None


CALLERS = {"openai": call_openai, "anthropic": call_anthropic}


def _api_rewrite(weak, evidence):
    """--api 옵션: provider 자동재작성 → staging/<slug>.repair.md (검토게이트)."""
    prov = pick_provider()
    if not prov:
        print("repair --api: LLM 키 없음(OPENAI_API_KEY/ANTHROPIC_API_KEY). 기본 self-witness 모드 사용.")
        return 0
    caller = CALLERS[prov[0]]
    n = 0
    for w in weak:
        slug, recur = w["slug"], w.get("recur_count", 0)
        cpath = CARDS / f"{slug}.md"
        if not cpath.exists():
            continue
        fm, body = parse_card(cpath.read_text(encoding="utf-8"))
        try:
            raw = caller(build_prompt(fm, body, recur), **prov[1])
            proposed = json.loads(raw)
        except Exception as ex:
            sys.stderr.write("  [repair --api] %s 실패(safe-skip): %s\n" % (slug, ex))
            continue
        (STAGING / f"{slug}.repair.md").write_text(
            "---\nslug: %s\nrepair_of: canonical\nrecur_count: %s\nproposed_by: %s\n---\n\n"
            "## proposed_rule\n%s\n\n## proposed_fix\n%s\n\n## proposed_check\n%s\n"
            % (slug, recur, prov[0], proposed.get("rule", ""), proposed.get("fix", ""), proposed.get("check", "")),
            encoding="utf-8")
        print("  → %s 재작성안 staging/%s.repair.md" % (slug, slug))
        n += 1
    return n


def main():
    use_api = "--api" in sys.argv
    agent = _agent()
    state_path = ROOT / f".feedback_state-{agent}.json"
    if not state_path.exists():
        print("repair: .feedback_state-%s.json 없음 — feedback.py 먼저 실행." % agent)
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

    # 기본: 구동 에이전트용 task 출력(키 불필요)
    evidence = _evidence(agent)
    out = emit_tasks(agent, weak, evidence)
    print("repair: 약한카드 %d개 → %s" % (len(weak), out.relative_to(ROOT)))
    print("  구동 에이전트가 RULE_SPEC.md §0(A/B/C) 판정 후 cards/ 직접 재작성(self-witness).")
    if use_api:
        n = _api_rewrite(weak, evidence)
        print("repair --api: %d/%d 자동재작성안 staging 출력." % (n, len(weak)))


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        sys.stderr.write("[lessonloop] repair fail-open: %s\n" % ex)
    sys.exit(0)
