# seeds — 창구 초기 데이터와 점검 설정 (담당: 최강현)

공식 창구와 가상 메일 수신자 CSV를 보관합니다. 아래 명령은 레포 루트에서 실행합니다.
locator는 창구 CSV를 `channels` 표에 넣고, 점검기는 링크 상태를 DB에 반영합니다. 잠깐 연결에 실패하면 기존 값을 그대로 둡니다.

| CSV | 역할 | 실제 소비 서비스·도구 |
|---|---|---|
| `channels.csv`, `integrated_channels.csv` | 동작구·서울시·전국 공식 창구 | locator가 두 파일 모두 빈 DB에 최초 적재합니다. |
| `mock_contacts.csv` | `channel_id`별 가상 부서·담당자·메일 주소 | mailer가 수신자를 조회합니다. |
| `integration/channel_check_schedule.csv` | 점검 주기·재시도 간격·HTTP 제한 | `scripts/check_channels.py`를 실행할 때 인자로 넘깁니다. |
| `reference/complaint_routing/dongjak_*.csv` | 동작구 행정동 목록과 동별 관할 | classifier·writer가 동 이름을 확인하고, locator·verifier가 관할을 맞춥니다. |

창구 DB 구조는 [schema.sql](../schema.sql)에, locator 출력은 [계약](../../contracts/locator.schema.json)에 정의되어 있습니다. 계약 파일은 `contracts/`(서비스끼리 주고받는 JSON 모양을 정한 스키마 폴더)에 있습니다.

## 적재·점검·테스트

[데이터 서버 설정](../../deploy/data/README.md)을 마친 뒤, `channels` 표가 있는 DB의 `DATABASE_URL`을 환경변수로 설정합니다.

```bash
python -m pip install -r src/locator/requirements.txt
# CSV 형식 확인: DB 연결 없이 실행합니다.
(cd src/locator && python -m app.seed_channels --validate-only)
# 빈 channels 표에 초기 데이터를 넣습니다.
(cd src/locator && python -m app.seed_channels)
# DB 링크를 한 번 점검하고 channels.csv와 같은 폴더의 integrated_channels.csv로 나눠 내보냅니다.
python scripts/check_channels.py --schedule-csv database/seeds/integration/channel_check_schedule.csv --once --export database/seeds/channels.csv
# 프로세스를 유지하며 정기 점검합니다.
python scripts/check_channels.py --schedule-csv database/seeds/integration/channel_check_schedule.csv
(cd src/locator && python -m pytest tests/test_catalog.py tests/test_check_channels.py -q)
```

`SEEDS_DIR`은 locator의 CSV 폴더를 바꿉니다. 기본값은 저장소의 이 폴더이며 컨테이너에서는 `/seeds`입니다.
mailer는 `CONTACTS_CSV`로 주소록 경로를 지정합니다. Docker 기본값은 `/seeds/mock_contacts.csv`입니다.

- 이미 데이터가 있으면 기본 적재는 건너뜁니다. `--upsert`는 같은 ID의 값과 활성 상태를 덮어쓰므로 주의가 필요합니다.
- 웹·앱 URL이 404·410이면 창구를 비활성화합니다. 전화는 `manual_review`(사람이 확인해야 함)로 남기고 확인일을 자동 갱신하지 않습니다.
- 정기 점검은 회차 종료 후 24시간, 실패 후 1시간을 기다립니다. cron 같은 운영체제 스케줄러에 자동 등록하지 않으므로 실행 중인 점검은 Ctrl+C로 멈춥니다.
