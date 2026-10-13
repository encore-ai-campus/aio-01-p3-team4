# writer — 민원 신고글 작성 (담당: 한태경)

유형·지역·세부 정보·신고 창구를 받아 제목·본문·채울 칸·제거한 개인정보 종류를 반환합니다. 아래 명령은 레포 루트에서 시작합니다.
AI 호출 실패 시 대체 모델과 로컬 템플릿을 차례로 사용합니다. 개인정보는 AI 요청 전과 최종 응답에서 제거합니다.

| API·입출력 | 내용 |
|---|---|
| `GET /health` | 서비스 상태를 반환합니다. |
| `POST /run` | `Handoff`(총괄이 에이전트에게 보내는 일 요청 JSON)를 받아 `StageResult`(에이전트가 돌려주는 결과 JSON)를 반환합니다. |
| `inputs` | `category`, `region`, `details`, `channel`이 필수입니다. |
| `data` | `title`, `body`, `fill_in`, `removed_pii`입니다. |

[입력 계약](../../contracts/writer_input.schema.json) · [출력 계약](../../contracts/writer.schema.json) · [요청 샘플](../../contracts/samples/writer_input.json)은 `contracts/`(서비스끼리 주고받는 JSON 모양을 정한 스키마 폴더)에 있습니다.

## 실행·테스트 (레포 루트 기준)

```bash
cd src/writer
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8040
python -m pytest -q  # 다른 터미널에서 같은 폴더·venv로 실행합니다.
```

## 환경변수 (레포 루트 `.env`)

| 이름 | 기본값·용도 |
|---|---|
| `OPENAI_API_KEY` | 빈 값이면 외부 호출 없이 로컬 템플릿으로 작성합니다. |
| `OPENAI_MODEL` / `OPENAI_FALLBACK_MODEL` | `gpt-6-luna` / `gpt-5.4-mini`입니다. |
| `LLM_TIMEOUT_SECONDS` / `LLM_BUDGET_SECONDS` | 개별 호출 10초 / 전체 복구 13초이며 각각 상한값입니다. |
| `REDIS_URL` | 선택 사항이며 장애 플래그를 확인합니다. |

위치·발생 시점이 없으면 `fill_in` 배열에 칸 이름(`label`)과 입력 예시(`placeholder`)를 넣습니다. 개인정보는 정규식으로 지우며, 실제 기관에 제출하지는 않습니다.
`llm_invalid`는 AI가 JSON 모양에 맞지 않는 답을 하도록 유도하는 장애 시험입니다. 응답이 깨지면 대체 모델·로컬 템플릿으로 복구합니다. 총괄이 세 번째 이상 호출하거나(`attempt >= 3`), 개인정보(`PII_IN_DRAFT`)·내용 불일치(`DRAFT_MISMATCH`) 때문에 다시 쓸 때는 로컬 템플릿을 사용합니다.
테스트는 실제 AI 키와 Redis 연결을 사용하지 않습니다.
