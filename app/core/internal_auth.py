"""Spring -> AI 내부 API용 간단한 공유 토큰 인증.

기존 app/api/dependencies.py를 덮어쓰지 않도록 별도 파일에 둔다.
운영에서는 내부 네트워크 허용 정책, HTTPS 또는 적절한 전송 보호도 적용한다.
"""

import os
from secrets import compare_digest

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

_internal_token_header = APIKeyHeader(name="X-Internal-Token", auto_error=False)


def require_internal_token(
    supplied_token: str | None = Security(_internal_token_header),
) -> None:
    expected_token = os.getenv("FINTRIS_INTERNAL_TOKEN")
    if not expected_token:
        # 실수로 인증 없이 작동하는 상황을 방지한다.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal API authentication is not configured",
        )

    if supplied_token is None or not compare_digest(supplied_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal service token",
        )
