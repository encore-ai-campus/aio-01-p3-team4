import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

spec = importlib.util.spec_from_file_location("chaos_measure", Path(__file__).parents[1] / "chaos_measure.py")
cm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cm)


def incident_record(human=False, detected="2026-10-11T00:00:00Z", recovered="2026-10-11T00:00:04+00:00"):
    row = dict(kind="llm_invalid", target="classifier", source="incidents",
               recovered=False, recovery_sec=None, result_ok=True)
    return cm.apply_incident(row, dict(detected_at=detected, recovered_at=recovered,
                                      human_intervention=human, result="recovered"))


def test_incident_conversion_and_missing_fields():
    row = incident_record()
    assert row["recovered"] and row["recovery_sec"] == 4
    assert not incident_record(human=True)["recovered"]
    assert not incident_record(human=None)["recovered"]
    assert not incident_record(recovered=None)["recovered"]
    assert incident_record(detected=None)["recovery_sec"] is None
    assert incident_record(detected="bad timestamp")["recovery_sec"] is None


def test_summary_uses_auto_recoveries_and_sources():
    rows = [incident_record(), incident_record(human=True), incident_record(recovered=None),
            incident_record(recovered="2026-10-11T00:00:06Z")]
    rows.append(dict(incident_record(recovered="2026-10-11T00:00:08Z"), source="script", kind="db_cut", target="postgres"))
    rows[2]["result_ok"] = False
    report = cm.summary(rows, "http://localhost", 3, "abc", {"total": 100})
    assert report["overall"] == dict(attempts=5, missed=0, auto_recovered=3, blocked=0, auto_recovery_rate=0.6, recovery_sec_avg=6)
    assert report["overall_in_metrics"] == dict(attempts=4, missed=0, auto_recovered=2, blocked=0, auto_recovery_rate=0.5, recovery_sec_avg=5)
    group = report["kinds"]["llm_invalid@classifier"]
    assert group["recovery_sec_max"] == 6 and group["result_ok"] == 3
    assert report["metrics_after"] == {"total": 100} and report["commit"] == "abc"
    assert cm.aggregate([])["recovery_sec_avg"] is None


def test_result_expectations():
    for kind, target in cm.FLAGS + [("service_down", "classifier"), ("db_cut", "postgres")]:
        if kind not in ("mail_down", "injection"):
            assert cm.result_ok(kind, {"status": "success"})
            assert not cm.result_ok(kind, {"status": "failed"})
    job = {"status": "failed", "state": {"last_result": {"failure": "INJECTION_BLOCKED"}}}
    assert cm.result_ok("injection", job)  # A detected attack is correctly blocked.
    assert cm.result_ok("injection", {"status": "blocked"})
    assert not cm.result_ok("injection", {"status": "failed", "failure": "SERVICE_UNAVAILABLE"})
    assert cm.result_ok("mail_down", {"status": "success"}, [{"agent": "mailer", "event_type": "success"}])
    assert not cm.result_ok("mail_down", {"status": "success"}, [{"agent": "writer", "event_type": "success"}])


def test_flags_use_direct_base_and_record_incident(monkeypatch):
    calls = []
    def request(base, path, method="GET", data=None, **kwargs):
        calls.append((path, method, data))
        if path == "/jobs?wait=true":
            return {"id": "job-1", "status": "success"}
        if path == "/incidents":
            return [{"id": calls[0][2]["incident_id"], "detected_at": "2026-10-11T00:00:00Z",
                     "recovered_at": "2026-10-11T00:00:02Z", "human_intervention": False, "result": "recovered"}]
    monkeypatch.setattr(cm, "request", request)
    row, error = cm.measure("http://local", "delay", "classifier", 1, "sample from contract")
    assert calls[0][0] == "/chaos/flags/classifier" and calls[0][2]["params"] == {"seconds": 12}
    assert row["incident_id"].startswith("inc-measure-delay-classifier-")
    assert row["recovery_sec"] == 2 and row["result_ok"] and error is None


