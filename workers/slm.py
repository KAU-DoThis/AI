"""sLM 워커: slm-requests 큐 → OpenAI 호환 sLM (지금 수업 Qwen, 나중에 승현 vLLM) → agent-replies 큐.

프롬프트·주소·키는 워커 쪽에만 둔다. 서버(fintris-ai slm_stage)는 가맹점 이름·업종만 보내고 코드 하나만 받는다.
vLLM으로 바꿀 때는 SLM_URL·SLM_MODEL만 바꾸고 정확도·답 형식을 다시 잰다.

실행:  python -m workers.slm      (설정은 환경변수나 같은 폴더의 .env)
"""
from __future__ import annotations

import json
import os
import re
import signal
import urllib.error
import urllib.request

from . import worker
from .jeff import load_env

_HINTS = {
    "FOOD": "식재료·마트·편의점",
    "DELIVERY_DINING": "식당·배달·술집",
    "CAFE": "카페·디저트·간식",
    "TRANSPORT": "대중교통·택시·주유·차량",
    "HOUSING": "월세·관리비·공과금",
    "TELECOM": "휴대폰·인터넷",
    "LIVING": "생필품·세탁·미용·꽃",
    "SHOPPING": "의류·신발·전자제품·화장품",
    "LEISURE": "영화·운동·게임·숙박·항공",
    "HEALTH": "병원·약국",
    "SUBSCRIPTION": "정기 구독 서비스",
    "ETC": "그 밖의 소비",
}
# v1 (~11/07): "추측하지 말 것" 때문에 침구·안경·냉난방기처럼 단서가 있는 이름에도 UNKNOWN (11/08 측정) → 비교용으로만 남김
_SYSTEM_V1 = (
    "카드 결제 가맹점 이름을 보고 소비 카테고리 하나를 고른다. 아래 코드 중 하나만 JSON으로 답한다. "
    '형식: {"category_code":"코드"}. 이름만으로 알 수 없으면 UNKNOWN. 추측하지 말 것.\n'
    + "\n".join(f"{code}: {_HINTS[code]}" for code in _HINTS)
    + "\nUNKNOWN: 이름만으로 알 수 없음"
)
# v2 (11/08): 단서가 하나라도 있으면 가장 가까운 칸을 고르고, UNKNOWN은 단서가 전혀 없을 때만.
# 설명은 BE 카테고리 이름(식비·생활/생필품·쇼핑/의류 …)의 뜻을 넓힌 것. 시험지 업종 목록을 보고 맞춘 게 아님
_HINTS_V2 = {
    "FOOD": "식재료를 사는 곳: 마트·슈퍼·편의점·정육점·과일·반찬·수산·농산·식품",
    "DELIVERY_DINING": "음식을 먹는 곳: 식당·배달·술집·구내식당·뷔페·분식·치킨",
    "CAFE": "카페·베이커리·디저트·아이스크림·떡·간식",
    "TRANSPORT": "대중교통·택시·주유·충전·주차·렌터카·자동차 정비와 용품",
    "HOUSING": "월세·관리비·전기·가스·수도 요금",
    "TELECOM": "휴대폰·인터넷 요금, 통신사",
    "LIVING": "생활에 쓰는 물건과 서비스: 생필품·잡화·주방·침구·가구·철물·세탁·수선·수리·청소·미용·꽃·반려동물",
    "SHOPPING": "사는 물건: 의류·신발·가방·안경·시계·귀금속·전자제품·가전·컴퓨터·휴대폰 기기·화장품·책·문구·악기·중고품",
    "LEISURE": "즐기는 것: 영화·공연·전시·운동·헬스·요가·골프·게임·노래방·숙박·여행·항공",
    "HEALTH": "병원·약국·한의원·의료기기·건강식품",
    "SUBSCRIPTION": "정기 구독 서비스 (OTT·음악·소프트웨어)",
    "ETC": "위에 없는 소비 (단서는 있지만 맞는 칸이 없을 때)",
}
_SYSTEM_V2 = (
    "카드 결제 가맹점 이름을 보고 소비 카테고리 하나를 고른다. 아래 코드 중 하나만 JSON으로 답한다. "
    '형식: {"category_code":"코드"}.\n'
    "이름에 무엇을 파는지 짐작할 단서(음식·물건·서비스를 뜻하는 말, 알려진 회사·브랜드)가 하나라도 있으면 "
    "가장 가까운 카테고리를 고른다. 딱 맞는 칸이 없으면 가장 가까운 칸을 고른다.\n"
    "UNKNOWN은 사람 이름·지명·뜻 없는 말뿐이라 무엇을 파는지 전혀 짐작할 수 없을 때만 쓴다.\n"
    + "\n".join(f"{code}: {_HINTS_V2[code]}" for code in _HINTS_V2)
    + "\nUNKNOWN: 단서가 전혀 없음"
)
# v3 (11/08): v2 개발 시험지에서 식당을 FOOD로, 숙박을 HOUSING으로 보냄 → 경계를 분명히
_HINTS_V3 = {
    **_HINTS_V2,
    "FOOD": "장보기·식재료를 사는 곳: 마트·슈퍼·편의점·정육점·청과·반찬가게·식품점 (음식점은 여기 아님)",
    "DELIVERY_DINING": "조리된 음식을 사 먹는 곳: 식당(한식·중식·일식·양식·면·탕·스시·누들 등)·배달·술집·구내식당·뷔페·분식·치킨",
    "HOUSING": "사는 집의 월세·관리비·전기·가스·수도 요금 (호텔·모텔·스테이 같은 숙박은 LEISURE)",
    "LEISURE": "즐기는 것: 영화·공연·전시·운동·헬스·요가·골프·게임·노래방·숙박(호텔·모텔·펜션·스테이)·여행·항공",
}
_SYSTEM_V3 = _SYSTEM_V2.split("UNKNOWN은")[0] + (
    "음식 이름이 들어간 가게는 식당이다 → FOOD가 아니라 DELIVERY_DINING.\n"
    "UNKNOWN은 사람 이름·지명·뜻 없는 말뿐이라 무엇을 파는지 전혀 짐작할 수 없을 때만 쓴다.\n"
    + "\n".join(f"{code}: {_HINTS_V3[code]}" for code in _HINTS_V3)
    + "\nUNKNOWN: 단서가 전혀 없음"
)
PROMPTS = {"v1": _SYSTEM_V1, "v2": _SYSTEM_V2, "v3": _SYSTEM_V3}
DEFAULT_PROMPT = "v3"
_CODE = re.compile(r'"category_code"\s*:\s*"([A-Z_]+)"')


