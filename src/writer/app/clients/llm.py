"""OpenAI Responses API 호출. 응답의 구조·내용 검사는 서비스가 담당한다."""
import json
import logging

import httpx

from app.core import config


def enabled() -> bool:
    return bool(config.OPENAI_API_KEY.strip())


async def generate(prompt: str, inputs: dict, model: str, *, break_output: bool = False) -> str:
    request = {
        "model": model, "instructions": prompt,
        "input": "다음 json 데이터를 읽고 json 객체로 답한다:\n" + json.dumps(inputs, ensure_ascii=False),
        "text": {"format": {"type": "json_object"}}, "store": False,
    }
    if break_output:
        request.pop("text")
        request["reasoning"] = {"effort": "none"}
        request["instructions"] = "이번 응답은 JSON·중괄호·코드블록을 절대 쓰지 말고 한국어 산문 두 문장으로만 답한다."
    logging.getLogger(__name__).warning("AI 호출 model=%s break_output=%s", model, break_output)
    async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT_SECONDS) as client:
        response = await client.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"}, json=request,
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or payload.get("status") != "completed":
        raise ValueError("AI 응답이 완료되지 않았습니다")
    parts = [
        item["text"]
        for output in payload.get("output", [])
        if output.get("type") == "message"
        for item in output.get("content", [])
        if item.get("type") == "output_text"
    ]
    if not parts:
        raise ValueError("AI 응답에 본문이 없습니다")
    return "".join(parts)