def test_measure_records_only_natural_incidents_for_its_job(monkeypatch):
    injected_id = None
    def request(base, path, method="GET", data=None, **kwargs):
        nonlocal injected_id
        if method == "PUT":
            injected_id = data["incident_id"]
        if path == "/jobs?wait=true":
            return {"id": "job-measured", "status": "success"}
        if path == "/incidents":
            incident = dict(job_id="job-measured", target="classifier", fault="LLM_OUTPUT_INVALID",
                            detected_at="2026-10-11T00:00:00Z", recovered_at="2026-10-11T00:00:02Z",
                            human_intervention=False, result="recovered")
            return [dict(incident, id=injected_id), dict(incident, id="incident-natural-recovered"),
                    dict(incident, id="incident-natural-failed", target="writer", recovered_at=None),
                    dict(incident, id="incident-natural-human", human_intervention=True),
                    dict(incident, id="other-job", job_id="job-other"),
                    dict(incident, id="incident-other-fault", fault="SERVICE_UNAVAILABLE"),
                    dict(incident, id="not-natural"),
                    dict(incident, id="unclaimed", job_id=None)]
    monkeypatch.setattr(cm, "request", request)
    row, error = cm.measure("http://local", "llm_invalid", "classifier", 1, "sample")
    assert row["natural_incidents"] == [
        dict(id="incident-natural-recovered", target="classifier", fault="LLM_OUTPUT_INVALID", recovered=True, recovery_sec=2),
        dict(id="incident-natural-failed", target="writer", fault="LLM_OUTPUT_INVALID", recovered=False, recovery_sec=None),
        dict(id="incident-natural-human", target="classifier", fault="LLM_OUTPUT_INVALID", recovered=False, recovery_sec=None)]
    assert row["incident_id"] == injected_id and row["recovery_sec"] == 2 and error is None


def test_summary_counts_natural_incidents_and_checks_metrics():
    rows = [dict(incident_record(), natural_incidents=[
        dict(id="n1", target="classifier", fault="LLM_OUTPUT_INVALID", recovered=True, recovery_sec=2),
        dict(id="n2", target="writer", fault="LLM_OUTPUT_INVALID", recovered=False, recovery_sec=None)]),
        dict(incident_record(recovered="2026-10-11T00:00:08Z"), kind="db_cut", target="postgres",
             source="script", natural_incidents=[
                 dict(id="n3", target="writer", fault="SERVICE_UNAVAILABLE", recovered=True, recovery_sec=6)])]
    report = cm.summary(rows, "http://local", 1, "test", {"total": 4})
    assert report["natural"] == dict(count=3, auto_recovered=2, recovery_sec_avg=4,
                                    by_fault={"LLM_OUTPUT_INVALID": 2, "SERVICE_UNAVAILABLE": 1})
    assert report["metrics_check"] == dict(expected_total=4, metrics_total=4, match=True)
    assert report["assumes_empty_incidents_before"] is True
    assert report["overall"]["attempts"] == 2 and report["overall"]["recovery_sec_avg"] == 6
    assert report["overall_in_metrics"]["attempts"] == 1
    assert report["kinds"]["llm_invalid@classifier"]["recovery_sec_avg"] == 4
    assert cm.summary(rows, "", 1, "", {"total": 5})["metrics_check"]["match"] is False
    assert cm.summary(rows, "", 1, "", None)["metrics_check"] == dict(
        expected_total=4, metrics_total=None, match=False)
    assert cm.summary([], "", 1, "", {"total": 0})["natural"] == dict(
        count=0, auto_recovered=0, recovery_sec_avg=None, by_fault={})


