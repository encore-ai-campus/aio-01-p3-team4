import json
from pathlib import Path
from typing import Literal
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.core.config import CONTRACTS

SCHEMAS = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in CONTRACTS.glob("*.schema.json")}
REGISTRY = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in SCHEMAS.values())

def validate(name: str, value: dict) -> dict:
    schema = SCHEMAS[name + ".schema.json"]
    errors = list(Draft202012Validator(schema, registry=REGISTRY, format_checker=FormatChecker()).iter_errors(value))
    if errors:
        raise ValueError(errors[0].message)
    return value

class Handoff(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    task_id: str
    from_agent: str = Field(alias="from")
    to: str
    objective: str
    inputs: dict
    constraints: list[str]
    attempt: int = Field(ge=1)
    last_failure: str | None
    memory_refs: list[str]

    @model_validator(mode="after")
    def contract(self):
        validate("handoff", self.model_dump(by_alias=True))
        return self

class StageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    task_id: str
    author: str
    status: Literal["success", "retry", "failed", "needs_user"]
    data: dict
    failure: str | None
    incident_id: str | None

    @model_validator(mode="after")
    def contract(self):
        validate("stage_result", self.model_dump())
        return self
