# Fintris AI Server — 폴더 분리 및 통합본

기존에 제공된 네 파일(`__init__.py`, `main.py`, `manager.py`, `models.py`)을 대상으로,
**원래 동작은 유지하면서** 디렉터리별로 옮긴 통합본이다. 이전에 만든 초기 블록 수신
API(`/internal/v1/initial-blocks`)도 함께 묶었다.

## 새 구조

```text
app/
├── __init__.py
├── main.py                  # 앱 생성 및 라우터 등록만 담당
├── agent/
│   ├── __init__.py
│   ├── manager.py          # AgentManager
│   └── user_agent.py       # UserAgent
├── api/
│   ├── __init__.py
│   ├── dependencies.py     # 기존 프로젝트 파일: 이 ZIP에는 넣지 않음
│   └── v1/
│       ├── __init__.py
│       ├── agent_events.py # 기존 이벤트 수신/상태 조회 API
│       ├── initial_blocks.py
│       └── classify.py     # 거래 분류 입구 (services만 부른다)
├── core/
│   ├── __init__.py
│   ├── internal_auth.py
│   └── classify_config.py  # 분류 설정 (환경변수)
├── services/
│   └── classify.py         # 분류기 준비, 분류, 첫 연동(분류 + 반복지출)
├── modules/
│   ├── classifier/         # 거래 분류 (규칙·사전·오타·Jeff·sLM) + 가맹점 사전 data/merchants.csv
│   ├── analysis/           # 반복지출(고정지출 블록 후보) 찾기
│   └── model_queue/        # Jeff·sLM을 SQS 큐로 부르는 클라이언트
├── models/
│   ├── __init__.py
│   ├── agent_event.py      # EventType, AgentEvent, EventReceipt
│   └── agent_status.py     # AgentStatus
└── schemas/
    ├── __init__.py
    ├── initial_blocks.py
    └── classify.py          # 분류 요청·응답 형태
```

역할: `schemas` = 주고받는 데이터 형태, `modules` = 기능 단위 모듈, `services` = 모듈 기능을 서버에서 쓰는 곳,
`api` = 요청을 받아 services를 부르는 얇은 입구.

### 파일 이동 / 삭제

- 기존 `app/manager.py` 내용 → `app/agent/manager.py`, `app/agent/user_agent.py`
- 기존 `app/models.py` 내용 → `app/models/agent_event.py`, `app/models/agent_status.py`
- 기존 `app/main.py` 안의 Agent API → `app/api/v1/agent_events.py`
- 새 `app/main.py`는 두 라우터 등록 (`agent_events`, `initial_blocks`)

**주의:** `app/manager.py`, `app/models.py`는 백업한 뒤 제거한다. 특히 `app/models.py`와
`app/models/` 폴더가 동시에 남아 있으면 import 혼란이 생길 수 있다.
다른 모듈에서 `from app.manager import ...`를 사용한다면
`from app.agent.manager import AgentManager`로 변경한다. `app.models`의
기존 네 이름은 `app/models/__init__.py`를 통해 계속 import할 수 있다.

**기존 파일 보존:** `app/api/dependencies.py`, 그 외 본인 프로젝트 파일은 이 묶음에 포함하지 않는다.
이미 수정한 `__init__.py` 내용은 직접 병합하고 무조건 덮어쓰지 않는다.

## 설치 및 실행

프로젝트 루트에서 실행한다 (Python 3.11+, Pydantic 2.x):

```powershell
python -m pip install -e ".[test]"
$env:FINTRIS_INTERNAL_TOKEN = "replace-with-long-random-secret"
python -m uvicorn app.main:app --reload --port 8000
```

API 문서: `http://127.0.0.1:8000/docs` / 상태 조회: `/health`

```powershell
python -m pytest -q
```

## 현재 API와 의미

| 메서드 | 경로 | 현재 동작 |
|---|---|---|
| GET | `/health` | 서버 헬스체크 |
| POST | `/internal/v1/agent/events` | 유저 Agent 생성/재사용, 메시지 Inbox 대기 (`202`) |
| GET | `/internal/v1/agents/{user_id}` | 메모리 속 Agent 상태 조회 |
| POST | `/internal/v1/initial-blocks` | 사용자/거래내역 입력 검증 후 단순 수신 확인 (`200`) |
| GET | `/internal/v1/categories` | 카테고리 코드 16개 (BE 시드와 같음) |
| POST | `/internal/v1/classify` | 거래 분류 (1~1000건) |
| POST | `/internal/v1/classify/initial` | 첫 계좌 연동 3개월 분류 + 반복지출 후보 (1~5000건) |

내부 API는 `X-Internal-Token` 헤더가 필요하다. 백엔드가 전송할
초기 블록 데이터의 정식 필드명은 팀과 합의 전이므로 아직 협의용 DTO다.

## 중요한 제약

- **Agent LLM, DB 기록, 사용자 기억 저장은 아직 없다.** 거래 분류는 규칙·사전 기준으로 동작하고,
  Jeff·sLM은 큐 주소가 있을 때만 켜진다.
- `/internal/v1/initial-blocks`는 UserAgent를 생성하거나 이벤트를 Inbox에 넣지 않는다.
- Agent는 한 프로세스의 메모리에만 존재하고 재시작 시 초기화된다.
- `uvicorn --workers`를 여러 개 띄우면 각 프로세스별로 별개 AgentManager가 생기므로
  사용자당 전역 1개 Agent가 보장되지 않는다. 공유 큐/스토리지는 이후 단계에서 필요하다.
