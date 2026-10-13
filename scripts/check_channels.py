"""공식 창구 링크를 점검하고 DB 결과를 12열 시드 CSV로 내보냅니다.

기본 --export는 구 창구를 지정 파일에, 시/전국 창구를 같은 폴더의
integrated_channels.csv에 씁니다. 한 파일로 모으려면 --export-all을 함께 사용합니다.
HTTP 확인은 접수 기능이나 전화번호의 유효성까지 검증하지 않습니다.
--schedule-csv를 지정한 경우에만 정기 실행하며 --once를 함께 쓰면 한 번만 점검합니다.
"""

import argparse
from collections.abc import Callable
from contextlib import nullcontext
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time

import httpx
import psycopg
from psycopg.rows import dict_row


COLUMNS = (
    "channel_id", "sido", "sigungu", "category", "name", "org", "method",
    "url", "jurisdiction", "verified_at", "source_url", "active",
)
SELECT_CHANNELS = "SELECT " + ", ".join(COLUMNS) + " FROM channels ORDER BY channel_id"
OFFICIAL_SUFFIXES = ("go.kr", "gov.kr", "120dasan.or.kr")
KST = timezone(timedelta(hours=9))
MAX_REDIRECTS = 5
SCHEDULE_COLUMNS = (
    "schedule_id", "run_on_start", "interval_seconds", "retry_seconds",
    "http_timeout_seconds", "max_redirects", "database_url_env", "description",
    "implementation_status", "source_path", "verified_at",
)


@dataclass(frozen=True)
class CheckSchedule:
    interval_seconds: int = 86400
    retry_seconds: int = 3600
    run_on_start: bool = True
    http_timeout_seconds: int = 10
    max_redirects: int = MAX_REDIRECTS


def load_schedule(path: Path) -> CheckSchedule:
    """운영자가 명시한 고정 스키마 설정만 로드. 설명 셀은 실행하지 않습니다."""
    path = Path(path)
    if path.stat().st_size > 16384:
        raise ValueError("점검 설정 CSV가 허용 크기를 초과했습니다")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(SCHEDULE_COLUMNS):
            raise ValueError("점검 설정 CSV의 헤더 또는 열 순서가 올바르지 않습니다")
        rows = list(reader)
    if len(rows) != 1 or any(value is None for value in rows[0].values()) or None in rows[0]:
        raise ValueError("점검 설정 CSV에는 완전한 설정 한 행만 있어야 합니다")
    row = rows[0]
    if any(re.search(r"postgres(?:ql)?://|(?:password|passwd)\s*=", value, re.I)
           for value in row.values()):
        raise ValueError("점검 설정 CSV에 DB 접속 문자열이나 비밀번호를 저장할 수 없습니다")
    if row["database_url_env"] != "DATABASE_URL":
        raise ValueError("정기 점검은 DATABASE_URL 환경변수만 사용합니다")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", row["schedule_id"]):
        raise ValueError("점검 설정 ID 형식이 올바르지 않습니다")
    if row["run_on_start"] not in ("true", "false"):
        raise ValueError("run_on_start는 true 또는 false여야 합니다")
    if (row["implementation_status"] != "implemented_opt_in_not_registered"
            or row["source_path"] != "scripts/check_channels.py"
            or not 1 <= len(row["description"]) <= 500):
        raise ValueError("점검 설정 메타데이터가 올바르지 않습니다")
    try:
        if date.fromisoformat(row["verified_at"]).isoformat() != row["verified_at"]:
            raise ValueError
    except ValueError:
        raise ValueError("점검 설정 확인일은 YYYY-MM-DD 형식이어야 합니다") from None

    def integer(name: str, minimum: int, maximum: int) -> int:
        value = row[name]
        if not re.fullmatch(r"[0-9]{1,8}", value) or not minimum <= int(value) <= maximum:
            raise ValueError(f"{name}은 {minimum}~{maximum} 범위의 정수여야 합니다")
        return int(value)

    interval = integer("interval_seconds", 60, 2678400)
    return CheckSchedule(
        interval_seconds=interval,
        retry_seconds=integer("retry_seconds", 60, interval),
        run_on_start=row["run_on_start"] == "true",
        http_timeout_seconds=integer("http_timeout_seconds", 1, 60),
        max_redirects=integer("max_redirects", 0, 10),
    )


class UnsafeURL(ValueError):
    """공식 외부 사이트로 안전하게 연결할 수 없는 주소."""


def _resolve_official_url(value: str) -> tuple[httpx.URL, httpx.URL]:
    """공식 도메인과 모든 DNS 응답을 검사하고 실제 연결 IP를 고정합니다."""
    url = httpx.URL(value)
    hostname = url.host.rstrip(".").lower()
    if (
        url.scheme not in ("http", "https")
        or url.userinfo
        or not hostname
        or not any(hostname == suffix or hostname.endswith("." + suffix)
                   for suffix in OFFICIAL_SUFFIXES)
        or url.port not in (None, 80 if url.scheme == "http" else 443)
    ):
        raise UnsafeURL("공식 HTTP(S) 주소만 허용")
    url = url.copy_with(host=hostname, fragment=None)
    records = socket.getaddrinfo(
        hostname, 443 if url.scheme == "https" else 80,
        type=socket.SOCK_STREAM,
    )
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise UnsafeURL("공인 IP만 허용")
    # 검사 후 HTTP 라이브러리가 호스트를 다시 DNS 조회하지 않게 합니다.
    # Host 및 TLS SNI/인증서 검증에는 아래 요청에서 원래 도메인을 사용합니다.
    return url, url.copy_with(host=addresses[0])


