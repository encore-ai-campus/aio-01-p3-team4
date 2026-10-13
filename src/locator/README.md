# locator — 공식 민원 창구 추천 (담당: 최강현)

민원 유형과 지역을 받아 공식 창구를 최대 3개 추천하고, 동작구 창구에는 해당 동 주민센터를 최대 2개 더 안내합니다. 레포 루트에서 Docker Compose로 실행하며 기본 접속 주소는 `http://localhost:8020`입니다.
동작구 → 서울시 → 전국 순으로 찾습니다. 동작구 밖이거나 지역이 불명확하면 전국 창구를 안내합니다.

| API·계약 | 내용 |
|---|---|
| `GET /health` | 프로세스 상태를 반환합니다. DB 연결은 검사하지 않습니다. |
| `POST /run` | [Handoff](../../contracts/handoff.schema.json)(총괄이 에이전트에게 보내는 일 요청 JSON)를 받고 [StageResult](../../contracts/stage_result.schema.json)(에이전트가 돌려주는 결과 JSON)를 반환합니다. |
| [입력](../../contracts/locator_input.schema.json) | `category`는 유형, `region`은 지역, `exclude_channel_ids`는 추천에서 제외할 창구 ID 목록입니다. |
| [출력 data](../../contracts/locator.schema.json) | `channels`는 추천 창구이며, `fallback_level`은 찾은 범위(`district`=동작구, `city`=서울시, `national`=전국)입니다. |

## 실행·테스트

레포 루트에서 시작합니다. 로컬 PostgreSQL·Redis와 locator를 함께 시작합니다.
```bash
docker compose --profile local up -d --build postgres redis locator
docker compose exec locator python -m app.seed_channels --validate-only
cd src/locator
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

## 환경변수

| 이름 | 용도 |
|---|---|
| `DATABASE_URL` | 필수입니다. [DB 스키마](../../database/schema.sql)가 설치된 PostgreSQL에 연결합니다. |
| `REDIS_URL` | 장애 플래그용입니다. 비우면 Redis 없이 실행합니다. |
| `SEEDS_DIR` | 운영 CSV 폴더입니다. 기본은 컨테이너 `/seeds`, 로컬 `database/seeds`입니다. |
| `CONTRACTS_DIR` | `contracts/`(서비스끼리 주고받는 JSON 모양을 정한 스키마 폴더)의 경로입니다. 기본은 로컬 `contracts`, 컨테이너 `/contracts`입니다. |
| `LOCATOR_INCLUDE_PHONE` | 기본 `true`입니다. `false`이면 전화 창구를 제외합니다. |
| `LOCATOR_TEST_DATABASE_URL` | 별도 DB 테스트용입니다. 미설정 시 DB 테스트를 생략합니다. |

시작 시 빈 `channels` 테이블에만 [운영 CSV](../../database/seeds/README.md)를 적재합니다. DB·스키마·CSV가 잘못되면 기동에 실패합니다.
추천이 없으면 `NO_CHANNEL`, 요청 중 DB 장애면 HTTP 503을 반환합니다.
레포 루트에서 `docker compose exec locator python -m app.seed_channels --upsert`로 CSV 내용을 DB에 반영합니다. 같은 ID의 창구는 활성 여부(`active`)도 CSV 값으로 바뀝니다.
