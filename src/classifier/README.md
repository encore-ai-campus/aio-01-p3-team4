# classifier — 민원 유형·지역 분류 (담당: 한태경)

민원 설명과 추가 답변을 받아 유형·지역·추가 질문을 반환합니다. 레포 루트에서 `cd src/classifier` 후 아래 명령으로 실행합니다.
서울 동작구의 가로등 고장·도로 파손·쓰레기 무단투기를 분류합니다. AI 호출 실패 시 대체 모델과 로컬 규칙을 차례로 사용합니다.

| API·입출력 | 내용 |
|---|---|
| `GET /health` | 서비스 상태를 반환합니다. |
| `POST /run` | 전체 Handoff를 받아 StageResult를 반환합니다. |
| `inputs` | `text`는 필수이며 `answers`는 선택입니다. |
| `data` | `category`, `confidence`, `region`, `details`, `missing`, `questions`입니다. |

[입력 계약](../../contracts/classifier_input.schema.json) · [출력 계약](../../contracts/classifier.schema.json) · [요청 샘플](../../contracts/samples/classifier_input.json)

## 실행·테스트 (레포 루트 기준)

```bash
cd src/classifier
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8010
python -m pytest -q  # 다른 터미널에서 같은 폴더·venv로 실행합니다.
```

## 환경변수 (레포 루트 `.env`)

| 이름 | 기본값·용도 |
|---|---|
| `OPENAI_API_KEY` | 빈 값이면 외부 호출 없이 로컬 규칙으로 분류합니다. |
| `OPENAI_MODEL` / `OPENAI_FALLBACK_MODEL` | `gpt-6-luna` / `gpt-5.4-mini`입니다. |
| `LLM_TIMEOUT_SECONDS` / `LLM_BUDGET_SECONDS` | 개별 호출 10초 / 전체 복구 13초이며 각각 상한값입니다. |
| `KAKAO_REST_KEY` | 있으면 카카오 로컬 API로 장소의 실제 지역을 확인합니다. 비우면 함께 들어 있는 역 데이터만 씁니다. |
| `PLACE_LOOKUP_TIMEOUT_SECONDS` | 장소 확인 제한 시간입니다. 기본·상한 1.5초입니다. |
| `REDIS_URL` | 선택 사항이며 장애 플래그를 확인합니다. |

유형이 불확실하거나 위치가 부족하면 `needs_user / NEEDS_MORE_INFO`입니다. 동작구라고 했는데 말한 장소가 다른 지역이면 위치를 한 번 되묻습니다. 구 안에서 동이 다르면 확인된 장소의 동으로 접수할지 한 번 묻고, '네'면 그 동으로 바로잡습니다. 지원 밖 지역·유형은 `failed / OUT_OF_SCOPE`입니다.
새 위치 답변이 있으면 지역·위치는 답변에서만 읽습니다. 긍정 답은 원문을 다시 확인해 동 교정에만 사용합니다. 답변의 장소도 어긋나면 한 번 더 되묻고, 반복 제한은 총괄이 맡습니다.
추가 답변은 `answers`의 `q_category`·`q_location_detail`에 넣습니다. 동 교정 확인 답은 `q_confirm_location`에 넣고 기존 위치 답을 유지합니다. 확인 질문의 `field`는 `location_detail`이므로 총괄은 질문 `id`를 보존해야 합니다. 이전 방식의 `location_detail: "네 맞아요"`도 긍정 답으로 처리합니다.
도메인 교정·대체 모델 후에도 규칙으로 넘어가거나 예산이 소진되면 incident와 서버가 측정한 recovery 기록을 남깁니다. 확인 답 경로는 기존 대체 모델부터 호출하고 지도 조회를 병행하며, `PLACE_DONG` 재검사를 생략합니다. 일반 분류의 모델 순서·예산은 유지합니다.
`llm_invalid` 장애 주입 시 `retry / LLM_OUTPUT_INVALID`입니다.
테스트는 실제 AI 키와 Redis 연결을 사용하지 않습니다.
