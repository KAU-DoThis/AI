"""기존 app/main.py를 수정하기 전 독립 통신 테스트용 서버.

프로젝트 최상위 디렉터리에서 실행:
python -m uvicorn examples.initial_blocks_demo:app --reload --port 8000
"""

from fastapi import FastAPI

from app.api.v1.initial_blocks import router as initial_blocks_router

app = FastAPI(title="Fintris AI initial-blocks integration demo")
app.include_router(initial_blocks_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
