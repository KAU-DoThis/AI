# LLM 연결과 로컬 실행

이 서버는 `app/modules/llm`의 공통 인터페이스를 사용한다.
OpenAI 호환 서버에는 `OpenAILLM`, Ollama에는 `OllamaLLM`을 연결한다.
방 입장 서비스가 프롬프트·응답 스키마를 구성하고 기존 동기 `chat()` 호출을 별도 스레드에서 실행한다.

## 1. 설치

프로젝트 루트에서 실행한다.

```powershell
python -m pip install -e ".[test]"
```

Ollama를 사용할 때는 선택 의존성을 함께 설치한다.

```powershell
python -m pip install -e ".[test,ollama]"
```

모델 파일을 이 FastAPI 서버에서 직접 적재하지 않는다.
이미 실행 중인 vLLM 또는 Ollama 서버에 HTTP 요청을 보낸다.

## 2. 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `FINTRIS_INTERNAL_TOKEN` | 없음 | Spring → AI 서버 인증 토큰. 서버 실행에 필요 |
| `LLM_PROVIDER` | `openai` | `openai` 또는 `ollama` |
| `LLM_BASE_URL` | 없음 | 모델 서버 주소 |
| `LLM_MODEL` | 없음 | 모델 서버가 제공하는 정확한 모델 ID |
| `LLM_API_KEY` | 빈 값 | OpenAI 호환 모델 서버 인증키. 비어 있으면 SDK에 `EMPTY` 전달 |
| `LLM_TIMEOUT_SECONDS` | `10` | 0보다 큰 유한한 수. 모델 생성 응답 대기 제한(초) |
| `LLM_MAX_TOKENS` | `512` | 양의 정수. 모델 출력 토큰 제한 |
| `LLM_TEMPERATURE` | `0.4` | 0 이상 2 이하의 유한한 수 |
| `LLM_OUTPUT_MODE` | `json_schema` | `json_schema`, `json_object`, `none` |

모델 주소나 모델명을 설정하지 않으면 방 입장 API를 사용할 수 없고 `503`을 반환한다.
기존 초기 블록·이벤트 수신 API를 위한 서버 실행은 가능하다.
설정 오류를 사용자에게 보여줄 기본 인사말로 숨기지 않는다.

잘못된 Provider 이름, URL 형식, 숫자 설정 등 **명시적으로 입력한 값이 유효하지 않으면 서버 시작을 중단한다.**
모델을 설정한 상태에서 해당 Provider SDK가 설치되지 않은 경우에도 시작 시 오류를 확인할 수 있다.
이는 주소·모델명이 비어 있어 방 입장 기능만 비활성화되는 경우와 구분한다.

`.env.example`을 참고하되, 이 구현은 환경변수를 읽는다.
`.env` 파일을 저장하는 것만으로 로드되지는 않는다. 아래처럼 현재 셸에 설정하거나 실행 환경에서 주입한다.

## 3. vLLM / OpenAI 호환 서버

아래는 같은 PC의 `8001` 포트에 모델 서버가 이미 떠 있다고 가정한 예시다.
실제 모델 서버 주소와 모델 ID로 변경한다. FastAPI는 이 예시에서 `8000` 포트를 사용한다.

```powershell
$env:FINTRIS_INTERNAL_TOKEN = "replace-with-long-random-secret"
$env:LLM_PROVIDER = "openai"
$env:LLM_BASE_URL = "http://127.0.0.1:8001/v1"
$env:LLM_MODEL = "YOUR_SERVED_MODEL_ID"
$env:LLM_API_KEY = "EMPTY"
$env:LLM_TIMEOUT_SECONDS = "10"
$env:LLM_MAX_TOKENS = "512"
$env:LLM_TEMPERATURE = "0.4"
$env:LLM_OUTPUT_MODE = "json_schema"

python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`LLM_BASE_URL`에는 OpenAI 호환 API의 `/v1`까지 넣는다.
SDK가 `/chat/completions`를 이어서 호출하므로 전체 `/v1/chat/completions` 경로를 넣지 않는다.
모델 서버가 인증을 사용하면 `LLM_API_KEY`를 해당 키로 설정한다.

서버가 서로 다른 PC나 컨테이너에 있다면 `127.0.0.1`은 현재 실행 환경 자신을 가리킨다.
AI 서버에서 실제로 접근할 수 있는 모델 서버 주소를 사용한다.

## 4. Ollama

Ollama의 네이티브 API를 사용하므로 주소에 `/v1`을 붙이지 않는다.
이미 받은 모델의 정확한 이름을 `LLM_MODEL`에 설정한다.

```powershell
$env:FINTRIS_INTERNAL_TOKEN = "replace-with-long-random-secret"
$env:LLM_PROVIDER = "ollama"
$env:LLM_BASE_URL = "http://127.0.0.1:11434"
$env:LLM_MODEL = "YOUR_INSTALLED_OLLAMA_MODEL"
$env:LLM_TIMEOUT_SECONDS = "10"
$env:LLM_OUTPUT_MODE = "json_schema"

