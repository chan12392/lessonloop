---
id: b2c3d4e5f6a1
l1: 기술
l2: secret-leak
trigger: ".env secrets API_KEY token password AWS_ git add committed .gitignore dotenv"
rule: 비밀(API 키·토큰·.env)은 커밋 전 `.gitignore` 확인 — 이미 커밋됐으면 `git rm --cached` + 키 폐기(히스토리에 남음)
enforce: guard
severity: high
sources: 3
status: canonical
stale_if: ""
---

## facts
- `.env` 를 `.gitignore` 없이 `git add .` 하면 비밀이 히스토리에 영구 잔류. private repo여도 collaborate/leak 시 노출
- 히스토리에서 지워도 clone 받은 사람의 로컬엔 남음 → **키 폐기가 우선**, history rewrite는 부차
- `.env.example`(더미값)만 커밋. 실제값은 로컬 `.env`(gitignored) 또는 비밀관리자

## fix
```
echo ".env" >> .gitignore
git rm --cached .env          # 추적 해제(로컬 파일 유지)
# 이미 푸시된 비밀 → 즉시 발급처에서 키 폐기/로테이션
```

## check
- 비밀이 담긴 파일이 `.gitignore` 에 있는가
- 커밋 전 `git status` 에 `.env` 가 unstaged 인가
- 이미 푸시된 비밀을 폐기했는가 (rewrite만으로는 부족)
