# deploy/data — 데이터 서버 PostgreSQL·Redis (담당: 한태경)

앱 서비스는 앱 서버에서, PostgreSQL과 Redis는 이 데이터 서버(별도 EC2)에서 돌립니다. 앱 서버가 재배포되거나 컨테이너가 꺼져도 DB 기록은 남게 하려고 나눴습니다.
처음 한 번만 아래 1~5단계를 차례로 실행하면 됩니다. DB 표는 [스키마](../../database/schema.sql)대로 처음 띄울 때 자동으로 만들어집니다.

## 1~3. 서버 준비부터 띄우기까지

```bash
docker compose version                      # 1. 버전이 나오면 준비 끝
git clone https://github.com/encore-ai-campus/aio-01-p3-team4.git
cd aio-01-p3-team4/deploy/data
cp .env.example .env                         # 2. .env의 비밀번호 두 줄을 직접 정한 값으로 바꾸기
docker compose up -d --wait                  # 3. 띄우기
docker compose ps                            # minwon-postgres, minwon-redis 둘 다 healthy면 성공
```

`port is already allocated`가 나오면 다른 컨테이너가 5432·6379를 쓰고 있는 것입니다. `docker ps`로 찾아 멈춘 뒤 다시 띄웁니다.

## 4. 보안 그룹

데이터 서버 인바운드 규칙에 TCP 5432·6379를 추가하고, 소스는 앱 서버 공인 IP`/32`로만 엽니다. `0.0.0.0/0`(전체 공개)은 넣지 않습니다.

## 5. 앱 서버와 연결

앱 서버에서 연결을 확인한 뒤, 앱 서버 `.env`에 데이터 서버 IP를 넣습니다.

```bash
docker run --rm postgres:17-alpine pg_isready -h <데이터 서버 IP> -p 5432        # accepting connections
docker run --rm redis:7-alpine redis-cli -h <데이터 서버 IP> -a '<비밀번호>' ping   # PONG
```

```bash
COMPOSE_PROFILES=
DATABASE_URL=postgresql://minwon:<비밀번호>@<데이터 서버 IP>:5432/minwon
REDIS_URL=redis://:<비밀번호>@<데이터 서버 IP>:6379/0
DATA_HOST=<데이터 서버 IP>
FRONTEND_BIND=0.0.0.0
```

`COMPOSE_PROFILES`를 비워 두면 앱 서버에서는 PostgreSQL·Redis를 띄우지 않습니다. `DATA_HOST`는 장애 시험 스크립트가 DB 끊김을 낼 때 씁니다.

## 알아 둘 것

- EC2를 껐다 켜면 공인 IP가 바뀝니다. 앱 서버 `.env`의 주소와 보안 그룹 소스를 새 IP로 고칩니다. 탄력적 IP를 붙이면 바뀌지 않습니다.
- DB 표 모양(`schema.sql`)이 바뀌면 이 폴더에서 `git pull` 후 `docker compose down -v && docker compose up -d --wait`로 다시 만듭니다. **기존 데이터가 지워집니다.**
- "DB 끊김" 장애 시험은 이 서버를 끄지 않고, 앱 서버에서 이쪽으로 가는 연결만 잠깐 막습니다.