def test_mail_preparation_injection_and_timeline_poll(monkeypatch):
    calls, polls, restores = [], [], []
    job = {"id": "job-mail", "status": "success", "state": {"result": {"draft": {"title": "t", "body": "b"}}}}
    def request(base, path, method="GET", data=None, **kwargs):
        calls.append((path, method, data))
        if path in ("/jobs?wait=true", "/jobs/job-mail"):
            return job
        if path.endswith("/timeline"):
            polls.append(1)
            return [{"agent": "mailer", "event_type": "success"}]
        if path == "/incidents":
            injected = next(c[2]["incident_id"] for c in calls if c[1] == "PUT")
            return [{"id": injected, "detected_at": "2026-10-11T00:00:00Z",
                     "recovered_at": "2026-10-11T00:00:45Z" if polls else None,
                     "human_intervention": False, "result": "recovered" if polls else "detected"}]
    monkeypatch.setattr(cm, "request", request)
    monkeypatch.setattr(cm, "wait_mailpit", lambda: restores.append(1))
    monkeypatch.setattr(cm.time, "sleep", lambda seconds: None)
    row, error = cm.measure("http://local", "mail_down", "mailer", 1, "sample", {"seconds": 45})
    assert [c[0] for c in calls[:3]] == ["/jobs?wait=true", "/chaos/flags/mailer", "/jobs/job-mail/send"]
    assert calls[1][2]["params"] == {"seconds": 45}
    assert calls[2][2] == {"title": "t", "body": "b"} and len(polls) == 2
    assert row["recovered"] and row["recovery_sec"] == 45 and len(restores) == 2 and error is None


def test_exception_does_not_fabricate_recovery(monkeypatch):
    def request(base, path, method="GET", data=None, **kwargs):
        if path == "/jobs?wait=true":
            raise TimeoutError("private details must not be saved")
        return [] if path == "/incidents" else None
    monkeypatch.setattr(cm, "request", request)
    row, error = cm.measure("http://local", "llm_invalid", "writer", 1, "sample")
    assert row["result"] == "error: TimeoutError" and error is None
    assert not row["result_ok"] and not row["recovered"] and row["job_id"] is None


def test_infra_restoration_and_stop_confirmation(tmp_path, monkeypatch):
    log = (tmp_path / "infra.log").open("w+")
    log.write("chaos: injected classifier for 5 seconds\nchaos: restored classifier\n")
    log.flush()
    process = subprocess.Popen(["true"])
    cm.wait_stopped(process, log, "classifier")
    cm.finish_infra(process, log, "classifier")
    log.seek(0)
    log.truncate()
    with pytest.raises(cm.RestoreFailed):
        cm.finish_infra(process, log, "classifier")
    log.close()
    def launch(command, cwd, stdout, stderr):
        stdout.write(f"chaos: injected {command[-2]} for 5 seconds\nchaos: restored {command[-2]}\n")
        stdout.flush()
        return process
    monkeypatch.setattr(cm.subprocess, "Popen", launch)
    def request(base, path, method="GET", data=None, **kwargs):
        if path == "/jobs?wait=true":
            return {"id": "job-infra", "status": "success"}
        if path == "/incidents":
            return [{"id": "wrong-job", "job_id": "other", "target": "classifier", "fault": "SERVICE_UNAVAILABLE"},
                    {"id": "inc-infra", "job_id": "job-infra", "target": "classifier", "fault": "SERVICE_UNAVAILABLE",
                     "detected_at": "2026-10-11T00:00:00Z", "recovered_at": "2026-10-11T00:00:05Z",
                     "human_intervention": False, "result": "recovered"}]
    monkeypatch.setattr(cm, "request", request)
    row, error = cm.measure("http://local", "service_down", "classifier", 1, "sample")
    assert row["hit"] and row["incident_id"] == "inc-infra" and row["recovery_sec"] == 5 and error is None
    row, error = cm.measure("http://local", "db_cut", "postgres", 1, "sample")
    assert row["hit"] and row["source"] == "script" and row["recovered"] and row["recovery_sec"] >= 0 and error is None
    def restore_failure(*args):
        raise cm.RestoreFailed("restored marker missing")
    monkeypatch.setattr(cm, "finish_infra", restore_failure)
    row, error = cm.measure("http://local", "service_down", "classifier", 1, "sample")
    assert error and row["human_intervention"] and not row["recovered"] and not row["result_ok"]


