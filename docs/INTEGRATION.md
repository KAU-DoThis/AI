# Fintris 초기 블록 요청 수신 API — 통합 가이드

> 이 문서는 초기 블록 **수신 확인 API**의 과거 통합 안내입니다. 현재 프로젝트 설치·실행은 [README](../README.md), 새 방 입장 API는 [COACH_ROOM_API.md](COACH_ROOM_API.md)를 참고하세요. 아래 ZIP 패치 설명은 당시 배포 방식에 대한 기록입니다.

## 이번 구현 범위

**Spring → FastAPI로 사용자/거래 데이터를 전송하고, 인증 + 입력 검증 후 HTTP 200 수신 확인 응답까지.**

- 초기 거래 자동 분류: **아직 없음**
- UserAgent 생성/메시지 큐 적재: **아직 연결하지 않음**
- AI DB 저장: **아직 없음**
- vLLM 호출: **아직 없음**
- 중복 요청 영구 기록, 작업 재시도: **아직 없음**
- POST 요청의 입력 데이터는 응답 후 메모리에 보존하지 않음

## 내 프로젝트 구조와 통합

이 압축 파일은 **추가 파일만 포함한 패치**다. 사용자 프로젝트의 `app/main.py`, `app/api/dependencies.py`, `app/api/v1/router.py`, `app/core/config.py`를 **덮어쓰지 않는다.**

```text
app/
├── api/
│   ├── dependencies.py          # 기존 파일 유지
│   └── v1/
│       └── initial_blocks.py    # 새 파일
├── schemas/
│   └── initial_blocks.py        # 새 파일
└── core/
    └── internal_auth.py         # 새 파일
```

기존 `app/main.py`에서 FastAPI 앱 생성 이후 다음 두 줄을 **추가**한다.

```python
from app.api.v1.initial_blocks import router as initial_blocks_router
app.include_router(initial_blocks_router)
```

`app`이라는 FastAPI 객체를 생성한 뒤 `include_router()`를 호출해야 한다. 이미 `app/api/v1/router.py`에서 버전별 Router들을 묶는다면 거기서 `include_router()`를 해도 되지만 **양쪽에 동시에 등록하지 않는다**. 이 라우터의 prefix는 `/internal/v1/initial-blocks`이므로 외부 prefix를 또 붙이지 않는다.

기존 `app/api/dependencies.py`는 오늘 단계에서 수정할 필요 없다. 인증 dependency는 독립적인 `app/core/internal_auth.py`의 `require_internal_token()`을 사용한다. 나중에 공통 인증을 정리할 때 `dependencies.py`로 이동/재노출하면 된다.

## 설치 및 실행

프로젝트 루트에서:

```bash
python -m pip install 'fastapi>=0.115,<1' 'uvicorn>=0.34,<1'
```

Windows PowerShell:

```powershell
$env:FINTRIS_INTERNAL_TOKEN = "local-dev-token-change-me"
python -m uvicorn app.main:app --reload --port 8000
```

최초 독립 통신 테스트라면 기존 `main.py`를 수정하지 않고 다음처럼 실행해도 된다:

```powershell
python -m uvicorn examples.initial_blocks_demo:app --reload --port 8000
```

AWS에서는 `--reload`를 빼고 실행하고, Spring 백엔드가 접근 가능한 사설 네트워크에서 서비스한다. 서버 간 공유 토큰은 비밀 값으로 환경변수에 저장하고 Git에 커밋하지 않는다. AI 서버의 8000 포트는 가능한 Spring 서버에서만 접근할 수 있게 제한한다.

## API 계약 (협의용 초안)

```http
POST /internal/v1/initial-blocks
X-Internal-Token: <공유 토큰>
Content-Type: application/json
```

요청 예시: `examples/initial_blocks_request.json`

```json
{
  "request_id": "init-42-001",
  "user": {"user_id": 42, "nickname": "테스트사용자"},
  "transactions": [
    {
      "transaction_id": 501,
      "linked_account_id": 11,
      "occurred_at": "2026-09-25T09:30:00+09:00",
      "amount": 600000,
      "direction": "OUT",
      "merchant_or_payee": "김민수"
    }
  ]
}
```

**필수 입력**: `request_id`, `user.user_id`, `transactions` (리스트). 각 거래는 `transaction_id`, `occurred_at`(ISO 8601), `amount`(원 단위 양의 정수), `direction`(`IN`/`OUT`)가 필수.

`accounts`, `existing_blocks`, `categories`는 선택 입력이다. 거래 계좌 식별자는 현재 `linked_account_id`를 표준으로 사용하되 이전 예시의 `account_id`도 수신한다. Spring의 실제 DTO에 맞춰 **변수명과 입출금 표현을 백엔드 팀과 확정해야 한다.** 미정의 추가 속성은 임시로 허용한다.

성공 응답 (`200 OK`):

```json
{
  "status": "received",
  "request_id": "init-42-001",
  "user_id": 42,
  "transaction_count": 1
}
```

상태 코드는 잘못된 인증 401, 서버 인증 설정 누락 503, 형식 오류 422이다. `received`는 **분류 완료나 DB 영구 저장을 뜻하지 않는다.**

## PowerShell에서 보내기

서버에서 `$env:FINTRIS_INTERNAL_TOKEN`에 설정한 것과 같은 값을 클라이언트 헤더로 보낸다.

```powershell
$headers = @{ 'X-Internal-Token' = 'local-dev-token-change-me' }
Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:8000/internal/v1/initial-blocks' `
  -Headers $headers -ContentType 'application/json' `
  -InFile 'examples/initial_blocks_request.json'
```

## 테스트

```bash
python -m pip install pytest httpx
python -m pytest tests/test_initial_blocks_api.py -q
```

## 백엔드 팀과 정할 사항

1. 계좌 식별자는 `linked_account_id`로 통일할지, API DTO에서 다른 명칭을 쓸지
2. 거래 금액과 입출금 방향(`amount` 양수 + `IN/OUT`)을 이 형태로 합의할지
3. 최초 계좌 연동 후 거래와 기본 카테고리, 블록 상태를 어떤 필드로 전달할지
4. 향후 긴 분석 작업에 대해 즉시 200 vs 작업 접수 202, 결과 전달 방식
5. 요청 재전송/중복 처리와 AI DB 저장 시점

**Spring 원본 DB와 AI 전용 개인화 DB의 역할은 분리하고, 지금은 실제 사용자 금융정보 대신 가상 거래로 연동 테스트한다.**
