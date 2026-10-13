from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.clients.chaos import Kind, Target

ALLOWED: dict[Target, frozenset[Kind]] = {
    'backend': frozenset(),
    'classifier': frozenset({'llm_invalid', 'delay'}),
    'locator': frozenset({'delay'}),
    'verifier': frozenset({'link_dead', 'stale_info', 'not_official', 'delay'}),
    'writer': frozenset({'llm_invalid', 'pii_leak', 'delay'}),
    'mailer': frozenset({'mail_down', 'delay'}),
}


class Candidate(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    target: Target
    kind: Kind

    @model_validator(mode='after')
    def supported_pair(self) -> 'Candidate':
        if self.kind not in ALLOWED[self.target]:
            raise ValueError('Unsupported target and kind combination')
        return self


class RedteamRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    candidates: list[Candidate] = Field(
        default_factory=lambda: [Candidate(target=t, kind=k) for t, kinds in ALLOWED.items() for k in sorted(kinds)
                                 if (t, k) != ('mailer', 'delay')],  # UI는 mail_down일 때만 발송한다.
        min_length=1, max_length=14)


class Selection(Candidate):
    reason: str = Field(min_length=1, max_length=500, strict=True)

    @model_validator(mode='after')
    def nonblank_reason(self) -> 'Selection':
        if not self.reason.strip():
            raise ValueError('Reason must not be blank')
        return self


class RedteamResult(Selection):
    incident_id: str
    injected_at: AwareDatetime
