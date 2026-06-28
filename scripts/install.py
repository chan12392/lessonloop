#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install.py -- LessonLoop 설치 (Claude Code 에이전트에 훅 배선).

사용:
  python scripts/install.py --agent myagent              # 수집모드(기본): recall+capture+pref
  python scripts/install.py --agent myagent --mode full  # + SessionStart 자동사이클(auto-promote)
  python scripts/install.py --agent myagent --uninstall  # 제거
  python scripts/install.py --agent myagent --dry-run    # 변경 없이 계획 출력

자동 탐지: settings.json = ~/.claude/settings.json, python = 현재 인터프리터.
멱등(재실행해도 중복 X). settings.json 백업 후 수정.
경로는 이 스크립트 위치(__file__)에서 도출 → 머신별 마운트 경로 무관.
"""
import sys, os, json, shutil, argparse, time
from pathlib import Path

# 경로 진실원천
SCRIPTS = Path(__file__).resolve().parent
try:
    from paths import journal_dir, cards_dir
    import config_set
except ImportError:
    # 하위 호환: 단돈 실행 시 fallback
    ROOT = SCRIPTS.parent
    journal_dir = lambda: ROOT
    cards_dir = lambda: ROOT
    config_set = None

# event -> (matcher, script). 수집모드 = 이 3개(읽기·캡처, auto-promote 없음).
COLLECTION = {
    "PreToolUse": ("Bash|Write|Edit", "recall_hook.py"),
    "Stop": (None, "capture.py"),
    "UserPromptSubmit": (None, "pref_recall.py"),
}
FULL_EXTRA = {"SessionStart": (None, "cycle.py")}   # full = + 자동 카드화
OUR_SCRIPTS = {"recall_hook.py", "capture.py", "pref_recall.py", "cycle.py"}


def default_settings():
    return Path(os.path.expanduser("~")) / ".claude" / "settings.json"


def is_ours(cmd):
    c = cmd.replace("\\", "/")
    return any(s in c for s in OUR_SCRIPTS)


def fwd(p):
    """forward-slash 경로 -- shlex(posix)·JSON 에서 백슬래시 이스케이프 문제 회피."""
    return str(p).replace("\\", "/")


def hook_block(matcher, python, script, agent):
    cmd = f'"{fwd(python)}" -X utf8 "{fwd(SCRIPTS / script)}" --agent {agent}'
    # sync_dir 은 훅 커맨드에 박지 않음 — capture 가 lessonloop.json(sync_dir 키)에서 읽음.
    # → 재설치 없이 config_set.py --sync-dir 로 런타임 변경 가능.
    blk = {"hooks": [{"type": "command", "command": cmd, "timeout": 60000}]}
    if matcher:
        blk["matcher"] = matcher
    return blk


def strip_ours(hooks):
    """기존 LessonLoop 훅 *엔트리*만 제거(멱등·재설치). 같은 블록 내 사용자 훅은 보존."""
    for ev in list(hooks.keys()):
        new_blocks = []
        for b in hooks[ev]:
            sub = b.get("hooks", [])
            kept_sub = [h for h in sub if not is_ours(h.get("command", ""))]
            if not kept_sub:                  # 우리 훅만 있던 블록 → 전체 제거
                continue
            b["hooks"] = kept_sub
            new_blocks.append(b)
        if new_blocks:
            hooks[ev] = new_blocks
        else:
            del hooks[ev]


def hermes_config_path(override):
    """폴백 체인 -- 단일 AppData 하드코딩 금지."""
    if override:
        return Path(override)
    env = os.environ.get("HERMES_CONFIG")
    if env:
        return Path(env)
    cands = [Path(os.path.expanduser("~")) / ".hermes" / "config.yaml"]
    la = os.environ.get("LOCALAPPDATA")
    if la:
        cands.append(Path(la) / "hermes" / "config.yaml")
    cands.append(Path(os.path.expanduser("~")) / ".config" / "hermes" / "config.yaml")
    for c in cands:
        if c.exists():
            return c
    return cands[0]


def ensure_hermes_skills(text, skills_path):
    """config.yaml 에 skills.external_dirs 등록(이미 있으면 no-op).
    라인 삽입만 → 주석·기존 내용 보존(pyyaml 의존 X, 전체재작성 ❌)."""
    import re as _re
    fwd = skills_path.replace("\\", "/")
    if fwd in text:                        # 이미 등록
        return text, False
    lines = text.split("\n")
    si = next((i for i, ln in enumerate(lines) if _re.match(r"^skills:\s*$", ln)), None)
    if si is None:                         # skills 섹션 자체 없음 → 끝에 추가
        return "\n".join(lines + ["", "skills:", "  external_dirs:", f'    - "{fwd}"']), True
    ei = None
    for j in range(si + 1, len(lines)):
        if _re.match(r"^\S", lines[j]):    # 다음 최상위 키 → external_dirs 없음
            break
        if _re.match(r"^\s+external_dirs:\s*$", lines[j]):
            ei = j
            break
    if ei is None:                         # external_dirs 없음 → skills 밑에 생성
        lines.insert(si + 1, "  external_dirs:")
        lines.insert(si + 2, f'    - "{fwd}"')
    else:                                  # 있음 → 첫 항목으로 삽입
        lines.insert(ei + 1, f'    - "{fwd}"')
    return "\n".join(lines), True


def install_hermes(a):
    """Hermes 셸훅(config.yaml) 배선 -- 3훅:
      post_tool_call  → capture.py  (실패 저널)
      pre_tool_call   → recall_hook.py  (기술카드 block, SCORE_MIN=2.5)
      pre_llm_call    → pref_recall.py  (사용자선호 context 주입)
    YAML 전체재작성 ❌(주석 손실). 안전 자동적용 = 'hooks: {}' 또는 'LessonLoop-only hooks'.
    사용자 다른 훅 섞여있으면 자동생략 → 3훅 스니펫 수동 출력."""
    import re as _re
    python = a.python or sys.executable
    cfg = hermes_config_path(a.settings)
    cap = f'"{fwd(python)}" -X utf8 "{fwd(SCRIPTS / "capture.py")}" --agent {a.agent}'
    rec = f'"{fwd(python)}" -X utf8 "{fwd(SCRIPTS / "recall_hook.py")}" --agent {a.agent}'
    pref = f'"{fwd(python)}" -X utf8 "{fwd(SCRIPTS / "pref_recall.py")}"'
    # sync_dir 은 lessonloop.json 에서 읽음(main 에서 config_set.set_sync_dir 처리) -- 훅 커맨드에 안 박음.
    block = ("hooks:\n"
             "  post_tool_call:\n"
             f"    - command: '{cap}'\n"
             "  pre_tool_call:\n"
             f"    - command: '{rec}'\n"
             "  pre_llm_call:\n"
             f"    - command: '{pref}'")
    snippet = block + "\nhooks_auto_accept: true"
    ll_scripts = ("capture.py", "recall_hook.py", "pref_recall.py")

    if not cfg.exists():
        print(f"Hermes config 없음: {cfg}")
        print("Hermes 1회 실행해 config 생성 후 재시도, 또는 --settings 로 경로 지정.")
        print("\n수동 배선 블록(config.yaml 에 추가):\n" + snippet)
        return

    text = cfg.read_text(encoding="utf-8")
    bak = cfg.with_name(cfg.name + f".bak-lessonloop-{int(time.time())}")
    if a.dry_run:
        print(f"DRY RUN: Hermes config 검사만 수행 -- 쓰기/백업 없음: {cfg}")
    else:
        shutil.copy(cfg, bak)
        print(f"backup: {bak}")

    if a.uninstall:
        print("Hermes 제거: 백업 복원 또는 config.yaml hooks 블록에서 LessonLoop 3라인(capture/recall_hook/pref_recall) 삭제.")
        if a.dry_run:
            print("  DRY RUN: 실제 config.yaml 변경/백업 없음")
        else:
            print(f"  백업: {bak}")
        return

    # 적용 모드 결정
    mode = "manual"
    ll_block_match = None
    if _re.search(r"^hooks:\s*\{\s*\}\s*$", text, _re.M):
        mode = "empty"
    else:
        mm = _re.search(r"^hooks:\s*$", text, _re.M)   # hooks: (내용 있는 블록)
        if mm:
            start = mm.end()
            end = len(text)
            for seg in _re.finditer(r"^\S", text[start:], _re.M):   # 다음 최상위키(들여쓰기 없음)
                end = start + seg.start()
                break
            block_text = text[mm.start():end]
            cmds = _re.findall(r"command:\s*'?([^\n']+)", block_text)
            if cmds and all(any(s in c for s in ll_scripts) for c in cmds):
                mode, ll_block_match = "llonly", (mm.start(), end)

    if mode == "empty":
        text = _re.sub(r"^hooks:\s*\{\s*\}\s*$",
                       lambda mmtch: block.rstrip("\n"), text, count=1, flags=_re.M)
        auto = True
    elif mode == "llonly":
        s, e = ll_block_match
        text = text[:s] + block + ("\n" if not text[e:].startswith("\n") else "") + text[e:]
        auto = True
    else:
        auto = False

    if _re.search(r"^hooks_auto_accept:\s*.*$", text, _re.M):
        text = _re.sub(r"^hooks_auto_accept:\s*.*$", "hooks_auto_accept: true", text, count=1, flags=_re.M)
    elif auto:
        text += "\nhooks_auto_accept: true\n"

    # skills.external_dirs 자동 등록(/lessonloop-config 스킬 → Hermes 세션에서 사용)
    skills_path = fwd(SCRIPTS.parent / "skills")
    text, skill_added = ensure_hermes_skills(text, str(SCRIPTS.parent / "skills"))

    if auto:
        if a.dry_run:
            print(f"DRY RUN: Hermes 설치 가능 -- agent={a.agent}")
            print("  실제 config.yaml 쓰기/백업은 수행하지 않음")
        else:
            cfg.write_text(text, encoding="utf-8")
            print(f"Hermes 설치 완료 -- agent={a.agent}")
        print(f"  config: {cfg}")
        print(f"  hooks:  post_tool_call→capture / pre_tool_call→recall_hook(block) / pre_llm_call→pref_recall(inject)")
        if skill_added:
            print(f"  스킬:   skills.external_dirs += {skills_path} (/lessonloop-config 사용 가능)")
        print(f"  로그:   journal-{a.agent}.jsonl · recall_log-{a.agent}.jsonl (journal_dir={journal_dir()})")
        print( "  ⚠ hooks_auto_accept=true(비대화 실행 허용). 보안 검토 원하면 false + HERMES_ACCEPT_HOOKS=1.")
        print( "  ▶ Hermes 재시작 후 적용. 'hermes hooks test pre_tool_call --for-tool terminal' 로 발동검증.")
    else:
        print("기존 hooks 블록에 사용자 훅이 섞여있음 -- 자동병합 생략(안전). config.yaml hooks 에 수동 추가:")
        print("\n" + snippet)
        if skill_added:
            print(f"\n스킬도 수동 추가(config.yaml):\n  skills:\n    external_dirs:\n      - \"{skills_path}\"")


def main():
    ap = argparse.ArgumentParser(description="LessonLoop installer (Claude Code / Hermes)")
    ap.add_argument("--agent", required=True, help="에이전트 식별자 (로그 네임스페이스)")
    ap.add_argument("--settings", default=None, help="settings.json 경로 (기본 ~/.claude/settings.json)")
    ap.add_argument("--python", default=None, help="훅 실행 python (기본 현재 인터프리터)")
    ap.add_argument("--sync-dir", default=None,
                    help="journal 미러 경로(공유FS 동기화 폴더 -- 다른 머신 수집기가 읽음). "
                         "로컬 설치(AppData 등)서 클라우드 동기화 폴더 등으로 journal 흘려보낼 때.")
    ap.add_argument("--journal-dir", default=None, help="journal 경로 (기본 스크립트 옆)")
    ap.add_argument("--cards-dir", default=None, help="cards/lessons_index 경로 (기본 스크립트 옆)")
    ap.add_argument("--mode", choices=["collection", "full"], default="collection")
    ap.add_argument("--runtime", choices=["claude-code", "hermes"], default="claude-code",
                    help="대상 런타임 (claude-code=settings.json 훅 / hermes=config.yaml 훅)")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 설치/제거 계획만 출력")
    a = ap.parse_args()

    # 경로 설정 (lessonloop.json) — CC·Hermes 공통. 같은 폴더 재설치 시 기존 값 자동 상속.
    if config_set:
        if a.dry_run:
            if a.journal_dir:
                print(f"DRY RUN: journal_dir 설정 예정: {a.journal_dir}")
            if a.cards_dir:
                print(f"DRY RUN: cards_dir 설정 예정: {a.cards_dir}")
            if a.sync_dir:
                print(f"DRY RUN: sync_dir 설정 예정: {a.sync_dir}")
        else:
            if a.journal_dir:
                config_set.set_journal_dir(a.journal_dir)
                print(f"journal_dir 설정: {a.journal_dir}")
            if a.cards_dir:
                config_set.set_cards_dir(a.cards_dir)
                print(f"cards_dir 설정: {a.cards_dir}")
            if a.sync_dir:
                config_set.set_sync_dir(a.sync_dir)
                print(f"sync_dir 설정: {a.sync_dir} (lessonloop.json — 재설치 없이 변경 가능)")
        # 같은 폴더에 다른 에이전트가 이미 깔려 경로 잡혀있으면 자동 상속 안내
        ejd, ecd, esd = config_set.get_journal_dir(), config_set.get_cards_dir(), config_set.get_sync_dir()
        if (ejd or ecd or esd) and not (a.journal_dir or a.cards_dir or a.sync_dir):
            print(f"  기존 lessonloop.json 경로 상속: journal_dir={ejd or '(기본)'} "
                  f"cards_dir={ecd or '(기본)'} sync_dir={esd or '(없음)'}")

    if a.runtime == "hermes":
        install_hermes(a)
        return

    settings = Path(a.settings) if a.settings else default_settings()
    python = a.python or sys.executable
    if not Path(python).exists():
        print(f"  WARN: python 경로 없음: {python} — Windows Store stub(exit 49) 가능. "
              f"--python 으로 실제 인터프리터(예: py 런처 경로) 지정 권장. 훅이 fail-open 될 수 있음.")

    if not a.dry_run:
        settings.parent.mkdir(parents=True, exist_ok=True)
    d = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    d.setdefault("hooks", {})

    if settings.exists():
        bak = settings.with_name(settings.name + f".bak-lessonloop-{int(time.time())}")
        if a.dry_run:
            print(f"DRY RUN: Claude Code settings 검사만 수행 -- 쓰기/백업 없음: {settings}")
        else:
            shutil.copy(settings, bak)
            print(f"backup: {bak}")

    strip_ours(d["hooks"])      # 멱등: 기존 우리 훅 제거 후 재배선

    if a.uninstall:
        if a.dry_run:
            print(f"DRY RUN: 제거 가능 (agent={a.agent}). LessonLoop 훅 삭제 예정, 실제 쓰기 없음.")
        else:
            settings.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"제거 완료 (agent={a.agent}). LessonLoop 훅 모두 삭제.")
        return

    spec = dict(COLLECTION)
    if a.mode == "full":
        spec.update(FULL_EXTRA)
    for ev, (matcher, script) in spec.items():
        if not (SCRIPTS / script).exists():
            print(f"  WARN: {script} 없음 -- 스킵"); continue
        d["hooks"].setdefault(ev, []).append(hook_block(matcher, python, script, a.agent))

    if a.dry_run:
        print(f"DRY RUN: Claude Code 설치 가능 -- agent={a.agent}, mode={a.mode}")
        print("  실제 settings.json/skill 파일 쓰기 없음")
    else:
        settings.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"설치 완료 -- agent={a.agent}, mode={a.mode}")
    print(f"  settings: {settings}")
    print(f"  python:   {python}")
    print(f"  배선 훅:  {', '.join(spec.keys())}")
    print(f"  로그:     journal-{a.agent}.jsonl · recall_log-{a.agent}.jsonl (cards_dir={cards_dir()})")

    # 스킬 배치 (경로 설정용)
    skills_dir = settings.parent / "skills"
    skill_src = SCRIPTS.parent / "skills" / "lessonloop-config.md"
    skill_dst = skills_dir / "lessonloop-config.md"
    if skill_src.exists():
        if a.dry_run:
            print(f"  DRY RUN 스킬: {skill_src} -> {skill_dst}")
        else:
            skills_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(skill_src, skill_dst)
            print(f"  스킬:     /lessonloop-config (경로 설정)")

    if a.mode == "collection":
        print("  ※ 수집모드: auto-promote 없음(raw 신호만). 큐레이션은 나중에 `--mode full` 또는 수동.")


if __name__ == "__main__":
    main()
