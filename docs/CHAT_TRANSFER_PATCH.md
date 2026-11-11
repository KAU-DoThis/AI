# Fintris 채팅·이동 제안 수정본

## 적용 대상과 검증 범위

사용자가 올린 `app.zip`을 기준으로 만든 **교체 파일 9개 + 추가 파일 1개**다.
전체 저장소나 최신 Spring 소스를 내려받아 수정한 결과가 아니다.
외부 계약은 이번 대화에서 합의한 `POST /internal/ai/coach/chat`의
`{userId, sessionId, text, blocks, waitingFunds}` → `{text, transfer}`를 구현했다.
Spring의 실제 제안 카드 생성·승인 처리는 수정하지 않았고 실서버로 검증하지 않았다.
실제 vLLM/Ollama도 호출하지 않았다. 로컬 테스트에서는 LLM 응답을 테스트용으로 대체한다.

원본 ZIP SHA-256: `9592e99ddd3d392dc6a24be05c06f78bfa2d056387a9482dfa7637a89ee3c26f`

## 1. 붙여넣기

프로젝트를 백업하거나 현재 변경을 별도 커밋한 뒤, 이 묶음의 `app/`, `tests/`,
`docs/`, `examples/`를 **기존 프로젝트 루트에 병합 복사**한다.
`app/` 폴더를 삭제하고 교체하는 것이 아니다. 이 묶음에는 수정 파일만 있다.
새 파일은 만들고 동일한 경로의 파일은 이 버전으로 교체한다.
GitHub에 자동 커밋·푸시하지 않는다. `.env`, 비밀 키, 의존성 잠금 파일은 포함하지 않는다.

| 파일 | 적용 |
|---|---|
| `app/agent/chat_manager.py` | 교체 |
| `app/agent/chat_session.py` | 교체 |
| `app/api/v1/agent_events.py` | 교체 |
| `app/api/v1/coach_chat.py` | 교체 |
| `app/core/chat_factory.py` | 교체 |
| `app/main.py` | 교체 |
| `app/modules/agent/tools.py` | 교체 |
| `app/schemas/coach_chat.py` | 교체 |
| `app/services/coach_chat.py` | 추가 |
| `app/tools/fintris_chat.py` | 교체 |

`tests/chat_addon/test_chat_addon.py`는 이전 채팅 추가 묶음에 있던 테스트를 새 계약으로 갱신한 파일이다.
`tests/chat_transfer/test_chat_transfer.py`는 이동 제안/실제 FastAPI 앱 연결 검증을 추가한다.
프로젝트에서 이 테스트 파일을 별도로 수정했다면 로컬 변경과 병합한다.
이 두 테스트 폴더 외의 기존 테스트는 제공받지 못했다. 전체 저장소 테스트는 로컬에서 추가로 실행한다.

## 2. 바로 확인하기

기존 가상환경을 켜고 프로젝트 루트에서 실행한다. 새 런타임 라이브러리는 추가하지 않았다.
테스트에는 기존의 pytest, pytest-asyncio, httpx가 필요하다.

```powershell
python -m pytest tests/chat_addon tests/chat_transfer -q
python -m pytest -q
git diff --check
git diff --stat
```

첫 명령은 동봉 테스트, 두 번째는 본인 저장소 전체 테스트다.
예전 `message` 요청/메타데이터 응답을 가정한 다른 테스트·호출 스크립트는 새 계약으로 갱신해야 한다.
기존 실행 방법과 환경변수는 유지한다. 예를 들어 기존 프로젝트가 env-file 실행을 사용한다면:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --env-file .env
```

매니저/대화 기록/중복 요청 결과가 메모리 기반이므로 단일 프로세스·워커 기준이다.
서버 재시작·유휴 정리 시 기록이 사라진다. 장기 기억 DB 저장은 이번 범위가 아니다.

## 3. 외부 API 계약

```http
POST /internal/ai/coach/chat
Content-Type: application/json
X-Internal-Token: <FINTRIS_INTERNAL_TOKEN과 동일한 내부 토큰>
```

요청 예제는 `examples/chat_transfer_request.json`에 있다.
`LLM_API_KEY`는 AI→LLM용으로, 백엔드→AI의 `FINTRIS_INTERNAL_TOKEN`과 다르다.
인증은 room과 동일한 `require_internal_token` 함수를 사용한다.
Spring이 이 헤더를 보내는지는 실제 연동 환경에서 확인해야 한다. 인증을 끄지는 않았다.

| 필드 | 처리 |
|---|---|
| `userId` | 양의 정수. 백엔드가 인증된 사용자의 값을 넣어야 함 |
| `sessionId` | 백엔드가 발급한 비어 있지 않은 문자열. 공백 제거 등 변형 없이 사용 |
| `text` | 필수 메시지. 기존 `message` 필드는 받지 않음 |
| `requestId` | 선택. 생략해도 됨. 동일 ID/검증된 동일 본문이면 보관 중인 실행 결과 재사용 |
| `blocks` | room과 동일한 BlockContext. `blockId`, `name`, `balance` 필요 |
| `waitingFunds` | room과 동일한 WaitingFundContext. `waitingFundId`, `amount` 필요, `label`/`description` 선택 |

`blocks`/`waitingFunds` 생략 또는 null은 '정보 없음', `[]`는 '조회 결과 비어 있음'이다.
ID 별칭 `id`는 더 이상 쓰지 않고 room과 같은 `blockId`를 사용한다.
알 수 없는 금융 DTO 필드는 room 모델과 같이 버린다. 요청 최상위 미정의 필드는 거절한다.
조회 도구는 **현재 요청의 스냅샷**을 읽으며 실시간 백엔드 API를 호출하지 않는다.

일반 대화 응답:

```json
{"text": "안녕하세요!", "transfer": null}
```

이동 제안 응답(예시):

```json
{
  "text": "‘생활비’에서 ‘여행’으로 50,000원 이동하는 제안이에요. 변경 내용을 확인해 주세요. 아직 잔액은 변경되지 않았어요.",
  "transfer": {"fromBlockId": 1, "toBlockId": 2, "amount": 50000}
}
```

내부의 agentId, 생성된 실행 ID, 사용한 도구 목록은 응답에 포함하지 않는다.
`requestId`를 생략하면 AI가 생성하는 ID는 추적용일 뿐이고, 동일 요청을 다시 보내도 새로운 실행이 된다.
명시적 requestId 중복 방지도 프로세스 메모리/보관 기간 내에서만 동작한다.
AI가 같은 응답을 재사용해도 **백엔드의 제안 카드 중복 생성까지 방지하지는 않는다**.
최종 적용과 제안 생성의 멱등성은 백엔드에서 별도로 보장해야 한다.

## 4. 구현 흐름과 변경 범위

```text
백엔드 요청 text
  → AgentManager (사용자별 직렬 처리 / 세션별 기록)
  → 교체 가능한 Agent.run(AgentInput, ToolExecutor)
  → get_blocks → propose_block_transfer
  → 이번 요청의 ToolExecutor 실행 기록
  → Fintris 응답 변환·안내 문구 확정
  → {text, transfer}
  → 백엔드에서 카드 생성 / 사용자 확인 / 최종 검증 후 반영
