FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir "fastapi>=0.115,<1" "uvicorn>=0.34,<1" "pydantic>=2.10,<3" "rapidfuzz>=3.10,<4" "boto3>=1.34,<2"

COPY app ./app

# root로 실행하지 않음
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
