# frontend — 시민 화면과 관리자 화면 (담당: 문태웅)

민원을 넣고 안내를 받는 **시민 화면**(`/`)과, 처리 현황과 장애·복구를 지켜보는 **관리자 화면**(`/admin/`)을 담은 React 앱입니다.
레포 루트에서 `cd src/frontend` 후 `npm ci`, `npm run dev`로 실행하면 두 화면이 함께 뜹니다. 백엔드 없이 보는 mock 모드도 있습니다.

| 주소 | 화면 | 내용 |
|---|---|---|
| `/` | 시민 | 질문 입력(글·음성·사진·외국어), 민원 처리 단계를 이어 보여 주는 노선도, 추가 질문, 공식 창구 카드, 문구 수정·복사, 가상 발송과 메일함 |
| `/admin/#/ops` | 관리자 | 관제 대시보드: 처리 건수, 서비스 10개(frontend·backend·classifier·locator·verifier·writer·mailer·chaos·PostgreSQL·Redis) 상태, 에이전트별·시간대별 그래프, 활동 로그 |
| `/admin/#/live` | 관리자 | 실시간 관제: 최근 작업의 처리 단계 노선도, 실패 지점·우회·복구, 데이터 전달 점검표, 타임라인 |
| `/admin/#/lab` | 관리자 | 장애 실험실: 장애 7종·공격 문구·레드팀 실행 후 실시간 관제로 이동 |
| `/admin/#/incidents` · `#/jobs/<id>` | 관리자 | 장애 기록 표, 작업 한 건의 단계별 기록 |
| `/api/` · `/mailbox/` | 연결 | 백엔드 API와 가상 메일함(Mailpit). 가상 발송은 실제 기관에 전달되지 않습니다 |

## 폴더

| 위치 | 내용 |
|---|---|
| `index.html` → `src/citizen/` | 시민 화면 (페이지·컴포넌트·스타일·테스트) |
| `admin.html` → `src/admin/` | 관리자 화면 (페이지·컴포넌트·스타일·테스트) |
| `src/api/` · `src/lib/` · `src/hooks/` · `src/styles/tokens.css` | 두 화면이 함께 쓰는 API 연결·계산·상태 훅·색상 변수 |
| `nginx/` | 배포용 nginx 설정과 관리자 화면 잠금 스크립트 |
| `tests/` | 공유 코드와 화면 흐름 테스트 |

## 실행·테스트 (레포 루트 기준)

```bash
cd src/frontend
npm ci
npm run dev                     # http://localhost:8501 , 관리자는 /admin/
VITE_API_MODE=mock npm run dev  # 백엔드 없이 실행합니다
npm run build
npm test
```

- 개발 서버의 `BACKEND_URL` 기본값은 `http://localhost:8000`, `MAILPIT_URL`은 `http://localhost:8025`입니다.
- Compose에서는 `docker compose up -d frontend`로 실행하며 기본 주소는 `http://127.0.0.1:8501`입니다. 관리자 화면 잠금(`ADMIN_GATE`)은 [루트 README](../../README.md#관리자-화면-잠그기)를 봅니다.
- 마이크 입력은 브라우저 정책상 https나 localhost에서만 됩니다.
- 결과 형식은 [최종 결과 계약](../../contracts/final.schema.json)을 따르고, mock 모드는 [가로등 결과 샘플](../../contracts/samples/final_streetlight_output.json)을 읽습니다. 이 파일들은 `contracts/`(서비스끼리 주고받는 JSON 모양을 정한 스키마 폴더)에 있습니다.