def test_main_continues_failed_runs_and_overwrites_files(monkeypatch, tmp_path):
    (tmp_path / "chaos_runs.jsonl").write_text("old data\n")
    monkeypatch.setattr(cm.time, "sleep", lambda seconds: None)
    texts = []
    def request(base, path, method="GET", data=None, **kwargs):
        if path == "/jobs?wait=true":
            texts.append(data["text"])
            raise ConnectionError()
        return [] if path == "/incidents" else None
    monkeypatch.setattr(cm, "request", request)
    assert cm.main(["--base", "http://local", "--runs", "2", "--kinds", "llm_invalid@classifier",
                    "--out", str(tmp_path), "--commit", "test"]) == 0
    rows = [json.loads(line) for line in (tmp_path / "chaos_runs.jsonl").read_text().splitlines()]
    assert len(rows) == 2 and all(not row["result_ok"] for row in rows)
    assert texts == [json.loads((cm.ROOT / f"contracts/samples/classifier_{name}_input.json").read_text())["inputs"]["text"]
                     for name in ("streetlight", "road_damage")]
    overall = json.loads((tmp_path / "chaos_summary.json").read_text())["overall"]
    assert overall["attempts"] == 0 and overall["missed"] == 2


def test_main_aborts_after_restore_failure_and_rotates_targets(monkeypatch, tmp_path):
    targets = []
    def measure(base, kind, target, run, text, params=None, variant=None):
        targets.append(target)
        return dict(incident_record(recovered=None), kind=kind, target=target), "restore failed" if run == 4 else None
    monkeypatch.setattr(cm, "measure", measure)
    monkeypatch.setattr(cm, "request", lambda *args: {"total": 0})
    monkeypatch.setattr(cm.time, "sleep", lambda seconds: None)
    assert cm.main(["--base", "http://local", "--runs", "6", "--kinds", "service_down,db_cut",
                    "--infra", "--out", str(tmp_path), "--commit", "test"]) == 1
    assert targets == list(cm.SERVICES)
    assert json.loads((tmp_path / "chaos_summary.json").read_text())["overall"]["attempts"] == 4


def test_cli_rejects_infra_without_opt_in_and_nonpositive_runs():
    for options in (["--kinds", "db_cut"], ["--runs", "0"], ["--kinds", "typo"]):
        with pytest.raises(SystemExit) as error:
            cm.main(["--base", "http://local"] + options)
        assert error.value.code == 2


@pytest.mark.parametrize("kind", ["llm_invalid", "service_down"])
@pytest.mark.parametrize("found", [True, False])
def test_measure_hit_requires_matching_incident(monkeypatch, kind, found):
    injected_id = None
    def request(base, path, method="GET", data=None, **kwargs):
        nonlocal injected_id
        if method == "PUT":
            injected_id = data["incident_id"]
        if path == "/jobs?wait=true":
            return {"id": "job-measured", "status": "success"}
        if path == "/incidents":
            return [dict(id=(injected_id or "inc-infra") if found else "unrelated",
                         job_id="job-measured" if found else "other-job", target="writer",
                         fault="SERVICE_UNAVAILABLE", detected_at="2026-10-11T00:00:00Z",
                         recovered_at="2026-10-11T00:00:04Z", human_intervention=False)]
    monkeypatch.setattr(cm, "request", request)
    monkeypatch.setattr(cm.subprocess, "Popen", lambda *args, **kwargs: object())
    monkeypatch.setattr(cm, "wait_stopped", lambda *args: None)
    monkeypatch.setattr(cm, "finish_infra", lambda *args: None)
    row, error = cm.measure("http://local", kind, "writer", 1, "sample")
    assert row["hit"] is found and row["result_ok"] and error is None
    if kind == "llm_invalid":
        assert row["incident_id"] == injected_id  # An injected flag ID alone is not a hit.