```

`propose_block_transfer(from_block_id, to_block_id, amount)`는 양의 정수 금액,
서로 다른 블록 ID, 이번 요청에 존재하는 고유 ID인지를 확인한다.
사용자 ID·인증 토큰·confirmed 값을 LLM 인자로 받지 않는다.
최신 잔액/소유권/버전/상태 등의 최종 업무 규칙은 백엔드 책임이다.
이 함수에는 HTTP 호출, DB 쓰기, 실제 잔액 변경이 없다.

`transfer`는 LLM 답변 JSON을 파싱하지 않고 도구 실행기의 검증된 결과로 만든다.
한 요청에 서로 다른 이동안이 여러 개 생기면 임의 선택/덮어쓰기를 하지 않고 `transfer: null`과
한 건을 선택해 달라는 안내를 반환한다. 같은 이동안의 반복 호출은 한 건으로 취급한다.
유효한 제안 뒤의 마지막 이동 제안 시도가 거절되면 이전 제안을 재사용하지 않는다.
오류가 먼저 난 뒤 수정한 단일 유효 제안이 성공하는 것은 허용한다.
실행 실패·시간 초과·최대 단계 도달이면 제안을 반환하거나 대화를 성공으로 저장하지 않는다.

이동 제안이 있는 응답의 안내 문구는 서버가 만든다. LLM이 '이동했어요'라고 말하더라도
사용자에게는 아직 잔액이 바뀌지 않았다고 표시하고, 동일 문구를 대화 기록에 저장한다.
일반 대화는 기존 LLM의 자연어 답변을 사용한다. 별도 LLM 호출은 늘리지 않는다.

`modules/agent/contracts.py`, `simple_agent.py`, `core/chat_llm.py`, room/분류 구현은 변경하지 않았다.
매니저에는 일반적인 실행 결과와 선택적 host 결과 처리 함수를 연결했고,
금융 전용 처리와 응답 문구는 `services/coach_chat.py`에 둔다.
에이전트를 바꿀 때도 기존 `run()/aclose()` 계약과 제공된 ToolExecutor를 사용하면 된다.

## 5. 로컬 HTTP 확인

PowerShell에서 `FINTRIS_INTERNAL_TOKEN`을 현재 셸에도 설정한 뒤 실행한다.
서버가 `.env`를 읽었다고 셸에도 자동으로 변수가 생기는 것은 아니다.

```powershell
$env:FINTRIS_INTERNAL_TOKEN = "실제 내부 토큰"
./examples/chat_transfer_test.ps1 -BaseUrl "http://127.0.0.1:8000"
```

예제 스크립트는 실제 AI 서버를 호출한다. 응답 예제 파일은 성공 형태를 설명하는 예시이며
실제 모델의 결정/응답을 보장하지 않는다. 배포 모델이 tool calling을 지원하도록 설정되어야 한다.

## 6. 테스트 및 남은 확인

- 동봉 105개 테스트 통과. `TEST_RESULTS.txt`에 명령·환경·실제 출력을 기록했다.
- 입력/출력 DTO, 금액/ID 검증, 도구 결과 위조·공유 방지, 사용자별 순차 처리, 재시도, 유휴 정리,
  대화 이력, 오류·시간 초과, 교체 가능한 엔진, 실제 FastAPI 라우터/수명주기 연결을 검증했다.
- room, health, categories, 이벤트, 거래 분류·초기 분류는 제공된 앱의 기본 호출 회귀 검증을 수행했다.
- 실제 Spring/vLLM 통신, 실제 모델의 자연어 도구 선택 정확도, 카드 표시·승인·거절·DB 반영은 미검증이다.
- repo 전체의 테스트/requirements/CI 설정은 업로드되지 않아 변경하지 않았다.

기존 `main.py`에 없던 인증을 해제하거나 기존 API를 삭제하지 않았다. 별도의 BackendClient,
자동 송금, 새 DB, 장기 기억, 자동 commit/push는 추가하지 않았다.

## 구현 시 확인한 공식 문서

- Pydantic serialization: https://docs.pydantic.dev/latest/concepts/serialization/
- Python asyncio task/shield: https://docs.python.org/3/library/asyncio-task.html

런타임 동작 판단은 문서뿐 아니라 실제 설치된 라이브러리에서 동봉 테스트로 확인했다.
