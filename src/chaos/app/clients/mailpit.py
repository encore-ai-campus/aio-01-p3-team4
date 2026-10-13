"""Mailpit 컨테이너만 잠시 중단하고 실제 Docker API로 복구한다."""
import asyncio
import logging
import os
from pathlib import Path

import httpx
from fastapi import HTTPException

SOCKET = "/var/run/docker.sock"
CONTAINER = os.getenv("MAILPIT_CONTAINER", "minwon-mailpit-1")
lock = asyncio.Lock()
tasks = set()


async def command(action):
    async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=SOCKET), timeout=10) as client:
        url = f"http://docker/containers/{CONTAINER}/{action}"
        response = await (client.get(url) if action == "json" else client.post(url, params={"t": 1}))
        response.raise_for_status()
        return response


async def prepare(flag):
    if not Path(SOCKET).is_socket():
        raise HTTPException(503, "Docker socket unavailable")
    if tasks:
        raise HTTPException(409, "Mailpit outage already active")
    try:
        seconds = float(flag.params.get("seconds", 20))
        if not 0 < seconds <= 60:
            seconds = 60 if seconds > 60 else 20
    except (TypeError, ValueError):
        raise HTTPException(422, "seconds must be a number") from None
    try:
        running = (await command("json")).json()["State"]["Running"]
    except (httpx.HTTPError, KeyError, ValueError):
        raise HTTPException(503, "Mailpit Docker control unavailable") from None
    if not running:
        raise HTTPException(409, "Mailpit is already stopped")
    return seconds


async def restore_after(seconds):
    try:
        await asyncio.sleep(seconds)
    finally:
        await command("start")
        logging.getLogger(__name__).warning("Mailpit started")


async def stop(seconds):
    try:
        await command("stop")
    except BaseException:
        # stop 응답을 잃어도 Mailpit을 꺼진 채로 남기지 않는다.
        await command("start")
        raise
    task = asyncio.create_task(restore_after(seconds))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    logging.getLogger(__name__).warning("Mailpit stopped seconds=%s", seconds)