def make_handler(url: str, api_key: str, model: str, timeout: float = 30.0, prompt: str = DEFAULT_PROMPT, post=None):
    """payload = {"name": 가맹점 이름, "merchant_type"?: 업종} → {"category_code": 코드 또는 None, "prompt"}

    코드가 선택지 안인지는 서버가 다시 본다 (워커 답을 그대로 믿지 않음).
    """
    post = post or _post
    url = url.rstrip("/")
    system = PROMPTS[prompt]
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def handle(payload: dict) -> dict:
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("payload.name 이 없음")
        user = f"가맹점: {name}"
        if payload.get("merchant_type"):
            user += f"\n업종: {payload['merchant_type']}"
        body = {"model": model, "temperature": 0, "max_tokens": 400,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": f"{user}\n/no_think"}]}
        text = post(url + "/chat/completions", body, headers, timeout)["choices"][0]["message"].get("content") or ""
        found = _CODE.findall(text)
        return {"category_code": found[-1] if found else None, "prompt": prompt}

    return handle


def _post(url: str, body: dict, headers: dict, timeout: float) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode()
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:  # 키가 담긴 헤더는 오류 글에 넣지 않는다
        raise RuntimeError(f"sLM {error.code}") from None
    except (urllib.error.URLError, ConnectionError, TimeoutError) as error:
        raise RuntimeError(f"sLM 연결 실패: {type(error).__name__}") from None


def main() -> None:
    load_env()
    import boto3

    queue_url = os.environ["SLM_REQUESTS_URL"]
    handler = make_handler(os.environ["SLM_URL"], os.environ.get("SLM_API_KEY", ""), os.environ["SLM_MODEL"],
                           float(os.environ.get("SLM_TIMEOUT", "30")), os.environ.get("SLM_PROMPT", DEFAULT_PROMPT))
    sqs = boto3.Session(profile_name=os.environ.get("AWS_PROFILE") or None,
                        region_name=os.environ.get("AWS_REGION", "ap-northeast-2")).client("sqs")
    stopping = []
    signal.signal(signal.SIGINT, lambda *_: (stopping.append(1), print("멈추는 중… (최대 20초)")))
    worker.run(sqs, queue_url, {"slm": handler}, stop=lambda: bool(stopping))
    print("멈춤")


if __name__ == "__main__":
    main()
