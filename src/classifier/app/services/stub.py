"""이전 import 경로와의 호환용 진입점. 샘플 반환 대신 실제 에이전트를 실행한다."""
from app.services.classification import run

__all__ = ["run"]
