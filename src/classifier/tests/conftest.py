import pytest

from app.core import config


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    # 개발자의 실제 키나 데이터 서버를 테스트에서 사용하지 않는다.
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "KAKAO_REST_KEY", "")
    monkeypatch.setattr(config, "REDIS_URL", "")
