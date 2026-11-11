import pytest
from fastapi.testclient import TestClient

from app.modules.classifier.dictionary import MerchantDictionary
from app.modules.classifier.pipeline import Classifier

TEST_TOKEN = "test-only-internal-token"


@pytest.fixture(scope="session")
def classifier() -> Classifier:
    return Classifier(MerchantDictionary.load(), question_threshold=0.7, fuzzy_min_score=90)


@pytest.fixture()
def client(monkeypatch):
    """분류 API용. 내부 API라 X-Internal-Token 헤더를 기본으로 붙인다."""
    monkeypatch.setenv("FINTRIS_INTERNAL_TOKEN", TEST_TOKEN)
    from app.main import app

    with TestClient(app, headers={"X-Internal-Token": TEST_TOKEN}) as c:
        yield c
