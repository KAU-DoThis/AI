# Fintris Spring ↔ AI 서버 연동 계약 초안

> 이 문서는 기존 이벤트 수신·큐 API의 초기 설계 기록입니다. 새로 구현된 방 입장 문구 생성 API는 [COACH_ROOM_API.md](COACH_ROOM_API.md), 모델 연결은 [LLM_SETUP.md](LLM_SETUP.md)를 참고하세요. 아래의 “LLM 호출 없음” 설명은 기존 수신 API 범위를 가리킵니다.

작성 기준: 2026-10-24, 백엔드 공개 `dev` 브랜치의 README / 기능명세서 / User.java / BlockController.java. **아직 백엔드 팀과 확정한 계약이 아니며, Spring 소스에 이 엔드포인트가 구현되어 있다는 뜻이 아닙니다.**

## 데이터 흐름

```text
Fintris 앱 → Spring (회원 인증, CODEF, DB, 금융 계산)
                         ↓ 내부 HTTP + X-Internal-Token
               Fintris AI Server (FastAPI)
                         ↓
               AgentManager.get_or_create(user_id)
                         ↓
                UserAgent.inbox (FIFO)
```

한 사용자의 에이전트는 이 단계에서 실행되지 않으며 받은 메시지/이벤트만 큐에 보관합니다. 실제 분류·대화·보고서와 콜백은 후속 단계입니다.

## 호출 규약

- URL: `POST {AI_SERVER_URL}/internal/v1/agent/events`
- Header: `X-Internal-Token: {FINTRIS_INTERNAL_TOKEN}`
- Content-Type: `application/json`
- `user_id`: Spring `User.id`와 같은 64비트 정수 값(양수)
- `event_id`: Spring 측에서 생성하는 요청 고유 ID. 재전송에는 **동일한 ID**를 사용해야 중복으로 등록되지 않습니다.
- `event_type`: `initial_blocks` / `chat_message` / `monthly_report`
- `payload`: 해당 기능별 JSON 객체. **초기 DTO 형태는 제안이며 추후 팀 합의로 구체화합니다.**

정상 수신 시 **HTTP 202**와 다음 예시를 반환합니다.

```json
{
  "event_id": "chat-101-m-1",
  "user_id": 101,
  "agent_id": "8b8a9b4b-b5a2-4a18-93e0-0bb5ca906f35",
  "agent_created": true,
  "duplicate": false,
  "status": "queued",
  "pending_events": 1
}
```

이 응답은 **접수 확인**이지 실제 AI 처리 결과가 아닙니다.

## 세 가지 이벤트와 요청 데이터

### 1. initial_blocks (기능 F-19 → F-04)

- **시점:** CODEF 계좌 연동 및 최근 3개월 거래 불러오기가 완료된 후
- **제안 payload:** `analysis_period`, `transactions`, `summary`
- **거래 항목 예시:** `transaction_id`, `occurred_at`, `amount`(원 단위 정수), `direction`, `merchant_or_payee`
- **중요:** 계좌 인증 비밀번호, CODEF connectedId, 카드 비밀번호 같은 비밀정보는 절대 전달하지 않습니다.
- **향후 AI 산출물:** 분류 결과/블록 구성안/이유. 실제 잔액 계산과 블록 저장은 Spring에서 수행

### 2. chat_message (기능 F-16)

- **시점:** 사용자가 Fintris 앱에서 AI 채팅 메시지를 보낸 후, Spring 인증이 끝난 시점
- **제안 payload:** `message_id`, `text` (향후 `conversation_id`, 관련 이벤트 context 추가 가능)
- **특징:** 같은 `user_id`의 메시지는 같은 Agent의 Inbox에 추가. 이미 실행 중일 때는 다음 루프 경계에서 반영 예정
- **향후 AI 산출물:** 사용자 응답/백엔드 도구 실행 제안 등 (스트리밍·결과 전달 프로토콜은 아직 미정)

### 3. monthly_report (기능 F-12)

- **시점:** 매월 1일 이전 달 집계 및 정기 동기화 완료 후
- **제안 payload:** `month`(YYYY-MM), `metrics` (Spring이 계산한 지출, 절약 성과, 블록 달성률, 점수)
- **향후 AI 산출물:** 집계값을 설명하는 보고서 문장과 코멘트

JSON 전체 샘플은 `../examples/requests.json` 참고.

## Backend 팀과 맞출 항목

1. Spring 실제 `AI_SERVER_URL`, 전달 방식(HTTP 클라이언트), 서비스 간 인증 키의 관리 방식
2. initial_blocks에 실거래 목록을 보낼지, 거래 분류와 블록 제안 API를 별도로 구성할지
3. Spring 집계 DTO/필드명과 AI 결과 DTO/콜백 주소
4. 채팅 실시간 응답을 SSE/별도 조회/콜백 중 어떤 방식으로 앱에 돌려줄지
5. 사용자 계정 삭제·재가입 시 Agent 수명 종료/상태 삭제 연동

## 현재 구현의 경계

- in-memory AgentManager + Inbox, **uvicorn worker 1개** 기준
- 재시작 시 Agent와 이벤트가 사라짐. 실제 서비스 전 내구성 있는 메시지 저장 및 사용자별 실행 소유권 설계 필요
- 실제 shs-agent, JEFF, LLM, DB, CODEF 호출 없음
- 내부 API 비밀키로 요청을 인증하며 앱에서 직접 이 API를 호출하지 않음

## 근거

- https://github.com/KAU-DoThis/Back_end/blob/dev/README.md
- https://github.com/KAU-DoThis/Back_end/blob/dev/CLAUDE.md
- https://github.com/KAU-DoThis/Back_end/blob/dev/docs/FINTRIS_%EA%B8%B0%EB%8A%A5%EB%AA%85%EC%84%B8%EC%84%9C.md
- https://github.com/KAU-DoThis/Back_end/blob/dev/src/main/java/com/dothis/fintris/user/entity/User.java
- https://github.com/KAU-DoThis/Back_end/blob/dev/src/main/java/com/dothis/fintris/block/controller/BlockController.java
