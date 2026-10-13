# chaos — 장애 스위치 (담당: 이원민)

장애를 일부러 켜고 끄는 서비스입니다. 에이전트별 장애 플래그를 Redis에 저장해 두면 그 에이전트가 다음 호출에서 실제 AI 호출·링크 검사·메일 발송에 그 장애를 걸고, 실패를 알아챈 에이전트나 총괄이 복구합니다. 서비스 다운·DB 끊김은 흉내가 아니라 `scripts/chaos_infra.sh`로 실제로 냅니다.

| API | 하는 일 |
|---|---|
| `PUT /flags/{target}` | 장애 켜기. 본문 `{incident_id, kind, injected_at, params}` |
| `GET /flags/{target}` | 켜진 장애 보기 |
| `DELETE /flags/{target}` | 장애 끄기 |
| `POST /run` | 레드팀: 허용된 조합 중 AI가 하나를 골라 `{target, kind, reason}`을 돌려주고 그 장애를 켭니다 |

- 대상: backend·classifier·locator·verifier·writer·mailer
- 장애 종류: classifier·writer의 `llm_invalid`, writer의 `pii_leak`, verifier의 `link_dead`·`stale_info`·`not_official`, mailer의 `mail_down`, 공통 `delay`. 공격 문구(`injection`)는 플래그 없이 실제 공격 문장을 질문으로 넣습니다. 각 장애가 어떻게 일어나는지는 [설계서 8절](../../docs/DESIGN.md#8-실제-호출에-장애를-주입하는-방법)에 있습니다.

## 실행·테스트 (레포 루트 기준)

```bash
cd src/chaos
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8050
python -m pytest -q        # 다른 터미널에서 같은 venv로
```

플래그 API에는 `REDIS_URL`, 레드팀에는 `OPENAI_API_KEY`·`OPENAI_MODEL`이 필요합니다. 테스트는 Redis 없이 돌아갑니다.
