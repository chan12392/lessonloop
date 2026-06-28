#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""promote.py — LessonLoop PROMOTE 단계 (순수 스크립트, lockfile 직렬화).

staging/*.md candidate → validate → exact-hash dedup → trigger-Jaccard cluster
  → 신규: cards/ 로 canonical move + reindex
  → 중복(동일 rule-hash): 기존 카드 sources +1, candidate 삭제
  → near-dup(Jaccard >= CLUSTER_MIN): 그래도 promote 하되 COMPACT 후보로 리포트
  → validate 실패: staging 에 남기고 _reject 사유 기록

모델 토큰 0. cron 으로 돌려도 lockfile 로 동시성 안전.

규칙(DESIGN §3[2] validate):
  스키마 + 길이 + literal trigger + PII/날짜/1인칭/에이전트명 금지.
"""
import sys, os, io, re, csv, hashlib, subprocess, time
from pathlib import Path

# 경로 진실원천
SCRIPTS = Path(__file__).resolve().parent
try:
    from paths import cards_dir, cards_dir_cards, ROOT
except ImportError:
    # 하위 호환: 단돈 실행 시 fallback
    ROOT = SCRIPTS.parent
    cards_dir = lambda: ROOT
    cards_dir_cards = lambda: ROOT / "cards"

CARDS = cards_dir_cards()
STAGING = cards_dir() / "staging"
LOCK = cards_dir() / ".promote.lock"
AUTO = "--auto" in sys.argv      # SessionStart 자동: ready 만 승격(lint_weak/미트리아지 보류)

L1_OK = {"기술", "에이전트행동", "사용자선호"}
ENFORCE_OK = {"lint", "hook", "guard", "manual"}
SEVERITY_OK = {"low", "medium", "high", "critical"}
REQUIRED = ("l1", "l2", "trigger", "rule", "enforce", "severity")

RULE_MAX = 240          # 한 줄 명령형 — 과길이 = 증류 실패 신호
TRIGGER_MIN_TOKENS = 1
CLUSTER_MIN = 0.5       # trigger-Jaccard >= 이 값 = near-dup (COMPACT 후보)

# PII/맥락 누수 금지 패턴 (추출 시 제거됐어야 함 = 방어선)
RE_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\d{4}년|\d{1,2}월\s?\d{1,2}일")
RE_FIRST_PERSON = re.compile(r"(?<![가-힣])(나는|내가|저는|제가|우리는|우리가)(?![가-힣])|(?<![A-Za-z])(I|I'm|I am|we|my)(?![A-Za-z])")
# 에이전트/봇 고유명 (OSS 공개 시 누수 차단). 소문자 비교.
BANNED_NAMES = {"myagent", "assistant"}  # add your own agent/bot names to scrub (OSS leak guard)


# ---------- frontmatter ----------
def parse_front(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return None, None
    fm = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"^([a-z0-9_]+):\s*(.*)$", line)
        if not mm:
            continue
        k, v = mm.group(1), mm.group(2).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        fm[k] = v
    return fm, m.group(2)


def trigger_tokens(trig: str):
    return {t.lower() for t in re.split(r"\s+", trig.replace('"', "").strip()) if t}


# ---------- validate ----------
def validate(fm: dict, body: str):
    errs = []
    for k in REQUIRED:
        if not fm.get(k, "").strip():
            errs.append(f"필수필드 누락: {k}")
    if fm.get("l1") and fm["l1"] not in L1_OK:
        errs.append(f"l1 부정: {fm['l1']} (허용 {L1_OK})")
    if fm.get("enforce") and fm["enforce"] not in ENFORCE_OK:
        errs.append(f"enforce 부정: {fm['enforce']}")
    if fm.get("severity") and fm["severity"] not in SEVERITY_OK:
        errs.append(f"severity 부정: {fm['severity']}")
    rule = fm.get("rule", "")
    if len(rule) > RULE_MAX:
        errs.append(f"rule 과길이 {len(rule)}>{RULE_MAX} (증류 실패)")
    if "\n" in rule:
        errs.append("rule 은 한 줄이어야 함")
    if len(trigger_tokens(fm.get("trigger", ""))) < TRIGGER_MIN_TOKENS:
        errs.append("trigger 토큰 없음")
    # PII/맥락 — rule+trigger 만 검사(body facts 는 기술 literal 허용)
    scan = f"{rule}\n{fm.get('trigger','')}"
    if RE_DATE.search(scan):
        errs.append("날짜 포함 (rule/trigger 에서 제거)")
    if RE_FIRST_PERSON.search(scan):
        errs.append("1인칭 포함 (명령형 2인칭으로)")
    low = scan.lower()
    hit = [n for n in BANNED_NAMES if n in low]
    if hit:
        errs.append(f"고유명/봇명 포함: {hit}")
    return errs


# ---------- canonicalize ----------
def make_id(rule: str) -> str:
    return hashlib.sha1(rule.encode("utf-8")).hexdigest()[:12]


def load_canonical():
    """기존 카드: id -> path, 그리고 trigger 토큰셋(클러스터용).

    dedup 키 = make_id(rule) 재계산. frontmatter id 필드를 *믿지 않음* —
    id가 sha1(rule)에서 드리프트해도(수동편집·구버전) dedup 안 깨지게.
    """
    by_id, trig_map = {}, {}
    for p in CARDS.glob("*.md"):
        fm, _ = parse_front(io.open(p, encoding="utf-8").read())
        if not fm:
            continue
        rule = fm.get("rule", "").strip()
        if rule:
            by_id[make_id(rule)] = p
        trig_map[p.stem] = trigger_tokens(fm.get("trigger", ""))
    return by_id, trig_map


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def bump_sources(path: Path, add: int = 1):
    text = io.open(path, encoding="utf-8").read()
    m = re.search(r"^sources:\s*(\d+)\s*$", text, re.M)
    cur = int(m.group(1)) if m else 0
    new = cur + add
    if m:
        text = re.sub(r"^sources:\s*\d+\s*$", f"sources: {new}", text, count=1, flags=re.M)
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)
    return new


def emit_canonical(fm: dict, body: str, cid: str, slug: str) -> Path:
    fm = dict(fm)
    fm["id"] = cid
    fm["status"] = "canonical"
    fm.setdefault("sources", "1")
    fm.setdefault("stale_if", "")
    fm.pop("provisional", None)
    fm.pop("writer_model", None)
    fm.pop("slug", None)
    order = ["id", "l1", "l2", "trigger", "rule", "enforce", "severity", "sources", "status", "stale_if"]
    lines = ["---"]
    for k in order:
        v = fm.get(k, "")
        if k == "trigger":          # trigger 는 항상 따옴표(공백 literal 보존)
            v = '"' + str(v).replace('"', "") + '"'
        lines.append(f"{k}: {v}")
    lines.append("---")
    out = "\n".join(lines) + "\n" + (body if body.startswith("\n") else "\n" + body)
    path = CARDS / f"{slug}.md"
    io.open(path, "w", encoding="utf-8", newline="\n").write(out.rstrip() + "\n")
    return path


def slug_for(fm: dict, p: Path) -> str:
    s = (fm.get("slug") or "").strip()
    if not s:
        s = p.stem
    # id-형 파일명(12 hex)이면 rule 에서 파생
    if re.fullmatch(r"[0-9a-f]{12}", s):
        base = re.sub(r"[^a-z0-9가-힣]+", "-", fm.get("rule", "card").lower()).strip("-")
        s = "-".join(base.split("-")[:6]) or "card"
    return s


# ---------- main ----------
def acquire_lock():
    try:
        fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        # stale lock(>10분) 자동 회수
        try:
            if time.time() - LOCK.stat().st_mtime > 600:
                LOCK.unlink()
                return acquire_lock()
        except OSError:
            pass
        return False


def main():
    STAGING.mkdir(exist_ok=True)
    cands = sorted(STAGING.glob("*.md"))
    if not cands:
        print("staging 비어있음 — 할 일 없음.")
        return
    if not acquire_lock():
        print("다른 promote 실행 중 (lock). 종료.")
        return
    try:
        by_id, trig_map = load_canonical()
        promoted, deduped, rejected, held, clusters = 0, 0, 0, 0, []
        for p in cands:
            fm, body = parse_front(io.open(p, encoding="utf-8").read())
            if fm is None:
                print(f"  REJECT {p.name}: frontmatter 없음"); rejected += 1; continue
            tv = fm.get("triage", "")
            # needs_human 은 항상 보류. --auto(SessionStart 자동) 는 ready(특이주제 고신뢰)만 승격,
            #   lint_weak/미트리아지는 수동 검토 대기 → 자동 노이즈 유입 차단.
            if tv == "needs_human" or (AUTO and tv != "ready"):
                why = "needs_human" if tv == "needs_human" else f"--auto: ready 아님({tv or '미트리아지'})"
                print(f"  HOLD   {p.name}: {why}"); held += 1; continue
            errs = validate(fm, body or "")
            if errs:
                print(f"  REJECT {p.name}: {'; '.join(errs)}")
                note = "\n".join(f"_reject: {e}" for e in errs)
                if "_reject:" not in (body or ""):
                    io.open(p, "a", encoding="utf-8", newline="\n").write(f"\n<!-- {note} -->\n")
                rejected += 1
                continue
            cid = make_id(fm["rule"])
            if cid in by_id:                       # exact rule dup
                try:                               # staging sources 값 비정상 → 기본1
                    add = int(fm.get("sources", "1") or 1)
                except (TypeError, ValueError):
                    add = 1
                n = bump_sources(by_id[cid], add)
                p.unlink()
                print(f"  DEDUP  {p.name} -> {by_id[cid].name} (sources={n})")
                deduped += 1
                continue
            slug = slug_for(fm, p)
            ctoks = trigger_tokens(fm["trigger"])
            near = max(((jaccard(ctoks, t), s) for s, t in trig_map.items()), default=(0.0, ""))
            out = emit_canonical(fm, body or "", cid, slug)
            by_id[cid] = out
            trig_map[slug] = ctoks
            p.unlink()
            promoted += 1
            tag = ""
            if near[0] >= CLUSTER_MIN:
                clusters.append((slug, near[1], round(near[0], 2)))
                tag = f"  [클러스터 후보 ~{near[1]} j={near[0]:.2f}]"
            print(f"  PROMOTE {p.name} -> {out.name} (id={cid}){tag}")

        print(f"\npromoted={promoted} deduped={deduped} rejected={rejected} held={held}")
        if clusters:
            print("COMPACT 후보 (near-dup, 수동/플러그인 융합 검토):")
            for s, near, j in clusters:
                print(f"  - {s} ~ {near} (Jaccard {j})")
        if promoted or deduped:
            print("\nreindex...")
            subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "build_index.py")], check=False)
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