- 기존 `models.py`의 클래스는 **Pydantic API 모델**이지 SQLAlchemy DB 모델이 아니다.
  요청대로 `models/`로 옮겼지만, 나중에 ORM이 추가되면 `schemas/`로 옮기는 것도 고려할 수 있다.
- 실제 금융정보 전체를 API 로그에 출력하지 않도록 유지한다.

## 거래 분류

계좌·카드 거래를 받아 **소비인지, 무엇에 썼는지, 사용자에게 물어볼지**를 붙여 돌려준다.
첫 계좌 연동(3개월)에서는 **반복지출(고정지출 블록 후보)**까지 찾는다. DB 접근·금액 계산은 하지 않고,
결과 저장은 Spring 백엔드가 한다.

### 부르기
```powershell
curl -X POST http://127.0.0.1:8000/internal/v1/classify `
  -H "Content-Type: application/json" -H "X-Internal-Token: $env:FINTRIS_INTERNAL_TOKEN" `
  -d '{"transactions":[{"transactionId":1,"occurredAt":"2026-10-10T12:30:00+09:00","amount":4500,"direction":"OUT","merchantOrPayee":"스타벅스 강남점","accountType":"CARD","isCanceled":false,"isOwnAccountTransfer":false}]}'
```
결과(요약): `{"results":[{"transactionId":1,"categoryCode":"CAFE","excluded":false,"needsQuestion":false,"confidence":0.9}]}`

- JSON 칸 이름은 camelCase. 정의되지 않은 칸은 거절하지 않고 받자마자 버린다(로그에는 칸 이름만).
- 요청: `transactionId`, `occurredAt`(`+09:00` 포함), `amount`(양수), `direction`(`OUT`/`IN`), `merchantOrPayee`,
  `merchantType`(카드 업종), `accountType`(`CARD`/`BANK`), `isCanceled`, `isOwnAccountTransfer`
- 응답(거래마다): `categoryCode`, `classifyStage`, `confidence`, `status`, `excluded`(소비 아님),
  `needsQuestion`·`questionType`·`questionOptions`(사용자에게 물을지·무슨 질문·답 후보)
- `/classify/initial`은 `recurring`을 더 준다: 고정지출 블록 후보 하나마다 이름, `categoryCode`,
  `rule.matchCondition`(받는 사람·금액 범위·날짜 ±일), 근거 문장, 속한 거래 ID.
  반복지출에 들어간 거래는 질문하지 않는다(블록 후보가 대신 보여준다).

### 분류 순서
1. 규칙: 본인 이체·입금·카드대금·현금 인출·적금 → 소비 제외, 간편결제만 찍힌 건 → 질문
2. 사전(`app/modules/classifier/data/merchants.csv`): 브랜드·업종어로 카테고리
3. 오타 매칭: 한글 자모 비교 (배달의민죡 → 배달의민족)
4. 사전에 없는 은행 출금: 1만 원 이상이면 송금 질문
5. Jeff → sLM: 사전이 모르는 카드 거래. 큐 주소가 없으면 건너뛰고 질문으로 보낸다

### 반복지출
받는 사람·날짜(±3일)·금액(±10%)이 **3개월 연속 한 달에 한 번** 맞으면 고정지출 블록 후보로 본다.
집주인 계좌 이체는 통장에 이름만 찍히므로, 가려진 사람 이름에게 매달 20만 원 이상(임시값)이면 `월세`(`HOUSING`)로 본다.

### Jeff·sLM (SQS 큐)
서버는 모델을 직접 부르지 않는다. 요청 큐에 넣고 답장 큐에서 기다린다. 모델 PC는 포트를 열지 않고 큐에서 꺼내 간다.
시간 안에 답이 없거나 실패하면 사용자 질문으로 처리하고, 잠시(`*_COOLDOWN`초) 그 단계를 건너뛴다.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `CLASSIFY_QUESTION_THRESHOLD` | `0.7` | 신뢰도가 이보다 낮으면 질문 |
| `CLASSIFY_FUZZY_MIN_SCORE` | `90` | 오타 매칭 기준 점수 |
| `AWS_REGION` | `ap-northeast-2` | 큐 리전 |
| `JEFF_REQUESTS_URL` | (빈 값) | Jeff 요청 큐. 비면 Jeff 꺼짐 |
| `SLM_REQUESTS_URL` | (빈 값) | sLM 요청 큐. 비면 sLM 꺼짐 |
| `MODEL_REPLIES_URL` | (빈 값) | Jeff·sLM 공용 답장 큐 |
| `JEFF_TIMEOUT` / `SLM_TIMEOUT` | `5` / `10` | 답 기다리는 시간(초) |
| `JEFF_COOLDOWN` / `SLM_COOLDOWN` | `60` / `60` | 실패 후 건너뛰는 시간(초) |
| `JEFF_ASK_THRESHOLD` | `0.2` | Jeff가 질문으로 보낼 확률 문턱 |

### Docker
```powershell
docker build -t fintris-agent .
docker run -p 8000:8000 -e FINTRIS_INTERNAL_TOKEN=... fintris-agent
```

### 협의 중
- 분류 경로(`/internal/v1/classify`)와 JSON 칸 이름 형식(camelCase / snake_case)
- 첫 연동 때 백엔드가 부를 입구 (`/classify/initial` 또는 `/initial-blocks`)
