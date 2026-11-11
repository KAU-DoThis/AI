# workers — Jeff·sLM 큐 워커

EC2의 AI 서버는 Jeff·sLM을 직접 부르지 않는다. 요청 큐에 넣고 답장 큐에서 기다린다.
이 폴더는 **모델이 있는 PC(집 PC·GPU 서버)에서** 큐를 꺼내 모델에 묻고 답장을 넣는 쪽이다.
모델 PC는 포트를 열지 않는다. EC2 이미지에는 들어가지 않는다(`.dockerignore`).

```
EC2 AI 서버 (app/modules/model_queue) ─▶ fintris-dev-jeff-requests ─▶ workers.jeff ─▶ jeff-serve
                                      ─▶ fintris-dev-slm-requests  ─▶ workers.slm  ─▶ OpenAI 호환 sLM (vLLM 등)
                 ◀────────────── fintris-dev-agent-replies ◀──────────────┘
```

편지 양식은 서버와 같은 `app/modules/model_queue/messages.py`를 쓴다.
기한이 지난 요청은 워커가 버리고, 모델 오류는 `ok=false` 답장으로 바로 알린다.

## 준비
1. 레포 맨 위에서 `pip install -r workers/requirements.txt`
2. `workers/.env.example` → `workers/.env` 복사 후 채우기 (`.env`는 깃에 안 올림)
3. AWS 자격 증명: `aws configure --profile <이름>` 후 `.env`의 `AWS_PROFILE`에 그 이름.
   권한은 자기 요청 큐 꺼내기·지우기 + 답장 큐 넣기만.

## Jeff 워커
jeff-serve를 먼저 켜고(기본 `http://127.0.0.1:8765`), 레포 맨 위에서:
```
python -m workers.jeff
```
- payload: `{"state": {...}, "options"?, "instructions"?, "model"?}` → 결과 `{"choice", "probabilities", "confidence", "model"}`
- options·instructions를 안 보내면 `JEFF_PROMPT_FILE`(학습 때 쓴 prompt yaml)을 쓴다.
- `JEFF_MODEL`: 학습한 어댑터 이름. 비우면 기본 모델.

## sLM 워커
```
python -m workers.slm
```
- 서버는 `{"name", "merchant_type"?}`만 보내고 `{"category_code", "prompt"}`를 받는다. 선택지 안인지는 서버가 다시 본다.
- 프롬프트(v1·v2·v3, `SLM_PROMPT`)·sLM 주소·키는 **워커 쪽에만** 있다.
- sLM을 바꿀 때는 `.env`의 `SLM_URL`(`.../v1`까지)·`SLM_MODEL`·`SLM_API_KEY`만 바꾼다. OpenAI 호환이어야 한다.
  모델이 바뀌면 정확도·답 형식을 다시 잰다.

## 멈추기
Ctrl+C (받는 중인 요청을 끝내고 최대 20초 안에 멈춘다)

## 테스트
레포 맨 위에서 `python -m pytest tests/test_workers.py -q` (가짜 SQS, AWS 불필요)