def test_summary_excludes_misses_and_keeps_all_result_outcomes():
    rows = [incident_record(), incident_record(human=True),
            dict(incident_record(recovered="2026-10-11T00:02:00Z"), hit=False),
            dict(incident_record(), kind="service_down", target="writer", hit=False),
            dict(incident_record(recovered="2026-10-11T00:00:08Z"),
                 kind="db_cut", target="postgres", source="script")]
    rows[1]["result_ok"] = False
    rows[3]["recovered"] = False
    rows[0]["natural_incidents"] = [dict(id="n1", fault="LLM_OUTPUT_INVALID", recovered=True, recovery_sec=2)]
    report = cm.summary(rows, "http://local", 1, "test", {"total": 3})
    assert report["overall"] == dict(attempts=3, missed=2, auto_recovered=2, blocked=0,
                                     auto_recovery_rate=2 / 3, recovery_sec_avg=6)
    assert report["overall_in_metrics"] == dict(attempts=2, missed=2, auto_recovered=1, blocked=0,
                                                auto_recovery_rate=0.5, recovery_sec_avg=4)
    assert report["kinds"]["llm_invalid@classifier"] == dict(
        attempts=2, missed=1, auto_recovered=1, blocked=0, auto_recovery_rate=0.5,
        recovery_sec_avg=4, recovery_sec_max=4, result_ok=2)
    assert report["kinds"]["service_down@writer"] == dict(
        attempts=0, missed=1, auto_recovered=0, blocked=0, auto_recovery_rate=0,
        recovery_sec_avg=None, recovery_sec_max=None, result_ok=1)
    assert report["metrics_check"] == dict(expected_total=3, metrics_total=3, match=True)


def test_resummarize_backfills_hits_and_preserves_metadata(tmp_path, monkeypatch):
    rows = [dict(incident_record(), incident_id="inc-hit"),
            dict(incident_record(recovered=None), kind="service_down", target="writer", incident_id=None),
            dict(incident_record(), kind="db_cut", target="postgres", source="script", incident_id=None)]
    for row in rows:
        row.pop("hit")
    rows.append(dict(rows[0], hit=False))  # Preserve an explicit miss even with a flag ID.
    runs_path, summary_path = tmp_path / "runs.jsonl", tmp_path / "summary.json"
    runs_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    runs_before = runs_path.read_bytes()
    previous = cm.summary(rows, "http://saved", 7, "original-commit", {"total": 1})
    previous["measured_at"] = "2026-10-10T00:00:00Z"
    summary_path.write_text(json.dumps(previous), encoding="utf-8")
    monkeypatch.setattr(cm, "request", lambda *args, **kwargs: pytest.fail("resummarize must stay offline"))
    cm.resummarize(runs_path, str(summary_path))
    report = json.loads(summary_path.read_text(encoding="utf-8"))
    for key in ("measured_at", "commit", "base", "runs_per_kind", "metrics_after"):
        assert report[key] == previous[key]
    assert report["overall"]["attempts"] == 2 and report["overall"]["missed"] == 2
    assert report["overall"]["auto_recovery_rate"] == 1
    assert report["kinds"]["service_down@writer"]["attempts"] == 0
    assert report["kinds"]["db_cut@postgres"]["attempts"] == 1
    assert report["metrics_check"] == dict(expected_total=1, metrics_total=1, match=True)
    assert runs_path.read_bytes() == runs_before


def test_detected_timestamp_and_failed_job_override_early_recovery(monkeypatch):
    assert not cm.apply_incident({}, {"result": "injected"})["hit"]
    injected = {}
    def request(base, path, method="GET", data=None, **kwargs):
        if method == "PUT":
            injected.update(data)
        if path == "/jobs?wait=true":
            return {"id": "job-failed", "status": "failed"}
        if path == "/incidents":
            return [{"id": injected["incident_id"], "detected_at": "2026-10-11T00:00:00Z",
                     "recovered_at": "2026-10-11T00:00:02Z", "human_intervention": False}]
    monkeypatch.setattr(cm, "request", request)
    row, error = cm.measure("http://local", "link_dead", "verifier", 1, "sample", {"repeat": 3})
    assert injected["params"] == {"repeat": 3}
    assert row["hit"] and not row["recovered"] and row["job_status"] == "failed" and error is None


