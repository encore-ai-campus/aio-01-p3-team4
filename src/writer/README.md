# writer — 민원 신고글 작성 (담당: 한태경)

유형·지역·세부 정보·신고 창구를 받아 제목·본문·채울 칸·제거한 개인정보 종류를 반환합니다. 레포 루트에서 `cd src/writer` 후 아래 명령으로 실행합니다.
AI 호출 실패 시 대체 모델과 로컬 템플릿을 차례로 사용합니다. 개인정보는 AI 요청 전과 최종 응답에서 제거합니다.

| API·입출력 | 내용 |
|---|---|
| `GET /health` | 서비스 상태를 반환합니다. |
| `POST /run` | 전체 Handoff를 받아 StageResult를 반환합니다. |
| `inputs` | `category`, `region`, `details`, `channel`이 필수입니다. |
| `data` | `title`, `body`, `fill_in`, `removed_pii`입니다. |

[입력 계약](../../contracts/writer_input.schema.json) · [출력 계약](../../contracts/writer.schema.json) · [요청 샘플](../../contracts/samples/writer_input.json)

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

위치·발생 시점이 없으면 `fill_in`에 표시합니다. 개인정보는 정규식으로 지우며, 실제 기관에 제출하지는 않습니다.
`llm_invalid` 장애 주입 시 `retry / LLM_OUTPUT_INVALID`입니다. `attempt >= 3` 또는 `PII_IN_DRAFT`·`DRAFT_MISMATCH` 재작성은 로컬 템플릿을 사용합니다.
테스트는 실제 AI 키와 Redis 연결을 사용하지 않습니다.
