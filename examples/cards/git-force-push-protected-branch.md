---
id: a1b2c3d4e5f6
l1: 기술
l2: destructive-git
trigger: "git push --force force-with-lease main master protected rebase rewrite-history reflog"
rule: `git push --force`(/`-f`)는 protected 브랜치(main/master)에 쓰지 않는다 — `--force-with-lease` 로 바꾸거나 rebase 전에 원격과 동기화
enforce: hook
severity: high
sources: 2
status: canonical
stale_if: ""
---

## facts
- `--force`는 원격 히스토리를 로컬로 덮어쓴다 — 팀원의 푸시를 조용히 날림. force 직전 원격 상태를 검증하지 않는다
- `--force-with-lease`는 원격 ref가 마지막 fetch와 같을 때만 강제 → 누가 먼저 푸시했으면 거부
- rebase 후 force는 동일 로컬 ref에만. 보호 브랜치엔 절대: 별도 브랜치→PR

## fix
```
git fetch origin
git push --force-with-lease origin my-feature
```
- 보호 브랜치엔 force 계열 전부 금지. 실수로 날렸으면 `git reflog` 로 직전 ref 복구

## check
- `--force-with-lease` 를 썼는가 (`--force`/`-f` 아님)
- 대상이 protected(main/master/release/*)가 아닌가