def probe_url(
    value: str, client: httpx.Client, *, timeout_seconds: float = 10.0,
    max_redirects: int = MAX_REDIRECTS,
) -> str:
    """alive/dead/unknown/blocked. 본문을 다운로드하거나 지시로 사용하지 않습니다."""
    seen: set[str] = set()
    for _ in range(max_redirects + 1):
        try:
            logical_url, connection_url = _resolve_official_url(value)
            if str(logical_url) in seen:
                return "unknown"
            seen.add(str(logical_url))
            # 자동 리다이렉트를 끄고 매 이동마다 도메인/DNS를 다시 검사합니다.
            with client.stream(
                "GET", connection_url,
                headers={"Host": logical_url.host, "User-Agent": "MinwonChannelChecker/1.0"},
                extensions={"sni_hostname": logical_url.host},
                follow_redirects=False, timeout=timeout_seconds,
            ) as response:
                code = response.status_code
                if code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        return "unknown"
                    value = str(logical_url.join(location))
                    continue
                if code in (404, 410):
                    return "dead"
                if 200 <= code < 300:
                    return "alive"
                # 인증·봇 차단, 속도 제한, 서버 오류는 창구 폐쇄의 증거가 아닙니다.
                return "unknown"
        except UnsafeURL:
            return "blocked"
        except (httpx.HTTPError, httpx.InvalidURL, OSError, ValueError):
            # 요청 URL이나 DB 접속 정보가 포함될 수 있는 예외 본문은 출력하지 않습니다.
            return "unknown"
    return "unknown"


def check_channels(
    connection: psycopg.Connection,
    *,
    client: httpx.Client | None = None,
    today: date | None = None,
    http_timeout_seconds: float = 10.0,
    max_redirects: int = MAX_REDIRECTS,
) -> list[dict]:
    """명확한 폐쇄만 비활성화하고, 활성 웹 창구와 출처가 모두 열릴 때 날짜 갱신.

    전화는 실제 통화 확인이 필요하므로 날짜를 자동 갱신하지 않습니다.
    운영자가 비활성화한 행은 링크가 살아 있어도 자동 재활성화하지 않습니다.
    """
    checked_at = today or datetime.now(KST).date()
    context = nullcontext(client) if client is not None else httpx.Client(
        trust_env=False, follow_redirects=False, timeout=http_timeout_seconds,
        # IP가 같은 다른 도메인 사이에서 TLS 연결이 재사용되지 않게 합니다.
        limits=httpx.Limits(max_keepalive_connections=0),
    )
    reports = []
    with context as http_client, connection.transaction():
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(SELECT_CHANNELS)
            rows = cursor.fetchall()
            cache: dict[str, str] = {}

            def probe(value: str) -> str:
                if value not in cache:
                    cache[value] = probe_url(
                        value, http_client, timeout_seconds=http_timeout_seconds,
                        max_redirects=max_redirects,
                    )
                return cache[value]

            for row in rows:
                source_status = probe(row["source_url"])
                url_status = "manual_review" if row["method"] == "phone" else probe(row["url"])
                status = "unknown"
                if row["method"] == "phone":
                    status = "manual_review"
                elif url_status == "dead":
                    cursor.execute(
                        "UPDATE channels SET active = false WHERE channel_id = %s",
                        (row["channel_id"],),
                    )
                    status = "dead"
                elif not row["active"]:
                    status = "inactive"
                elif "blocked" in (url_status, source_status):
                    status = "blocked"
                elif url_status == source_status == "alive":
                    cursor.execute(
                        "UPDATE channels SET verified_at = %s WHERE channel_id = %s AND active = true",
                        (checked_at, row["channel_id"]),
                    )
                    status = "verified"
                elif source_status == "dead":
                    # 출처 페이지 이동만으로 접수 창구 자체가 폐쇄됐다고 판단하지 않습니다.
                    status = "manual_review"
                reports.append({
                    "channel_id": row["channel_id"], "status": status,
                    "url_status": url_status, "source_status": source_status,
                })
    return reports


