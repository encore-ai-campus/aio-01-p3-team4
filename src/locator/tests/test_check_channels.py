"""실제 공공기관/DB에 연결하지 않는 정기 링크 점검 회귀 테스트."""

from contextlib import contextmanager
import csv
from datetime import date
import importlib.util
import json
from pathlib import Path
import socket

import httpx
import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_channels.py"
spec = importlib.util.spec_from_file_location("channel_checker", SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class FakeConnection:
    def __init__(self, rows):
        self.rows = [dict(row) for row in rows]
        self.updates = []

    @contextmanager
    def transaction(self):
        original = [dict(row) for row in self.rows]
        try:
            yield self
        except Exception:
            self.rows = original
            raise

    @contextmanager
    def cursor(self, **kwargs):
        yield self

    def execute(self, sql, parameters=None):
        if sql.startswith("SELECT"):
            return
        self.updates.append((sql, parameters))
        if "verified_at" in sql:
            for row in self.rows:
                if row["channel_id"] == parameters[1] and row["active"]:
                    row["verified_at"] = parameters[0]
        else:
            for row in self.rows:
                if row["channel_id"] == parameters[0]:
                    row["active"] = False

    def fetchall(self):
        return [dict(row) for row in self.rows]


def channel(**overrides):
    return {
        "channel_id": "dj-all-01", "sido": "서울특별시", "sigungu": "동작구",
        "category": "all", "name": '동작구, "신고" 안내', "org": "동작구청",
        "method": "web", "url": "https://www.dongjak.go.kr/report",
        "jurisdiction": "동작구", "verified_at": date(2026, 9, 1),
        "source_url": "https://www.dongjak.go.kr/source", "active": True,
        **overrides,
    }


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(checker.socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443)),
    ])


def run_check(row, handler):
    db = FakeConnection([row])
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        report = checker.check_channels(db, client=client, today=date(2026, 10, 7))
    return db, report[0]


def test_success_requires_source_and_pins_ip_with_original_tls_name():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "www.dongjak.go.kr"
        assert request.extensions["sni_hostname"] == "www.dongjak.go.kr"
        return httpx.Response(200)

    db, report = run_check(channel(), handler)
    assert report["status"] == "verified"
    assert len(requests) == 2
    assert db.rows[0]["verified_at"] == date(2026, 10, 7)
    assert db.rows[0]["active"] is True


@pytest.mark.parametrize("code", [404, 410])
def test_confirmed_dead_link_is_disabled(code):
    db, report = run_check(channel(), lambda request: httpx.Response(
        code if request.url.path == "/report" else 200,
    ))
    assert report["status"] == "dead"
    assert db.rows[0]["active"] is False
    assert db.rows[0]["verified_at"] == date(2026, 9, 1)


@pytest.mark.parametrize("code", [401, 403, 429, 500, 503])
def test_transient_or_access_denied_preserves_state(code):
    db, report = run_check(channel(), lambda request: httpx.Response(code))
    assert report["status"] == "unknown"
    assert db.updates == []


@pytest.mark.parametrize("code, expected", [(404, "manual_review"), (403, "unknown")])
def test_unverified_source_cannot_refresh_date(code, expected):
    db, report = run_check(channel(), lambda request: httpx.Response(
        code if request.url.path == "/source" else 200,
    ))
    assert report["status"] == expected
    assert db.updates == []


def test_phone_requires_human_review_even_when_source_is_alive():
    requested = []

    def handler(request):
        requested.append(request.url.path)
        return httpx.Response(200)

    db, report = run_check(channel(method="phone", url="tel:02-820-1119"), handler)
    assert report["status"] == "manual_review"
    assert db.updates == []
    assert requested == ["/source"]


def test_alive_inactive_channel_is_not_reactivated_or_refreshed():
    db, report = run_check(channel(active=False), lambda request: httpx.Response(200))
    assert report["status"] == "inactive"
    assert db.updates == []


@pytest.mark.parametrize("url", [
    "https://example.com/", "https://dongjak.go.kr.example.com/",
    "https://notgo.kr/", "http://127.0.0.1/", "file:///etc/passwd",
    "https://secret:password@www.dongjak.go.kr/", "https://www.dongjak.go.kr:5432/",
])
def test_unofficial_initial_urls_are_blocked_before_network(url):
    def handler(request):
        pytest.fail("unsafe URL must not reach transport")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert checker.probe_url(url, client) == "blocked"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.8", "169.254.169.254", "::1"])
