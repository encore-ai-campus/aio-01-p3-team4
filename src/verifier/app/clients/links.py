import logging
import httpx

TRANSPORT: httpx.AsyncBaseTransport | None = None


async def check_link(client, url) -> tuple[bool, str]:
    try:
        response = await client.head(url)
        status = response.status_code
        if status >= 400:
            async with client.stream("GET", url) as response:
                status = response.status_code
        return status < 400, f"응답 {status}"
    except httpx.HTTPError as exc:
        logging.getLogger(__name__).warning("링크 연결 실패 error_type=%s", type(exc).__name__)
        return False, f"연결 실패({type(exc).__name__})"
