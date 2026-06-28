#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quick_install.py — LessonLoop 원라인 설치.

GitHub에서 tarball 받아 압축 해제 후 install.py 실행.
zip 없이 Python만 있으면 됨.

사용:
  curl -fsSL https://raw.githubusercontent.com/chan12392/lessonloop/main/scripts/quick_install.py | python - --agent myagent
  또는
  python - <(curl -fsSL https://raw.githubusercontent.com/chan12392/lessonloop/main/scripts/quick_install.py) --agent myagent

Windows PowerShell:
  iex "& { (iwr https://raw.githubusercontent.com/chan12392/lessonloop/main/scripts/quick_install.py).Content } --agent myagent"

에이전트 이름 자동 감지 (순서):
  1. --agent 인자 (명시적 지정)
  2. env LESSONLOOP_AGENT
  3. Hermes: config.yaml agent_name (존재하면)
  4. Claude Code: .claude/session_id (hostname-기반 fallback)
  5. 최후: hostname

--dry-run 을 주면 tarball 검증과 install.py 계획 출력만 하고 실제 훅/파일은 쓰지 않음.
"""
from __future__ import annotations
import sys, os, tempfile, tarfile, shutil, subprocess, argparse, socket
from pathlib import Path
from urllib.request import urlopen, Request

REPO = "chan12392/lessonloop"
BRANCH = "main"
TARBALL_URL = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.tar.gz"


def auto_detect_agent() -> str | None:
    """환경/설정에서 에이전트 이름 자동 감지."""
    # 1. env
    if agent := os.environ.get("LESSONLOOP_AGENT"):
        return agent

    # 2. Hermes config.yaml
    hermes_cfg = _hermes_config_path()
    if hermes_cfg and hermes_cfg.exists():
        try:
            import yaml
            cfg = yaml.safe_load(hermes_cfg.read_text(encoding="utf-8"))
            if name := cfg.get("agent_name"):
                return name
        except Exception:
            pass

    # 3. Claude Code session (데스크톱 앱)
    session_dir = Path.home() / ".claude" / "sessions"
    if session_dir.exists():
        try:
            # 가장 최근 세션 ID에서 hostname 추출
            recent = max(session_dir.glob("*"), key=lambda p: p.stat().st_mtime if p.is_file() else 0, default=None)
            if recent:
                # 세션 ID 형식: hostname-timestamp 또는 UUID
                name = recent.name.replace(".jsonl", "").split("-")[0]
                if name and len(name) >= 3:
                    return name
        except Exception:
            pass

    # 4. hostname (최후 fallback)
    try:
        hostname = socket.gethostname().split(".")[0]
        if hostname:
            return hostname
    except Exception:
        pass

    return None


def _hermes_config_path() -> Path | None:
    """Hermes config.yaml 경로 탐지."""
    env = os.environ.get("HERMES_CONFIG")
    if env:
        return Path(env)
    la = os.environ.get("LOCALAPPDATA")
    if la:
        p = Path(la) / "hermes" / "config.yaml"
        if p.exists():
            return p
    return None


def download_tarball(url: Path, token: str | None = None) -> Path:
    """tarball 다운로드 (token 있으면 Authorization 헤더)."""
    print(f"Downloading {TARBALL_URL}...")
    headers = {"User-Agent": "LessonLoop-QuickInstall"}
    if token:
        headers["Authorization"] = f"token {token}"
    req = Request(TARBALL_URL, headers=headers)
    with urlopen(req, timeout=30) as r:
        data = r.read()
    url.write_bytes(data)
    print(f"Downloaded: {url} ({len(data)} bytes)")
    return url


def _safe_members(tf, dest):
    """경로 순회 방어(CVE-2007-4559 계열) — dest 밖으로 나가는 멤버 거부.
    버전 무관(filter= 미지원 구 Py 호환) 수동 검증."""
    base = dest.resolve()
    for m in tf.getmembers():
        p = (dest / m.name).resolve()
        try:
            p.relative_to(base)
        except ValueError:
            raise RuntimeError(f"unsafe tar member (path escape): {m.name}")
        yield m


def extract_tarball(tarball: Path, dest: Path) -> Path:
    """tar.gz 압축 해제 (tarfile 모듈)."""
    print(f"Extracting to {dest}...")
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as tf:
        tf.extractall(dest, members=_safe_members(tf, dest))
    # lessonloop-main 폴더 확인
    extracted = dest / f"lessonloop-{BRANCH}"
    if not extracted.exists():
        # tar.gz 내부 구조가 다를 수 있음
        for item in dest.iterdir():
            if item.is_dir() and "lessonloop" in item.name.lower():
                extracted = item
                break
    print(f"Extracted: {extracted}")
    return extracted


def quick_install(args):
    # 에이전트 이름 결정 (명시 → 자동 감지)
    agent = args.agent
    if not agent:
        agent = auto_detect_agent()
        if agent:
            print(f"Auto-detected agent: {agent}")
        else:
            print("ERROR: --agent required (auto-detect failed)")
            return 1

    # 설치 경로 결정 (기본 AppData/Local/lessonloop)
    if args.dir:
        install_dir = Path(args.dir).expanduser().resolve()
    else:
        # OS별 기본 경로
        if sys.platform == "win32":
            appdata = os.environ.get("LOCALAPPDATA")
            if appdata:
                install_dir = Path(appdata) / "lessonloop"
            else:
                install_dir = Path.home() / ".lessonloop"
        else:
            install_dir = Path.home() / ".lessonloop"

    print(f"Install directory: {install_dir}")

    # 임시 디렉토리에 tarball 다운로드
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tarball = tmpdir / "lessonloop.tar.gz"

        download_tarball(tarball, args.token)

        # 압축 해제
        extract_dir = tmpdir / "extract"
        source_dir = extract_tarball(tarball, extract_dir)

        # install.py 경로
        install_script = source_dir / "scripts" / "install.py"
        if not install_script.exists():
            print(f"ERROR: install.py not found at {install_script}")
            return 1

        # install.py 실행
        print("Running install.py...")
        cmd = [sys.executable, str(install_script), "--agent", agent]
        if args.runtime:
            cmd.extend(["--runtime", args.runtime])
        if args.journal_dir:
            cmd.extend(["--journal-dir", args.journal_dir])
        if args.cards_dir:
            cmd.extend(["--cards-dir", args.cards_dir])
        if args.sync_dir:
            cmd.extend(["--sync-dir", args.sync_dir])
        if args.mode:
            cmd.extend(["--mode", args.mode])
        if args.dry_run:
            cmd.append("--dry-run")

        print(f"Command: {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=source_dir)

        # source_dir을 install_dir로 복사 (선택)
        if args.keep_source and args.dry_run:
            print(f"DRY RUN: would copy source to {install_dir}")
        elif args.keep_source:
            print(f"Copying source to {install_dir}...")
            if install_dir.exists():
                shutil.rmtree(install_dir)
            shutil.copytree(source_dir, install_dir)
            print(f"Source copied: {install_dir}")

        return result.returncode


def main():
    ap = argparse.ArgumentParser(description="LessonLoop quick install (GitHub tarball)")
    ap.add_argument("--agent", help="에이전트 식별자 (자동 감지: env HERMES_CONFIG/.claude sessions/hostname)")
    ap.add_argument("--dir", help="설치 경로 (기본: AppData/Local/lessonloop)")
    ap.add_argument("--runtime", choices=["claude-code", "hermes"], default="claude-code")
    ap.add_argument("--journal-dir", help="journal 경로")
    ap.add_argument("--cards-dir", help="cards 경로")
    ap.add_argument("--sync-dir", help="journal 미러 경로")
    ap.add_argument("--mode", choices=["collection", "full"], default="collection")
    ap.add_argument("--token", help="GitHub token (private repo 필요, env LESSONLOOP_GH_TOKEN)")
    ap.add_argument("--keep-source", action="store_true",
                    help="소스를 설치 경로에 보존 (기본: 훅만 설치하고 소스 삭제)")
    ap.add_argument("--dry-run", action="store_true", help="다운로드/압축해제 후 install.py --dry-run 만 실행")
    args = ap.parse_args()

    # token fallback
    if not args.token:
        args.token = os.environ.get("LESSONLOOP_GH_TOKEN")

    return quick_install(args)


if __name__ == "__main__":
    sys.exit(main())
