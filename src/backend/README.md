# backend — 총괄 에이전트와 화면 API (담당: 오병훈)

민원을 받아 분류 → 창구 찾기 → 창구 검증 → 문구 작성 → 문구 검증 순서로 에이전트를 부르고, 실패하면 다시 시도하거나 다른 길로 돌아가는 총괄(Supervisor)입니다. 화면이 쓰는 API도 여기서 엽니다.
모든 단계와 장애는 PostgreSQL(`jobs`·`timeline_events`·`incidents`·`user_inputs`)에 남고, 화면의 진행 표시와 운영 현황은 이 기록을 읽습니다.

## 복구 규칙

| 상황 | 총괄이 하는 일 |
|---|---|
| AI 응답 깨짐·서비스 응답 없음 | 같은 단계를 최대 3번 시도. 서비스 다운이면 3초·6초 기다렸다가 다시 부름 (호출 제한 15초) |
| 창구 없음·링크 죽음·비공식 주소 | 동작구 → 서울시 → 전국으로 범위를 넓혀 다시 찾기 (최대 2번) |
| 문구에 개인정보·내용 불일치 | 이유를 붙여 문구 작성을 다시 시킴 |
| 정보 부족 | 사용자에게 추가 질문 (최대 2번), 그래도 부족하면 "확인 필요"와 상위 창구 안내 |
| 공격 문구 | 바로 `failed / INJECTION_BLOCKED`로 막고 장애로 기록 |
| 유형이 여러 개 섞인 설명 | 유형별로 창구 찾기부터 문구 검증까지 병렬 처리, 결과는 유형별로 따로 |

자세한 흐름과 실패 코드는 [설계서 4·7절](../../docs/DESIGN.md#4-일이-흘러가는-순서)에 있습니다.

## API

| Method | Path | 하는 일 |
|---|---|---|
| `POST` | `/jobs` | 민원 접수. 바로 `running`을 돌려주고 뒤에서 처리 (`?wait=true`면 끝까지 기다림) |
| 요청 필드 | `lang` | `ko`(기본) / `en` / `ja` / `zh` / `vi`. 외국어 설명·답변을 한국어로 번역 |
| 요청 필드 | `has_photo` | 엄격한 boolean, 기본 `false`. 사진 보유 여부만 전달하며 파일 업로드 없음 |
| 응답 state | `lang/original_text/has_photo/i18n` | 외국어 원문·사진 여부·표시용 번역(실패 시 생략). `text_ko`는 처리에 사용한 문구이며 발송 초안은 한국어 원본 유지 |
| `GET` | `/jobs/{id}` · `/jobs/{id}/timeline` | 상태·결과 / 단계별 진행 기록 |
| `POST` | `/jobs/{id}/answers` | 추가 질문 답을 받고 이어서 처리 (`?wait=true` 지원) |
| `POST` | `/jobs/{id}/review` | 사람이 고친 제목·본문 첨삭. 문제 구간·이유·수정안 반환 (`index`=유형 번호) |
| `POST` | `/jobs/{id}/send` | 가상 담당자에게 발송. 꼭 고쳐야 할 문제가 남아 있으면 409 |
| `GET` | `/ops/overview?hours=0\|24\|168` · `/metrics` · `/incidents` | 운영 현황 묶음 / 자동 복구율·평균 복구시간 / 장애 기록 |
| `ALL` | `/chaos/{path}` | 장애 스위치(chaos)로 전달 |

`/metrics`는 `METRICS_SINCE`(기본 `2026-10-12T04:26:00Z`) 이후 주입되고 실제 감지된 장애만 집계합니다. 자동 처리율은 사람 개입 없는 복구+차단 ÷ 감지된 주입 장애이며, 평균 시간은 복구 건만 사용합니다. 기존 응답 필드 4개는 유지합니다.
`/incidents`는 모든 기록을 보존하고 각 행의 `metrics_since`로 서버 집계 시작값을 전달합니다. 화면도 이 값을 쓰므로 환경변수를 바꿔도 프론트 재빌드가 필요 없습니다.

## 실행·테스트 (레포 루트 기준)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r src/backend/requirements.txt
uvicorn app.main:app --app-dir src/backend --port 8000
python -m pytest src/backend/tests -m "not integration" -q
```

- 필요한 값: `DATABASE_URL`, `REDIS_URL`. 에이전트 주소는 `<이름>_URL`(예: `CLASSIFIER_URL=http://localhost:8010`)로 바꾸며, 없으면 Compose 서비스 이름(`http://classifier:8000`)을 씁니다.
- 단위 테스트는 DB·에이전트 없이 돌아갑니다. `-m integration` 테스트는 `TEST_DATABASE_URL`(pgvector가 있는 PostgreSQL)이 있을 때만 돌고, 없으면 건너뜁니다.
