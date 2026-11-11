# 초불이 방 입장 API — 백엔드 전달 명세

작성일: 2026-11-11 (KST)

AI 서버에 구현한 방 입장 API의 계약이다. 백엔드가 로그인한 사용자의 현재 상태를 보내면,
AI가 `greeting`과 바로 시키기 버튼 문구 3개를 생성해 같은 HTTP 요청의 응답으로 반환한다.
이 변경에는 Spring 백엔드 코드 수정이 포함되지 않는다.

## 1. 호출 정보

```http
POST {AI_SERVER_BASE_URL}/internal/ai/coach/room
Content-Type: application/json
X-Internal-Token: <FINTRIS_INTERNAL_TOKEN과 같은 값>
```

- 호출 주체: Spring 백엔드
- 성공 상태: `200 OK`
- 응답 본문: `greeting`, `suggestions`를 가진 JSON 객체
- 응답 방식: 생성이 끝날 때까지 기다린 뒤 반환. 접수용 `202`, 작업 ID, 별도 결과 조회는 사용하지 않는다.
- 실행 범위: 인사말과 버튼 문구 생성. 블록 이동·분배·저장을 실행하지 않는다.

`AI_SERVER_BASE_URL`은 이 문서에서 AI FastAPI 서버 주소를 나타내는 표기다.
기존 백엔드 설정 이름은 `AI_BASE_URL`이다. vLLM 서버 주소와 구분한다.

## 2. 요청

| 필드 | 자료형 | 필수 | 설명 |
|---|---|---|---|
| `userId` | 양의 정수 | 예 | 백엔드가 인증한 사용자 ID |
| `sessionId` | 비어 있지 않은 문자열 | 예 | 백엔드가 발급한 이번 대화 ID |
| `blocks` | 객체 배열 | 예 | 현재 블록 상태. 블록이 없으면 `[]` |
| `waitingFunds` | 객체 배열 | 예 | 현재 나눌 돈. 없으면 `[]` |

금액·ID에 숫자 문자열이나 실수를 보내지 않는다. `blocks`, `waitingFunds`를 생략하지 않으며,
비어 있는 경우에도 명시적으로 빈 배열을 보낸다. `sessionId`는 불투명한 식별자로 취급하며 값을 바꾸지 않는다.

### 블록 항목

| 필드 | 자료형 | 필수 | 설명 |
|---|---|---|---|
| `blockId` | 양의 정수 | 예 | 블록 식별자 |
| `name` | 비어 있지 않은 문자열 | 예 | 화면에 보이는 블록 이름 |
| `balance` | 정수 | 예 | 원 단위 잔액. 음수 상태도 표현 가능 |
| `type` | 비어 있지 않은 문자열 또는 `null` | 아니오 | 예: `SPENDING`, `GOAL`, `FIXED` |
| `capacity` | 0 이상 정수 또는 `null` | 아니오 | 백엔드가 계산한 기준 금액 |
| `targetAmount` | 0 이상 정수 또는 `null` | 아니오 | 목표 금액 |
| `status` | 비어 있지 않은 문자열 또는 `null` | 아니오 | 예: `SAFE`, `CAUTION`, `DANGER` |
| `statusLabel` | 문자열 또는 `null` | 아니오 | 예: 안전, 주의, 위험 |

### 나눌 돈 항목

| 필드 | 자료형 | 필수 | 설명 |
|---|---|---|---|
| `waitingFundId` | 양의 정수 | 예 | 나눌 돈 식별자 |
| `amount` | 0 이상 정수 | 예 | 원 단위 남은 금액 |
| `label` | 문자열 또는 `null` | 아니오 | 예: 절약분, 정기 입금 |
| `description` | 문자열 또는 `null` | 아니오 | 기존 DTO 호환을 위한 설명 필드 |

기존 `BlockResponse`, `WaitingFundResponse` 객체의 추가 필드를 함께 보내도 된다.
AI 요청 모델이 정의하지 않은 추가 필드는 무시한다. 문구 생성에는 블록 이름·종류·잔액·기준 금액·목표·상태와
나눌 돈의 `label`, `amount`만 사용한다. 사용자·세션·블록·나눌 돈의 ID, `description`, 미정의 추가 필드는 모델에 보내지 않는다.

### 요청 예시

전체 예시는 [`examples/coach_room_request.json`](../examples/coach_room_request.json)에 있다.

```json
{
  "userId": 42,
  "sessionId": "c805ca80-d766-4e64-8b8d-1bca4f77a9a6",
  "blocks": [
    {"blockId": 1, "name": "생활비", "balance": 120000},
    {"blockId": 2, "name": "여행", "balance": 300000, "targetAmount": 500000}
  ],
  "waitingFunds": []
}
```

## 3. 성공 응답

```json
{
  "greeting": "오늘은 생활비와 여행 목표를 함께 살펴볼까요?",
  "suggestions": [
    "내 블록 잔액을 알려줘",
    "여행 목표까지 얼마나 남았어?",
    "생활비 블록 상태를 설명해줘"
  ]
}
```

문구는 예시이며 입력과 모델에 따라 달라진다. 응답 형식은 다음을 보장한다.

- `greeting`: 앞뒤 공백을 제거한 비어 있지 않은 문자열
- `suggestions`: 정확히 3개의 비어 있지 않은 문자열
- 버튼 문구: 앞뒤 공백 제거 후 완전히 같은 문구가 중복되지 않음