def export_channels(
    connection: psycopg.Connection, destination: Path, *, split: bool = True,
) -> None:
    """DB 전체 행(비활성 포함)을 기존 12열/true·false CSV로 원자적 교체합니다."""
    destination = Path(destination)
    integrated = destination.with_name("integrated_channels.csv")
    if split and destination == integrated:
        raise ValueError("분리 내보내기의 구 창구 파일은 integrated_channels.csv와 달라야 합니다")
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(SELECT_CHANNELS)
        rows = cursor.fetchall()
    targets = [(destination, rows)] if not split else [
        (destination, [row for row in rows if row["sigungu"]]),
        (integrated, [row for row in rows if not row["sigungu"]]),
    ]
    staged: list[tuple[Path, Path]] = []
    try:
        for path, selected in targets:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", dir=path.parent,
                prefix="." + path.name + ".", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                staged.append((temporary, path))
                writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
                writer.writeheader()
                for row in selected:
                    writer.writerow({**row, "active": "true" if row["active"] else "false"})
        for temporary, path in staged:
            os.replace(temporary, path)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def run_cycle(
    database_url: str, *, schedule: CheckSchedule | None = None,
    destination: Path | None = None, split: bool = True,
) -> list[dict]:
    """회차마다 연결·트랜잭션을 새로 만들고 성공한 DB 상태만 내보냅니다."""
    settings = schedule or CheckSchedule()
    with psycopg.connect(database_url, connect_timeout=5) as connection:
        reports = check_channels(
            connection, http_timeout_seconds=settings.http_timeout_seconds,
            max_redirects=settings.max_redirects,
        )
    # 먼저 DB 트랜잭션을 커밋한 뒤 최신 데이터를 CSV로 출력합니다.
    if destination is not None:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            export_channels(connection, destination, split=split)
    return reports


def _print_event(event: dict) -> None:
    print(json.dumps({"checked_at": datetime.now(KST).isoformat(), **event}, ensure_ascii=False), flush=True)


def run_schedule(
    schedule: CheckSchedule, cycle: Callable[[], list[dict]], *, once: bool = False,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[dict], None] = _print_event,
) -> int:
    """완료 시각부터 대기하는 직렬 반복. 밀린 회차를 몰아서 실행하지 않습니다.

    회차 실패는 비밀값 없는 로그를 남긴 뒤 retry_seconds 후 다시 실행합니다.
    --once는 run_on_start와 무관하게 즉시 실행하고 실패 시 1을 반환합니다.
    Ctrl+C는 호출자에게 전달하여 CLI가 130으로 종료하도록 합니다.
    """
    delay = 0 if schedule.run_on_start or once else schedule.interval_seconds
    while True:
        deadline = monotonic() + delay
        while (remaining := deadline - monotonic()) > 0:
            # 짧게 깨어나 남은 시간을 다시 확인하며 Ctrl+C로 중지할 수 있습니다.
            sleep(min(remaining, 60.0))
        try:
            reports = cycle()
        except Exception as error:
            # 예외 메시지에는 접속 문자열·개인정보가 포함될 수 있어 출력하지 않습니다.
            emit({
                "event": "channel_check_failed", "error_type": type(error).__name__,
                "retry_in_seconds": None if once else schedule.retry_seconds,
            })
            if once:
                return 1
            delay = schedule.retry_seconds
        else:
            emit({"event": "channel_check_completed", "reports": reports})
            if once:
                return 0
            delay = schedule.interval_seconds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="기존 단발 실행용; 정기 모드는 DATABASE_URL 환경변수 사용")
    parser.add_argument("--export", type=Path, nargs="?", const=Path("database/seeds/channels.csv"))
    parser.add_argument("--export-all", action="store_true", help="--export 파일 하나에 모든 지역 출력")
    parser.add_argument("--schedule-csv", type=Path, help="검토한 설정 CSV를 읽어 정기 실행; 자동 등록은 하지 않음")
    parser.add_argument("--once", action="store_true", help="--schedule-csv 설정으로 즉시 한 회차만 실행")
    args = parser.parse_args()
    if args.export_all and not args.export:
        parser.error("--export-all은 --export와 함께 사용")
    if args.once and not args.schedule_csv:
        parser.error("--once는 --schedule-csv와 함께 사용")
    if args.schedule_csv and args.database_url is not None:
        parser.error("정기 점검에는 --database-url 대신 DATABASE_URL 환경변수를 사용하세요")
    database_url = os.getenv("DATABASE_URL") if args.schedule_csv else args.database_url or os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL 환경변수 필요" if args.schedule_csv else "--database-url 또는 DATABASE_URL 필요")
    schedule = None
    if args.schedule_csv:
        try:
            schedule = load_schedule(args.schedule_csv)
        except (OSError, ValueError):
            parser.error("점검 설정 CSV의 경로·헤더·값을 확인하세요; 접속 정보는 CSV에 저장하지 마세요")
    try:
        if schedule is not None:
            return run_schedule(
                schedule,
                lambda: run_cycle(database_url, schedule=schedule, destination=args.export, split=not args.export_all),
                once=args.once,
            )
        reports = run_cycle(database_url, destination=args.export, split=not args.export_all)
    except KeyboardInterrupt:
        print("창구 정기 점검을 중지했습니다.", file=sys.stderr)
        return 130
    except (psycopg.Error, OSError, ValueError):
        print("창구 점검/내보내기 실패: DB 연결, 스키마와 출력 경로를 확인하세요.", file=sys.stderr)
        return 1
    print(json.dumps(reports, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
