# mailer — 가상 민원 메일 발송 (담당: 문태웅)

신고 창구와 문구를 받아 가상 메일을 보내고 접수번호를 반환합니다. 레포 루트에서 `docker compose up -d mailpit mailer`로 실행합니다.
발송에 실패하면 편지함에 저장해 다시 보냅니다. 성공 뒤에는 접수번호가 적힌 가상 회신을 보냅니다.
릴레이를 설정하면 가상 메일·회신 복사본을 실제 주소 한 곳으로도 보냅니다. 실패해도 가상 흐름과 접수번호에는 영향이 없습니다.
복사본은 백그라운드에서 각각 최대 3번 시도합니다. 실제 기관에는 전달하지 않습니다.

| 입력·출력 / API | 내용 |
|---|---|
| 입력 | [Handoff](../../contracts/handoff.schema.json)의 `inputs`: [mailer 입력](../../contracts/mailer_input.schema.json) |
| 출력 | [StageResult](../../contracts/stage_result.schema.json)의 `data`: [mailer 결과](../../contracts/mailer.schema.json) |
| `GET /health` | 서비스 상태를 반환합니다. |
| `POST /run` | 신고 메일 발송 결과와 접수번호를 반환합니다. |
| `GET /outbox` | 최근 발송 기록을 `items` 배열로 반환합니다. 본문은 제외합니다. |

## 실행·테스트

Compose의 API 주소는 `http://127.0.0.1:8060`이며, Mailpit 메일함은 `http://127.0.0.1:8025`입니다.
직접 실행할 때는 Mailpit을 먼저 띄운 뒤 레포 루트에서 다음 명령을 실행합니다.

```bash
docker compose up -d mailpit
cd src/mailer
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
SMTP_HOST=127.0.0.1 uvicorn app.main:app --port 8060
python -m pytest -q  # 다른 터미널에서 같은 venv로 실행합니다.
```

## 환경변수

- SMTP: `SMTP_HOST`(기본 `mailpit`), `SMTP_PORT`(기본 `1025`)입니다. `REDIS_URL`은 장애 플래그 조회에 쓰입니다.
- 릴레이: `MAIL_RELAY_HOST`, `MAIL_RELAY_PORT`(기본 `587`), `MAIL_RELAY_USER`, `MAIL_RELAY_PASSWORD`, `MAIL_RELAY_TO`입니다. HOST·USER·PASSWORD·TO를 모두 설정하면 STARTTLS로 보냅니다. `MAIL_RELAY_TIMEOUT_SEC` 기본값은 `8`초입니다.
- 주소: `MAIL_SENDER`(기본 `minwon-guide@minwon.example`), `MAIL_CITIZEN`(기본 `citizen@minwon.example`), `CONTACTS_CSV`입니다. 기본 주소록은 [가상 담당자 CSV](../../database/seeds/mock_contacts.csv)입니다.
- 저장·회신: `OUTBOX_PATH`(기본 `/tmp/minwon_mailer_outbox.json`), `OUTBOX_INTERVAL_SEC`(기본 `5`)입니다. `CONTRACTS_DIR`은 계약 폴더 경로입니다.

- `.example` 주소만 허용합니다. 다른 주소는 SMTP 연결 전에 `MAIL_BLOCKED`로 차단합니다. 실제 기관에는 보내지 않습니다.
- 발송을 3번 시도한 뒤 실패하면 `retry`·`MAIL_SEND_FAILED`·`queued`를 반환합니다. 이미 보낸 `task_id`는 다시 보내지 않습니다.
- 기본 편지함 파일은 컨테이너를 삭제하면 사라집니다. 여러 프로세스가 같은 파일을 공유하는 용도로 쓰지 않습니다.
