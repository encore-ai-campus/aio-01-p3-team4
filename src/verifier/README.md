# verifier — 민원 창구와 문구 검사 (담당: 문태웅)

신고 창구와 문구를 검사하고 `PASS`·`RETRY`·`FAIL`을 반환합니다. 아래 명령은 레포 루트에서 시작하며 접속 주소는 `http://localhost:8030`입니다.
`mode=channel`에서 창구가 모두 탈락하면 `FAIL`, `mode=draft`에서 문구에 문제가 있으면 `RETRY`를 반환합니다. 확인일만 오래된 창구는 `PASS`로 통과시키며, `checks`에 확인일 검사 결과와 `reasons`에 `STALE_INFO` 경고를 남깁니다.

| API·계약 | 내용 |
|---|---|
| `GET /health` | 프로세스 상태를 반환합니다. |
| `POST /run` | [Handoff](../../contracts/handoff.schema.json)(총괄이 에이전트에게 보내는 일 요청 JSON)를 받고 [StageResult](../../contracts/stage_result.schema.json)(에이전트가 돌려주는 결과 JSON)를 반환합니다. |
| [입력](../../contracts/verifier_input.schema.json) | `mode`, `category`, `region`과 창구 또는 문구를 받습니다. |
| [출력 data](../../contracts/verifier.schema.json) | `verdict`, `checks`, `reasons`, `passed_channel_ids`입니다. |

창구 모드는 공식 도메인·링크·확인일·관할·유형을 검사합니다. 전화 창구는 번호 형식과 `source_url`을 검사합니다.
문구 모드는 개인정보·유형 키워드·지역·비공식 링크를 검사합니다. 개인정보 검사 결과에는 값 대신 종류만 남깁니다.

## 실행·테스트 (레포 루트에서 시작합니다)

```bash
cd src/verifier
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8030
python -m pytest -q  # 다른 터미널에서 같은 폴더·venv로 실행합니다.
```

테스트는 미리 정한 HTTP 응답과 Redis 대역을 사용해 실제 외부 연결 없이 실행합니다.

## 환경변수

| 이름 | 용도 |
|---|---|
| `REDIS_URL` | 장애 플래그용입니다. 비우면 Redis 없이 실행합니다. |
| `CONTRACTS_DIR` | `contracts/`(서비스끼리 주고받는 JSON 모양을 정한 스키마 폴더)의 경로입니다. 기본은 로컬 `contracts`, 컨테이너 `/contracts`입니다. |

창구 실패 코드는 `NOT_OFFICIAL`·`LINK_DEAD`입니다. `STALE_INFO`는 확인일이 90일을 넘은 창구의 경고입니다.
문구 실패 코드는 `PII_IN_DRAFT`·`DRAFT_MISMATCH`입니다. 통과한 창구만 `passed_channel_ids`에 남깁니다.
