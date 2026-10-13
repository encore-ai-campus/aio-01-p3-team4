from typing import Literal

from fastapi import Request
from fastapi.responses import JSONResponse


class SelectionError(Exception):
    def __init__(self, status_code: Literal[502, 503, 504], detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class CorruptFlagError(Exception):
    pass


async def redis_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={'detail': 'Redis unavailable'})


async def corrupt_flag_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={'detail': 'Stored chaos flag is invalid'})


async def selection_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, SelectionError):
        raise exc
    return JSONResponse(status_code=exc.status_code, content={'detail': exc.detail})
