---
name: lessonloop-config
description: Configure LessonLoop paths (journal_dir, cards_dir, sync_dir) for Hermes agents. Uses config_set.py as the core.
prerequisites: []
---

LessonLoop 경로 설정 (Hermes 에이전트).

## 사용법

경로 확인:
```
/lessonloop-config --show
```

journal 경로 설정:
```
/lessonloop-config --journal-dir "G:/lessonloop/journals"
```

cards 경로 설정:
```
/lessonloop-config --cards-dir "G:/lessonloop/cards"
```

**sync_dir 설정** (capture 가 journal 을 공유FS 에 미러 → 다른 머신 수집기가 읽음).
원격 머신→수집기 머신 수집 시 핵심. **반드시 절대경로**:
```
/lessonloop-config --sync-dir "C:/Users/<user>/Desktop/vault/lessonloop/sync"
```
⚠ **절대경로 필수** — 상대경로(`Desktop/...`)는 Hermes 실행 cwd(`AppData/Local/hermes`) 기준으로
잡혀 엉뚱한 곳에 미러 폴더가 생김(알려진 함정). 원격 머신=절대경로,
동기화로 수집기 머신 기준 동일 파일.
미러 끄기: `/lessonloop-config --sync-dir ""`

기본값 리셋:
```
/lessonloop-config --reset
```

## 자연어/채널 명령 매핑 (텔레그램 등)

사용자가 "lessonloop --sync-dir 경로" 또는 "sync-dir 을 경로로 설정" 이라고 하면,
`config_set.py --sync-dir "<경로>"` 를 실행. **경로는 반드시 절대경로로 변환/확인**할 것
(상대경로는 Hermes cwd 기준 함정). capture 는 재실행 시 lessonloop.json 의 sync_dir 을
자동 반영하므로 **훅 재설치 불필요**.

## 스킬 등록

`install.py --runtime hermes` 실행 시 **자동 등록됨** — Hermes `config.yaml` 의
`skills.external_dirs` 에 lessonloop skills 폴더가 추가됨(라인 삽입, 주석·기존값 보존, 중복 skip).
Hermes 재시작 후 `/lessonloop-config` 사용 가능.

수동 필요 시(config.yaml):
```yaml
skills:
  external_dirs:
    - "/home/me/lessonloop/skills"
```

## 경로 설명

- **journal_dir**: `journal-<agent>.jsonl` 저장 위치 (capture 출력)
- **cards_dir**: `cards/`, `lessons_index.csv` 위치 (recall 읽음)
- **sync_dir**: capture 가 `journal-<agent>.jsonl` 을 미러하는 공유FS 폴더. 다른 머신(예: 원격 에이전트)→수집기 머신으로 journal 넘길 때. 로컬 경로 ↔ 수집기 경로(동일 파일, 동기화 폴더 통해).

분리 필요 시 로컬/공유FS 각각 지정.

