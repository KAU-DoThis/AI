import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    app_env: str
    log_level: str
    question_threshold: float
    fuzzy_min_score: int
    aws_region: str = "ap-northeast-2"
    jeff_requests_url: str = ""  # 비면 4단(Jeff) 꺼짐
    model_replies_url: str = ""
    jeff_timeout: float = 5.0
    jeff_cooldown: float = 60.0
    jeff_ask_threshold: float = 0.2  # ask 확률이 이 값 이상이면 사용자에게 묻는다
    slm_requests_url: str = ""  # 비면 sLM 꺼짐 → 사전이 모르는 거래는 질문. 주소·키·프롬프트는 sLM 워커 쪽
    slm_timeout: float = 10.0
    slm_cooldown: float = 60.0


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app_env=os.getenv("APP_ENV", "local"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        question_threshold=float(os.getenv("CLASSIFY_QUESTION_THRESHOLD", "0.7")),
        fuzzy_min_score=int(os.getenv("CLASSIFY_FUZZY_MIN_SCORE", "90")),
        aws_region=os.getenv("AWS_REGION", "ap-northeast-2"),
        jeff_requests_url=os.getenv("JEFF_REQUESTS_URL", ""),
        model_replies_url=os.getenv("MODEL_REPLIES_URL", ""),
        jeff_timeout=float(os.getenv("JEFF_TIMEOUT", "5")),
        jeff_cooldown=float(os.getenv("JEFF_COOLDOWN", "60")),
        jeff_ask_threshold=float(os.getenv("JEFF_ASK_THRESHOLD", "0.2")),
        slm_requests_url=os.getenv("SLM_REQUESTS_URL", ""),
        slm_timeout=float(os.getenv("SLM_TIMEOUT", "10")),
        slm_cooldown=float(os.getenv("SLM_COOLDOWN", "60")),
    )
