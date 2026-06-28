---
id: c3d4e5f6a1b2
l1: 에이전트행동
l2: completion-honesty
trigger: "완료 했다 fixed done 동작 확인 검증 stderr exit-code 테스트 재시작"
rule: "완료" 보고 전 실제로 검증(명령 exit-code·로그·재실행) — "했다"는 증거 통과분만, 검증 전엔 "하는 중"
enforce: hook
severity: medium
sources: 4
status: canonical
stale_if: ""
---

## facts
- "명령 실행됨" ≠ "의도한 결과". exit 0이라도 논리적 실패 가능(stderr만, 또는 조용히 잘못된 상태)
- "빠르게 했다"는 보고가 실측을 대신하지 않는다 — 검증 없는 완료 보고는 대표가 수습하게 만든다
- 성공 메시지(stdout)와 실제 효과(fs/상태)가 다를 수 있다. fs/DB 직접 확인이 유일 증거

## fix
- 변경 후 검증 단계 명시: exit-code 체크, 대상 파일/상태 재읽기, 또는 테스트 실행
- 검증 통과만 "완료". 통과 전엔 "진행 중/확인 필요" 로 보고
- 한 응답에 여러 작업을 몰아서 보고하지 않는다 — 핵심 하나씩, 검증 포함

## check
- 보고한 "완료" 에 대응하는 검증 증거(exit-code/로그/재실행)가 있는가
- stdout 성공 메시지만 믿지 않고 fs/상태를 직접 봤는가
