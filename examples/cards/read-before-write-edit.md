---
id: PENDING
l1: 에이전트행동
l2: tool-sequence
trigger: "Read Write Edit overwrite file has not been read yet new_string old_string file_path"
rule: Write·Edit 호출 직전, 같은 file_path 를 이 세션에서 Read 했는지 확인 — 안 했으면 먼저 Read 할 것(CC 는 Read 없는 Write/Edit 을 "File has not been read yet" 로 거부)
trigger_tools: "Write,Edit"
enforce: guard
severity: high
sources: 1
status: canonical
stale_if: ""
---

## facts
- Claude Code Write/Edit 툴은 같은 세션에서 해당 file_path 의 Read 를 선행 요구 — 안 하면 `<tool_use_error>File has not been read yet. Read it first before writing to it.</tool_use_error>` 로 즉시 거부(객관실패, 행동 무효)
- Read 없이 Write/Edit 시도 = 파일 현재 내용 모른 채 덮어쓰기 → new_string 가 실제 내용과 안 맞으면 파손. Edit 의 old_string 매칭도 현재 내용 알아야 가능
- 이 카드는 토큰 매칭이 아니라 **tool 기반 트리거**(trigger_tools: Write,Edit) — 경로 토큰과 무관하게 Write/Edit 호출마다 발화

## fix
- Write/Edit 직전: 이 세션에서 같은 file_path 를 Read 했나 확인(기억 의존❌, 컨텍스트/최근 Read 기록 확인)
- 안 했으면 먼저 Read → 그 내용 기반으로 new_string/old_string 작성
- 완전 신규 파일(기존 없음)은 첫 Write 허용되나, 기존 파일 덮어쓰기는 반드시 Read 선행

## check
- 이 Write/Edit 의 file_path 를 이 세션에서 실제로 Read 했나
- new_string/old_string 이 Read 한 현재 내용과 정합하나