`sessionId`는 백엔드가 이미 보유하므로 응답에 다시 넣지 않는다.
백엔드 공통 `ApiResponse` 형식으로 감싸지 않은 AI 내부 응답이다.
백엔드는 앱에 응답할 때 기존 `sessionId`, `greeting`에 `suggestions`를 추가하면 된다.

이 API는 문구의 형식을 검증한다. 문구의 적합성과 모델의 생성 시간은 실제 사용 모델로 확인해야 한다.
버튼 문구를 받았다는 사실은 해당 행동이 실행되었다는 뜻이 아니다.

## 4. 오류와 제한 시간

| HTTP 상태 | `detail.code` | 의미 |
|---|---|---|
| `401` | 없음 | 내부 토큰 누락 또는 불일치. 기존 인증의 문자열 `detail` 사용 |
| `422` | 없음 | 필수 필드, 자료형 또는 값 오류. `loc`, `type`, `msg` 검증 오류 배열 사용 |
| `502` | `COACH_ROOM_PROVIDER_ERROR` | 모델 서버 요청 실패 |
| `502` | `COACH_ROOM_INVALID_RESPONSE` | JSON·문구 개수·중복·빈 본문·완료 상태 등이 유효하지 않음 |
| `503` | `LLM_NOT_CONFIGURED` | 모델 설정을 사용할 수 없음 |
| `503` | 없음 | 서버 간 인증 설정 누락. 기존 인증의 문자열 `detail` 사용 |
| `504` | `COACH_ROOM_TIMEOUT` | 생성 응답 대기 시간 초과 |

생성 오류 응답 예시:

```json
{
  "detail": {
    "code": "COACH_ROOM_TIMEOUT",
    "message": "Room text generation timed out"
  }
}
```

모델 오류의 원문과 내부 추론 텍스트는 클라이언트 오류 응답에 포함하지 않는다.
입력 검증 오류에도 원본 요청의 금융 데이터는 다시 넣지 않고, 잘못된 필드의 위치·종류·설명만 반환한다.
모델 호출 실패를 고정 문구의 `200` 응답으로 대체하지 않으며, 자동 재시도도 수행하지 않는다.
백엔드에서 기본 인사말·버튼을 표시할지는 백엔드 화면 처리에서 결정할 수 있다.

기본 `LLM_TIMEOUT_SECONDS`는 10초다. 백엔드의 현재 AI 채팅 클라이언트는 응답 제한이 15초이므로,
입장 API를 연결할 때도 AI 측 생성 제한보다 여유 있는 값을 사용하고 실제 모델로 시간을 측정한다.
스레드에서 이미 진행 중인 동기 모델 요청은 HTTP 응답 대기가 끝나도 즉시 중단된다고 보장할 수 없다.
AI 서버는 모델 SDK 요청에도 제한 시간을 설정한다.

## 5. 백엔드에서 연결할 위치

기준 코드: Back_end `811dfe55fec86bc68c99a18fe3557d6a6bbed316`.

현재 [`CoachRoomController`](https://github.com/KAU-DoThis/Back_end/blob/811dfe55fec86bc68c99a18fe3557d6a6bbed316/src/main/java/com/dothis/fintris/coach/controller/CoachRoomController.java)의
`GET /api/v1/coach/room`은 새 세션과 인사말을 반환한다.
[`CoachChatService.enter()`](https://github.com/KAU-DoThis/Back_end/blob/811dfe55fec86bc68c99a18fe3557d6a6bbed316/src/main/java/com/dothis/fintris/coach/service/CoachChatService.java)는
고정 인사말을 선택하며 AI 입장 API를 아직 호출하지 않는다.

백엔드 담당자가 연결할 작업은 다음과 같다.

1. 방 입장에서 로그인한 `userId`와 새 `sessionId`를 준비한다.
2. 현재 `blocks`, `waitingFunds`를 조회해 이 AI API에 전송한다.
3. 응답의 `greeting`, `suggestions`를 앱에 전달한다.
4. 앱이 버튼을 누르면 해당 문자열을 기존 메시지 요청의 `text`로 보낸다.

이 저장소의 이번 구현에는 `/internal/ai/coach/chat`이 포함되지 않는다.
따라서 버튼 표시까지 연동한 뒤, 버튼 클릭 후의 실제 채팅은 다음 구현에서 연결해야 한다.

## 6. 세션과 저장 범위

`userId`, `sessionId`는 요청에서 받는 식별자다. LLM이 생성하거나 변경하지 않는다.
현재 방 입장 API는 Agent Inbox에 이벤트를 넣거나 세션·대화·추천 결과를 DB에 저장하지 않는다.
동일한 요청을 다시 보내면 다시 생성하며, 문구가 같다는 보장은 없다.

## 7. 연결 확인

샘플 데이터로 다음 항목을 확인한다.

- 올바른 토큰과 요청으로 `200`, 인사말 한 개, 버튼 세 개를 받는지
- 두 배열이 비어 있을 때도 존재하지 않는 블록을 전제로 하지 않는지
- 잘못된 토큰·요청·모델 오류를 성공 응답으로 처리하지 않는지
- 앱에 전달할 때 백엔드의 `sessionId`가 유지되는지

AI 서버 실행과 직접 호출 방법은 [LLM_SETUP.md](LLM_SETUP.md)를 참고한다.
