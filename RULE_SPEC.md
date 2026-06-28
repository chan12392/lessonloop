# RULE_SPEC — 카드 rule 재작성 규칙 (self-witness refiner 가이드)

> 이 문서는 **구동 중인 에이전트(LLM)** 가 LessonLoop 가 잡은 *약한카드*(recall 이 떴는데
> 같은 행동이 또 실패)의 `rule`/`fix`/`check` 를 재작성할 때 따르는 처방전이다.
> `scripts/repair.py` 가 `staging/repair-tasks-<agent>.md` 에 후보를 모아주면, 에이전트는
> 이 규칙에 맞춰 판단·재작성한다. **API 키 불필요** — 쓰고 있는 LLM 본인이 한다.
>
> 낮은 등급 모델도 품질을 내도록 **처방형**(fill-in)으로 고정. 판단이 서지 않으면 재작성하지 말 것.

## 0. 재작성 전 반드시 먼저 판단 (이 단계 skip 금지)

약한카드가 떴다고 무조건 고치지 마라. 재발의 **진짜 원인**을 3가지로 분류:

| 원인 | 뜻 | 행동 |
|---|---|---|
| **A. rule 부실** | rule 이 추상적/불완전 → 행동 직전에 못 막음 | **재작성** (이 문서 1~4따라) |
| **B. trigger-overlap** | 카드가 *관련 없는* 명령에 토큰 겹쳐 발화. 실패 원인은 다름 | **재작성 ❌** — task 에 `verdict: trigger-overlap` 표시만 |
| **C. 이미 코드 수정됨** | 재발은 과거 이력, 원인은 이미 fix됨 | **재작성 ❌** — `verdict: already-fixed` + 근거 1줄 |

**B/C 판정법** (측정): task 에 있는 `[실패 excerpt]` 와 카드 `[현재 rule]` 를 읽고 —
실패의 근본 원인이 rule 이 말하는 것과 **같은가**?
- 같다 → A (재작성)
- 다르다 → B (trigger-overlap)
- 같았지만 코드가 이미 바뀌어 더 안 일어난다 → C

**판정이 서지 않으면 B 로 두고 건드리지 마라.** (과장·지어내기 금지)

## 1. 좋은 rule 의 형태 (재작성 목표)

- **1~2문장**. 길면 못 쓴다.
- **"행동 직전에 확인"** 형태. `~하기 전에 X를 확인/실행하라` — 사후 설명이 아니라 사전 체크.
- **구체적**: 도구명·명령·확장자·조건을 명시. 추상 금지("주의해라"·"신중히"·"적절히").
- **실행가능**: 모델이 다음 턴에 바로 할 수 있는 동작.
- trigger 토큰과 **정합** — 그 토큰이 뜰 때 이 rule 이 맞는 상황이어야 함.

## 2. 재작성 레시피

```
[조건/trigger 상황] → [행동 직전 확인할 구체적 것] → [안 되면 할 대체]
```
예) `.bat/.ps1 저장 직전 → 첫 4바이트 hex 실측(BOM/개행) → cp949/CRLF면 지정 인코딩으로 재저장`

## 3. 하지 말 것 (anti-pattern)

- ❌ 원인 단언 없이 "~할 수 있다" 막연 경고
- ❌ card 의 facts 를 위반/과장하는 내용
- ❌ rule 을 generic 하게 바꿔 더 넓게 발화시키기 (오탐 증가)
- ❌ 근거 없이 severity 올리기
- ❌ B/C 케이스를 억지 A 로 만들어 "재작성했다" 보고

## 4. 산출물 (cards/<slug>.md 편집)

1. `rule:` 필드만 1~2문장으로 교체 (facts/fix/check 는 A 일 때만 보강).
2. frontmatter 에 표시:
   - `repaired_at: <ISO날짜>` (재작성 시)
   - 또는 `repair_verdict: trigger-overlap|already-fixed` (재작성 안 했을 때)
3. `sources:` +1 (self-witness 도 출처).
4. 본문 맨 아래 한 줄: `<!-- repaired: <한글 1줄 근거> -->`

## 5. 검증 (재작성 후)

- `py scripts/build_index.py` 로 인덱스 재생성(id 해시 갱신).
- recall 이 새 rule 로 정상 발화하는지 확인 (`scripts/recall_hook.py` dry).
- 다음 feedback 주기에 해당 카드 recur 가 0 으로 떨어지는지 관찰(떨어지면 진짜 A 였음).

## 6. 약모델용 최소 체크리스트

- [ ] B/C 아닌가 먼저 확인 (0단계)
- [ ] rule 이 "직전 확인" 형태인가
- [ ] 구체적 도구/조건 들어갔는가
- [ ] 1~2문장인가
- [ ] facts 위반 없나
- [ ] repaired_at 또는 repair_verdict 표시했나