def test_injection_uses_detector_without_setting_flag(monkeypatch):
    calls = []
    def request(base, path, method="GET", data=None, **kwargs):
        calls.append((path, method, data))
        if path == "/jobs?wait=true":
            return {"id": "job-attack", "status": "failed", "state": {"last_result": {"failure": "INJECTION_BLOCKED"}}}
        if path == "/incidents":
            return [{"id": "incident-attack", "job_id": "job-attack", "target": "backend", "fault": "INJECTION_BLOCKED",
                     "detected_at": "2026-10-11T00:00:00Z", "recovered_at": "2026-10-11T00:00:00Z",
                     "result": "blocked", "human_intervention": False}]
    monkeypatch.setattr(cm, "request", request)
    row, error = cm.measure("http://local", "injection", "backend", 1, "sample")
    assert all("/flags/" not in path for path, _, _ in calls)
    assert "Ignore all previous instructions" in calls[0][2]["text"]
    assert row["hit"] and row["result"] == "blocked" and row["recovered"] and error is None
    assert not row["natural_incidents"]


def test_default_plan_is_110_runs_with_separate_stress_and_existing_metrics(monkeypatch, tmp_path):
    plan = []
    def measure(base, kind, target, run, text, params=None, variant=None):
        plan.append((kind, target, params, variant))
        return dict(incident_record(), kind=kind, target=target, variant=variant, params=params,
                    job_status="success", incident={"id": str(len(plan))}), None
    monkeypatch.setattr(cm, "measure", measure)
    monkeypatch.setattr(cm, "request", lambda *args: {"total": 100 + len(plan)})
    monkeypatch.setattr(cm.time, "sleep", lambda seconds: None)
    assert cm.main(["--base", "http://local", "--infra", "--commit", "test", "--out", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "chaos_summary.json").read_text())
    assert len(plan) == report["total_runs"] == 110
    assert sum(variant.endswith(":stress") for _, _, _, variant in plan) == 20
    assert sum(kind in ("service_down", "db_cut") for kind, _, _, _ in plan) == 10
    assert report["metrics_check"] == dict(expected_total=210, metrics_total=210, match=True)
    assert report["metrics_before"] == {"total": 100} and not report["assumes_empty_incidents_before"]
    assert report["job_statuses"] == {"success": 110}


def test_mailpit_inspects_compose_container_and_waits_until_running(monkeypatch):
    states = iter(["container-id\n", "false\n", "true\n"])
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        return next(states)
    monkeypatch.setattr(cm.subprocess, "check_output", command)
    monkeypatch.setattr(cm.time, "sleep", lambda seconds: None)
    cm.wait_mailpit()
    assert calls[0] == ["docker", "compose", "ps", "--all", "-q", "mailpit"]
    assert calls[1][-1] == calls[2][-1] == "container-id"


@pytest.mark.parametrize("failure,incident_result", [("INJECTION_BLOCKED", "blocked"), (None, "blocked"), ("INJECTION_BLOCKED", None)])
def test_resummarize_counts_blocked_attacks_without_rewriting_evidence(tmp_path, monkeypatch, failure, incident_result):
    blocked = dict(incident_record(), kind="injection", target="backend", job_status="failed",
                   job_failure=failure, incident={"id": "attack", "result": incident_result},
                   result="blocked", result_ok=False, recovered=False)
    failed = dict(blocked, kind="delay", job_failure="SERVICE_UNAVAILABLE", result="failed",
                  incident={"id": "delay", "result": "failed"})
    human = dict(blocked, human_intervention=True)
    error = dict(blocked, result="error: TimeoutError")
    missed = dict(blocked, hit=False)
    rows = [blocked, failed, human, error, missed]
    runs, report = tmp_path / "runs.jsonl", tmp_path / "summary.json"
    runs.write_text("".join(json.dumps(r) + "\n" for r in rows))
    raw = runs.read_bytes()
    previous = cm.summary(rows, "http://saved", 1, "original", {"total": 2})
    report.write_text(json.dumps(previous))
    monkeypatch.setattr(cm, "request", lambda *a, **k: pytest.fail("offline only"))
    cm.resummarize(runs, report)
    summary = json.loads(report.read_text())
    assert summary["overall"]["auto_recovered"] == summary["overall"]["blocked"] == 1
    assert summary["overall"]["attempts"] == 4 and summary["overall"]["missed"] == 1
    assert summary["job_statuses"] == {"failed": 5}
    assert summary["measured_at"] == previous["measured_at"]
    assert runs.read_bytes() == raw