def test_private_dns_answer_blocks_official_domain(monkeypatch, address):
    monkeypatch.setattr(checker.socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)),
    ])
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("private DNS"))) as client:
        assert checker.probe_url("https://www.dongjak.go.kr/", client) == "blocked"


@pytest.mark.parametrize("target", ["https://example.com/", "http://127.0.0.1/"])
def test_redirect_cannot_escape_official_hosts(target):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": target})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        assert checker.probe_url("https://www.dongjak.go.kr/report", client) == "blocked"
    assert len(requests) == 1


def test_redirect_dns_is_rechecked_even_on_same_official_host(monkeypatch):
    answers = iter(["8.8.8.8", "127.0.0.1"])
    monkeypatch.setattr(checker.socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), 443)),
    ])
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "/moved"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert checker.probe_url("https://www.dongjak.go.kr/report", client) == "blocked"
    assert len(requests) == 1


def test_relative_redirect_is_joined_against_official_url():
    def handler(request):
        if request.url.path == "/report":
            return httpx.Response(302, headers={"location": "/new"})
        assert request.url.path == "/new"
        assert request.headers["host"] == "www.dongjak.go.kr"
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert checker.probe_url("https://www.dongjak.go.kr/report", client) == "alive"


def test_timeout_is_not_proof_of_dead_link():
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)

    db, report = run_check(channel(), handler)
    assert report["status"] == "unknown"
    assert db.updates == []


def test_redirect_loop_is_unknown():
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        302, headers={"location": "/report"},
    ))) as client:
        assert checker.probe_url("https://www.dongjak.go.kr/report", client) == "unknown"


def test_link_check_does_not_consume_untrusted_page_body_and_reuses_shared_url():
    class UnreadBody(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("link checker must not download page content")
            yield b""

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=UnreadBody())

    row = channel(source_url="https://www.dongjak.go.kr/report")
    db, report = run_check(row, handler)
    assert report["status"] == "verified"
    assert len(requests) == 1


def test_db_failure_rolls_back_prior_updates():
    class FailingDB(FakeConnection):
        def execute(self, sql, parameters=None):
            if sql.startswith("UPDATE") and self.updates:
                raise checker.psycopg.OperationalError("simulated connection loss")
            super().execute(sql, parameters)

    db = FailingDB([channel(), channel(channel_id="dj-all-02")])
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        with pytest.raises(checker.psycopg.OperationalError):
            checker.check_channels(db, client=client, today=date(2026, 10, 7))
    assert all(row["verified_at"] == date(2026, 9, 1) for row in db.rows)


def test_export_roundtrip_splits_regions_with_original_columns(tmp_path):
    rows = [
        channel(), channel(channel_id="seoul-all-01", sigungu="", jurisdiction="서울특별시"),
        channel(channel_id="national-all-01", sido=None, sigungu=None, active=False),
    ]
    destination = tmp_path / "seeds" / "channels.csv"
    checker.export_channels(FakeConnection(rows), destination)
    with destination.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        district = list(reader)
        assert reader.fieldnames == list(checker.COLUMNS)
    with destination.with_name("integrated_channels.csv").open(encoding="utf-8", newline="") as handle:
        integrated = list(csv.DictReader(handle))
    assert [row["channel_id"] for row in district] == ["dj-all-01"]
    assert district[0]["name"] == rows[0]["name"]
    assert district[0]["active"] == "true"
    assert len(integrated) == 2
    assert integrated[1]["active"] == "false"
    assert integrated[1]["sido"] == integrated[1]["sigungu"] == ""
    assert not list(destination.parent.glob("*.tmp"))


