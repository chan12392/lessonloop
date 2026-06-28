---
name: lessonloop-config
description: Configure LessonLoop paths (journal_dir, cards_dir, sync_dir) without reinstallation. Shows or sets paths stored in lessonloop.json.
---

LessonLoop 경로 설정 (재설치 불필요).

## 사용법

경로 확인:
```bash
/lessonloop-config --show
```

journal 경로 설정 (capture 출력):
```bash
/lessonloop-config --journal-dir "G:/lessonloop/journals"
```

cards 경로 설정 (카드·index, recall 읽음):
```bash
/lessonloop-config --cards-dir "G:/lessonloop/cards"
```

**sync_dir 설정** (capture 가 journal 을 공유FS 에 미러 → 다른 머신 수집기가 읽음):
```bash
/lessonloop-config --sync-dir "/home/me/lessonloop/sync"
```
미러 끄기:
```bash
/lessonloop-config --sync-dir ""
```

기본값으로 리셋 (스크립트 옆):
```bash
/lessonloop-config --reset
```

## 자연어/채널 명령 매핑 (텔레그램 등)

사용자가 "lessonloop --sync-dir 경로" 또는 "sync-dir 을 경로로 설정" 이라고 하면,
`config_set.py --sync-dir "<경로>"` 를 실행. **경로는 절대경로여야 함** — 상대경로는
capture 실행 cwd 기준으로 잘못 잡힘(채널/Hermes 원격 설정 시 특히 주의).
capture 는 재실행 시 lessonloop.json 의 sync_dir 을 자동 반영하므로 **훅 재설치 불필요**.

## 경로 설명

- **journal_dir**: `journal-<agent>.jsonl` 저장 위치 (수집물)
- **cards_dir**: `cards/`, `lessons_index.csv` 위치 (교훈 정본)
- **sync_dir**: capture 가 `journal-<agent>.jsonl` 을 미러(복제)하는 공유FS 폴더. 로컬은 dedup 정본(불변), 미러는 append-only(fail-open). 멀티머신 수집용.

두 경로를 분리하면 journal을 로컬에, cards를 공유FS(클라우드 동기화 폴더 등)에 둘 수 있습니다.
sync_dir 은 원격 머신→수집기 머신 처럼 동일 cards 를 못 공유하는 머신에서 journal 만 흘려보낼 때 씁니다.

