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
│       └── initial_blocks.py
├── core/
│   ├── __init__.py
│   └── internal_auth.py
├── models/
│   ├── __init__.py
│   ├── agent_event.py      # EventType, AgentEvent, EventReceipt
│   └── agent_status.py     # AgentStatus
└── schemas/
    ├── __init__.py
    └── initial_blocks.py
```

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

세 내부 API는 `X-Internal-Token` 헤더가 필요하다. 백엔드가 전송할
초기 블록 데이터의 정식 필드명은 팀과 합의 전이므로 아직 협의용 DTO다.

## 중요한 제약

- **LLM, 실제 거래 분류, DB 기록, 사용자 기억 저장은 아직 없다.**
- `/internal/v1/initial-blocks`는 UserAgent를 생성하거나 이벤트를 Inbox에 넣지 않는다.
- Agent는 한 프로세스의 메모리에만 존재하고 재시작 시 초기화된다.
- `uvicorn --workers`를 여러 개 띄우면 각 프로세스별로 별개 AgentManager가 생기므로
  사용자당 전역 1개 Agent가 보장되지 않는다. 공유 큐/스토리지는 이후 단계에서 필요하다.
- 기존 `models.py`의 클래스는 **Pydantic API 모델**이지 SQLAlchemy DB 모델이 아니다.
  요청대로 `models/`로 옮겼지만, 나중에 ORM이 추가되면 `schemas/`로 옮기는 것도 고려할 수 있다.
- 실제 금융정보 전체를 API 로그에 출력하지 않도록 유지한다.
