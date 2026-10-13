from types import TracebackType
from typing import Self

import pytest

from app.services import flags as service


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.consume_on_write = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value
        if self.consume_on_write:
            self.values.pop(key)

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> MemoryRedis:
    monkeypatch.setattr(service, 'REDIS_URL', 'redis://test:6379')
    redis = MemoryRedis()
    monkeypatch.setattr(service.Redis, 'from_url', lambda *args, **kwargs: redis)
    return redis