python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Ollama Provider에서는 `LLM_API_KEY`를 사용하지 않는다.

## 5. 출력 모드와 검증

| 모드 | 모델에 전달할 출력 요청 | AI 서버의 검증 |
|---|---|---|
| `json_schema` | 방 입장 응답의 JSON Schema | 인사말·정확히 세 개의 문구·중복 여부 검사 |
| `json_object` | JSON 객체 출력 | 동일한 응답 모델로 검사 |
| `none` | 프롬프트에 JSON 형식 명시 | 동일한 응답 모델로 검사 |

OpenAI 호환 Provider는 `ChatOptions.extra.request.response_format`,
Ollama Provider는 `ChatOptions.extra.format`으로 출력 형식을 전달한다.
먼저 `json_schema`를 사용하고, 실제 모델 서버가 해당 형식을 지원하는지 확인한다.
서버별 지원 차이로 모드를 바꿀 수 있지만, 형식이 틀린 생성 결과를 정상 응답으로 반환하지 않는다.
JSON이 아닌 응답, 빈 본문, 길이 제한으로 잘린 응답, 문구 개수 오류 등은 실패로 처리한다.

`LLM_MAX_TOKENS`가 너무 작으면 JSON 생성이 끝나기 전에 잘릴 수 있다.
추론을 포함하는 모델은 출력 제한을 사용하는 방식이 다를 수 있으므로, 실제 모델에서 응답 내용과 소요 시간을 확인한다.

## 6. API 직접 호출

서버 실행 후 프로젝트 루트의 다른 PowerShell 창에서 호출한다.

```powershell
$headers = @{ "X-Internal-Token" = "replace-with-long-random-secret" }
$response = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/internal/ai/coach/room" `
  -Headers $headers `
  -ContentType "application/json; charset=utf-8" `
  -InFile "examples/coach_room_request.json"
$response | ConvertTo-Json -Depth 10
```

브라우저에서 <http://127.0.0.1:8000/docs>를 열어 `X-Internal-Token`을 인증한 뒤 같은 요청을 보낼 수도 있다.
실제 연동의 요청·응답 규약은 [COACH_ROOM_API.md](COACH_ROOM_API.md)에 있다.

## 7. 제한 시간과 오류 확인

- `401`: 요청 헤더의 토큰과 서버의 `FINTRIS_INTERNAL_TOKEN`이 같은지 확인한다.
- `422`: `userId`, `sessionId`, 두 목록, 항목별 필수 필드와 숫자 자료형을 확인한다.
- `503` / `LLM_NOT_CONFIGURED`: `LLM_BASE_URL`과 `LLM_MODEL`을 모두 설정했는지 확인한다.
- `502`: 모델 서버 연결, 모델 ID, 출력 형식 지원, 생성 결과를 확인한다.
- `504`: 실제 생성 시간이 제한을 넘었는지 확인한다.

서버는 모델 SDK의 자동 재시도를 사용하지 않는다.
SDK 요청 제한 시간과 방 입장 서비스의 대기 제한을 함께 사용하며,
백엔드의 전체 HTTP 제한 시간은 이 값보다 여유 있게 설정해야 한다.
대기 제한이 끝나는 것이 이미 시작한 동기 요청이나 원격 모델 연산의 즉각적인 중단을 보장하지는 않는다.

실패를 조사할 때도 공유 토큰·모델 API 키·사용자의 원본 금융정보를 로그에 넣지 않는다.

## 8. 검증 범위

```powershell
python -m pytest -q
```

자동 테스트는 실제 모델 서버를 호출하지 않는다. Provider나 HTTP 응답을 대체하여
API 계약·인증·검증·옵션 전달·제한 시간 동작을 확인한다.
실제 서버 연결 확인에서는 모델 ID, 생성 시간, 입력 블록에 맞는 문구, 빈 블록 상태를 확인한다.
실제 모델의 정확도나 생성 시간이 자동 테스트만으로 검증되는 것은 아니다.
