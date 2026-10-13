import asyncio
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core import config
from app.core.errors import SelectionError
from app.schemas.redteam import RedteamRequest, Selection


class Message(BaseModel):
    content: str = Field(min_length=1)


class Choice(BaseModel):
    finish_reason: Literal['stop']
    message: Message


class Completion(BaseModel):
    choices: list[Choice] = Field(min_length=1, max_length=1)


async def choose(body: RedteamRequest) -> Selection:
    if not config.OPENAI_API_KEY:
        raise SelectionError(503, 'Redteam model is not configured')
    try:
        async with asyncio.timeout(10), httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.post(
                'https://api.openai.com/v1/chat/completions',
                headers={'Authorization': f'Bearer {config.OPENAI_API_KEY}'},
                json={
                    'model': config.OPENAI_MODEL,
                    'messages': [
                        {'role': 'system', 'content': 'Choose exactly one supplied candidate for a resilience test. Return target, kind and a brief reason. Do not execute actions.'},
                        {'role': 'user', 'content': body.model_dump_json()},
                    ],
                    'response_format': {'type': 'json_schema', 'json_schema': {
                        'name': 'chaos_selection', 'strict': True,
                        'schema': Selection.model_json_schema(),
                    }},
                },
            )
            response.raise_for_status()
        completion = Completion.model_validate_json(response.content)
        return Selection.model_validate_json(completion.choices[0].message.content)
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise SelectionError(504, 'Redteam model timed out') from exc
    except (httpx.HTTPError, ValidationError) as exc:
        raise SelectionError(502, 'Redteam model request or response failed') from exc