def test_export_all_can_write_single_file(tmp_path):
    destination = tmp_path / "all.csv"
    checker.export_channels(FakeConnection([
        channel(), channel(channel_id="national-all-01", sido="", sigungu=""),
    ]), destination, split=False)
    with destination.open(encoding="utf-8", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 2
    assert not destination.with_name("integrated_channels.csv").exists()


def test_split_export_rejects_colliding_paths(tmp_path):
    with pytest.raises(ValueError):
        checker.export_channels(FakeConnection([]), tmp_path / "integrated_channels.csv")


def test_cli_error_does_not_print_database_credentials(monkeypatch, capsys):
    secret = "postgresql://user:very-secret@localhost/db"
    monkeypatch.setattr(checker.sys, "argv", [str(SCRIPT), "--database-url", secret])

    def fail_connect(*args, **kwargs):
        raise checker.psycopg.OperationalError(secret)

    monkeypatch.setattr(checker.psycopg, "connect", fail_connect)
    assert checker.main() == 1
    captured = capsys.readouterr()
    assert "very-secret" not in captured.out + captured.err
    assert "DB" in captured.err


@pytest.fixture
def schedule_file(tmp_path):
    source = SCRIPT.parent.parent / "database/seeds/integration/channel_check_schedule.csv"
    with source.open(encoding="utf-8-sig", newline="") as handle:
        original = next(csv.DictReader(handle))

    def write(*, changes=None, columns=None, count=1):
        path = tmp_path / "schedule.csv"
        record = {**original, **(changes or {})}
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns or checker.SCHEDULE_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for _ in range(count):
                writer.writerow(record)
        return path

    return write


def test_reviewed_schedule_csv_loads_default_operating_policy(schedule_file):
    config = checker.load_schedule(schedule_file())
    assert config == checker.CheckSchedule(
        interval_seconds=86400, retry_seconds=3600, run_on_start=True,
        http_timeout_seconds=10, max_redirects=5,
    )


@pytest.mark.parametrize("changes", [
    {"interval_seconds": "0"}, {"interval_seconds": "-1"},
    {"interval_seconds": "60; run arbitrary command"}, {"interval_seconds": "2678401"},
    {"retry_seconds": "0"}, {"retry_seconds": "86401"},
    {"http_timeout_seconds": "nan"}, {"http_timeout_seconds": "61"},
    {"max_redirects": "-1"}, {"max_redirects": "11"},
    {"run_on_start": "yes"}, {"database_url_env": "OTHER_SECRET"},
    {"description": "postgresql://user:very-secret@example.invalid/db"},
    {"source_path": "unreviewed.py"}, {"implementation_status": "enabled"},
    {"verified_at": "2026-02-31"},
])
def test_unsafe_schedule_values_rejected_before_execution(schedule_file, changes):
    with pytest.raises(ValueError):
        checker.load_schedule(schedule_file(changes=changes))


@pytest.mark.parametrize("variation", ["extra", "missing", "duplicate", "two_rows"])
def test_schedule_rejects_unknown_or_ambiguous_csv_structure(schedule_file, variation):
    columns = list(checker.SCHEDULE_COLUMNS)
    count = 1
    if variation == "extra":
        columns.append("database_url")
    elif variation == "missing":
        columns.remove("retry_seconds")
    elif variation == "duplicate":
        columns.append("interval_seconds")
    else:
        count = 2
    with pytest.raises(ValueError):
        checker.load_schedule(schedule_file(columns=columns, count=count))


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert 0 < seconds <= 60
        self.waits.append(seconds)
        self.now += seconds


def test_scheduler_recovers_after_failed_cycle_without_exposing_secret():
    clock = FakeClock()
    starts, events = [], []

    def cycle():
        starts.append(clock.now)
        if len(starts) == 1:
            clock.now += 5
            raise checker.psycopg.OperationalError("postgresql://user:very-secret@host/db")
        if len(starts) == 2:
            clock.now += 10
            return [{"channel_id": "dj-all-01", "status": "verified"}]
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        checker.run_schedule(
            checker.CheckSchedule(interval_seconds=120, retry_seconds=60), cycle,
            monotonic=clock.monotonic, sleep=clock.sleep, emit=events.append,
        )
    assert starts == [0, 65, 195]
    assert events[0]["event"] == "channel_check_failed"
    assert events[0]["retry_in_seconds"] == 60
    assert events[1]["event"] == "channel_check_completed"
    assert "very-secret" not in json.dumps(events)
    assert "reports" not in events[0]  # DB 장애를 정상적인 빈 창구 결과로 표시하지 않음.


def test_long_cycle_does_not_trigger_back_to_back_catchup_runs():
    clock = FakeClock()
    starts = []

    def cycle():
        starts.append(clock.now)
        if len(starts) == 3:
            raise KeyboardInterrupt
        clock.now += 1000  # 점검이 주기보다 오래 걸려도 밀린 회차를 몰아서 실행하지 않음.
        return []

    with pytest.raises(KeyboardInterrupt):
        checker.run_schedule(
            checker.CheckSchedule(interval_seconds=120, retry_seconds=60), cycle,
            monotonic=clock.monotonic, sleep=clock.sleep, emit=lambda event: None,
        )
    assert starts == [0, 1120, 2240]


def test_delayed_first_run_waits_but_once_is_immediate():
    config = checker.CheckSchedule(interval_seconds=120, retry_seconds=60, run_on_start=False)
    clock = FakeClock()
    starts = []

    def interrupted():
        starts.append(clock.now)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        checker.run_schedule(config, interrupted, monotonic=clock.monotonic, sleep=clock.sleep)
    assert starts == [120]
    clock = FakeClock()
    assert checker.run_schedule(
        config, lambda: [], once=True, monotonic=clock.monotonic,
        sleep=clock.sleep, emit=lambda event: None,
    ) == 0
    assert clock.waits == []


def test_once_failure_returns_nonzero_without_waiting_or_retrying():
    clock = FakeClock()
    calls = []

    def failed():
        calls.append(True)
        raise checker.psycopg.OperationalError("temporary DB failure")

    assert checker.run_schedule(
        checker.CheckSchedule(), failed, once=True,
        monotonic=clock.monotonic, sleep=clock.sleep, emit=lambda event: None,
    ) == 1
    assert calls == [True]
    assert clock.waits == []


@pytest.fixture
def offline_cycle(monkeypatch):
    connections, requests = [], []

    class LifecycleDB(FakeConnection):
        def __init__(self):
            super().__init__([channel()])
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

    def connect(*args, **kwargs):
        assert all(connection.closed for connection in connections)
        db = LifecycleDB()
        connections.append(db)
        return db

    def handler(request):
        requests.append(request)
        return httpx.Response(200)

    real_check = checker.check_channels

    def check(db, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
            return real_check(db, client=client, **kwargs)

    monkeypatch.setattr(checker.psycopg, "connect", connect)
    monkeypatch.setattr(checker, "check_channels", check)
    return connections, requests


def test_each_cycle_closes_connection_and_applies_http_settings(offline_cycle):
    connections, requests = offline_cycle
    for _ in range(2):
        reports = checker.run_cycle(
            "ignored-by-fake-db", schedule=checker.CheckSchedule(http_timeout_seconds=3, max_redirects=0),
        )
        assert reports[0]["status"] == "verified"
    assert len(connections) == 2
    assert all(connection.closed for connection in connections)
    assert all(request.extensions["timeout"]["read"] == 3 for request in requests)


def test_http_redirect_limit_from_settings_is_enforced():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "/moved"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert checker.probe_url("https://www.dongjak.go.kr/report", client, max_redirects=0) == "unknown"
    assert len(requests) == 1


def test_cli_once_executes_exactly_one_real_check_cycle(monkeypatch, schedule_file, offline_cycle, capsys):
    path = schedule_file(changes={"run_on_start": "false"})
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:very-secret@host/db")
    monkeypatch.setattr(checker.sys, "argv", [str(SCRIPT), "--schedule-csv", str(path), "--once"])
    assert checker.main() == 0
    connections, requests = offline_cycle
    assert len(connections) == 1 and connections[0].closed
    assert len(requests) == 2
    output = capsys.readouterr().out
    assert output.count('"event": "channel_check_completed"') == 1
    assert "very-secret" not in output


def test_default_cli_remains_single_run(monkeypatch, offline_cycle, capsys):
    monkeypatch.setenv("DATABASE_URL", "ignored-by-fake-db")
    monkeypatch.setattr(checker.sys, "argv", [str(SCRIPT)])
    assert checker.main() == 0
    assert len(offline_cycle[0]) == 1
    assert isinstance(json.loads(capsys.readouterr().out), list)


def test_scheduled_cli_rejects_inline_credentials(monkeypatch, schedule_file, offline_cycle, capsys):
    secret = "postgresql://user:very-secret@host/db"
    monkeypatch.setenv("DATABASE_URL", "ignored-by-fake-db")
    monkeypatch.setattr(checker.sys, "argv", [
        str(SCRIPT), "--schedule-csv", str(schedule_file()), "--database-url", secret, "--once",
    ])
    with pytest.raises(SystemExit) as error:
        checker.main()
    assert error.value.code == 2
    assert offline_cycle[0] == []
    captured = capsys.readouterr()
    assert "very-secret" not in captured.out + captured.err


def test_cli_ctrl_c_exits_cleanly(monkeypatch, schedule_file, capsys):
    monkeypatch.setenv("DATABASE_URL", "ignored-by-fake-db")
    monkeypatch.setattr(checker.sys, "argv", [str(SCRIPT), "--schedule-csv", str(schedule_file()), "--once"])

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(checker, "run_cycle", interrupted)
    assert checker.main() == 130
    assert "Traceback" not in capsys.readouterr().err
