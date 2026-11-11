# 기존 저장소에 연결하기

## 1. 파일을 안전하게 추가

ZIP을 저장소 안에 바로 덮어 풀지 말고 별도 폴더에 풀어둔다.

```powershell
# 압축을 푼 추가 코드 폴더에서 실행한다.
python apply_chat_addon.py C:\Users\ssh15\OneDrive\Documents\ai_fintris --check
python apply_chat_addon.py C:\Users\ssh15\OneDrive\Documents\ai_fintris
```

기존 파일과 내용이 다른 경로가 하나라도 있으면 스크립트는 **아무 파일도 복사하지 않고 중단**한다.
충돌 파일은 로컬 코드에 맞게 병합해야 한다. 이 스크립트는 `main.py`를 자동 편집하지 않는다.

## 2. main.py imports

기존 room 라우터, 분류기, LLM 초기화는 그대로 유지한다.

```python
import os
from app.agent.chat_manager import ManagerLimits
from app.api.v1.coach_chat import build_coach_chat_router
from app.core.chat_factory import build_chat_manager
from app.modules.llm import ChatOptions
```

`authenticate_internal`은 현재 프로젝트가 내부 API에서 사용하는 인증 dependency를 그대로 사용한다.
과거 구조는 `app/core/internal_auth.py`에 있었으나 현재 이름/위치는 실제 로컬 코드를 기준으로 한다.
라우터 생성 함수에 이를 전달하므로 이 추가 파일은 인증 함수의 import 경로를 가정하지 않는다.

## 3. 기존 lifespan의 AgentManager 생성 부분 교체

아래는 **연결 예시**다. 최신 `main.py`를 읽지 못했으므로 `llm`은 기존 room 서비스에 전달하는
실제 LLM 객체 변수명으로 바꿔야 한다. 새 provider나 SDK를 다시 만들지 않는다.
기존 `app.state.agent_manager = AgentManager()`는 제거/교체하고 **두 매니저를 동시에 만들지 않는다.**

기존 LLM 초기화가 끝난 뒤, 기존 `yield` 앞에 추가한다.

```python
provider_name = os.environ.get("LLM_PROVIDER", "openai").lower()
chat_extra = {}
if provider_name == "openai":  # OpenAI-compatible vLLM 포함
    chat_extra["request"] = {
        "timeout": float(os.environ.get("CHAT_LLM_TIMEOUT_SECONDS", "45"))
    }

chat_options = ChatOptions(
    temperature=0.3,
    max_tokens=1024,
    extra=chat_extra,
)

chat_manager = build_chat_manager(
    llm=llm,  # 현재 lifespan에서 room에 넘기는 실제 LLM 인스턴스
    model=os.environ["LLM_MODEL"],
    options=chat_options,
    limits=ManagerLimits(
        idle_seconds=float(os.environ.get("CHAT_IDLE_SECONDS", "1800")),
        turn_timeout_seconds=float(os.environ.get("CHAT_TIMEOUT_SECONDS", "90")),
    ),
)
app.state.agent_manager = chat_manager
await chat_manager.start()
```

기존 `yield`는 하나만 유지한다. 기존 `try/finally`가 있으면 종료 구문만 병합한다.

```python
try:
    yield
finally:
    await chat_manager.aclose()
    # 기존 LLM/분류기/HTTP client 정리 코드는 이 다음에 그대로 유지한다.
```

Ollama 사용 시 provider 생성 코드에서 HTTP timeout을 유한하게 설정해야 한다.
위의 OpenAI 전용 `extra.request.timeout`을 Ollama에 그대로 넘기지 않는다.
기존 room용 JSON schema/response_format을 채팅 옵션에 복사하지 않는다.
채팅은 자유 텍스트 + tool calling이며, room의 greeting/suggestions 전용 출력과 다르다.
SDK 자동 재시도 설정도 기존 프로젝트 정책을 확인한다. 이 매니저는 LLM/도구를 자동 재실행하지 않는다.

## 4. 라우터 추가

`app = FastAPI(...)` 아래 기존 라우터 등록 부분에 한 줄을 추가한다.

```python
app.include_router(build_coach_chat_router(authenticate_internal))
```

이 라우터 내부에 `/internal/ai/coach` prefix가 이미 있으므로 외부에서 같은 prefix를 더하지 않는다.
기존 `/room`, 초기 분류, 이벤트 라우터는 삭제하지 않는다.
`agent_events`의 dependency도 같은 `app.state.agent_manager`를 반환하는지 확인한다.
타입 힌트가 구형 AgentManager를 가리키면 새 `app.agent.chat_manager.AgentManager`로 갱신한다.

기존 이벤트 엔드포인트의 `accept/get_status/get_agent`는 유지된다. 이벤트가 자동으로 채팅을
실행하도록 바꾸지는 않았다. 동일 메시지를 `/agent/events`와 `/chat` 양쪽으로 중복 전송하지 않는다.

## 5. .env와 실행

기존 `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`를 그대로 사용한다.
`FINTRIS_INTERNAL_TOKEN`은 백엔드 → AI 인증용, `LLM_API_KEY`는 AI → LLM 인증용으로 다르다.
추가 설정은 선택 사항이다.

```dotenv
CHAT_IDLE_SECONDS=1800
CHAT_TIMEOUT_SECONDS=90
CHAT_LLM_TIMEOUT_SECONDS=45
```

```powershell
python -m pytest tests/chat_addon -q
# 그 다음 현재 저장소의 기존 전체 테스트도 실행한다.
python -m pytest -q
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --env-file .env
```

`--env-file`은 기존 환경의 python-dotenv/uvicorn 설정을 사용한다. 외부 접근 범위는 네트워크에서 제한한다.
실행 후 Swagger에 `POST /internal/ai/coach/chat`이 보이는지 확인한다.
`examples/chat_request.ps1`은 실제 내부 API로 요청을 보낸다. 토큰을 파일에 저장하지 않는다.

## 로컬 Codex에 적용을 맡길 때

```text
현재 저장소에 fintris_chat_addon의 채팅 기능을 통합해줘.
먼저 실제 main.py, dependencies, internal_auth, core/llm과 기존 테스트를 확인해.
room/분류 API와 기존 LLM 모듈은 유지하고 새 SDK나 provider를 중복 구현하지 마.
app.state.agent_manager는 하나만 두고 새 factory로 구성해.
/chat router에는 기존 내부 인증 dependency를 전달해.
모든 엔진 코드는 modules/agent에 두며 manager가 SimpleAgent에 직접 의존하면 안 돼.
기존 lifespan의 startup/shutdown을 보존하고 매니저를 LLM client보다 먼저 정리해.
충돌 파일은 덮어쓰지 말고 병합해. 새 테스트와 기존 전체 테스트를 실행해.
실제 backend 쓰기 도구/DB/메모리는 추가하지 마. 자동 commit/push하지 마.
변경 파일과 테스트 결과, 실제 LLM 연결 미검증 여부를 보고해.
```
