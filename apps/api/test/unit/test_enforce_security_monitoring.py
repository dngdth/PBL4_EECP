import json
import os
import sqlite3
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.client.sensors.security_event_sensor import SecurityEventSensor
from agent.infrastructure.policy_enforcement import (
    POLICY_MARKER_END,
    POLICY_MARKER_START,
    ProcessIdentity,
    ProcessIdentityStatus,
    WindowsPolicyEnforcer,
)
from agent.ipc.security_events import SecurityEventIpcHandler
from agent.service.environment import load_service_environment
from agent.service.runtime import AgentServiceRuntime
from agent.service.security_event_journal import (
    SecurityEventJournal,
    SecurityEventRecord,
)
from agent.service.windows_service import (
    WTS_CONSOLE_CONNECT,
    WTS_CONSOLE_DISCONNECT,
    WTS_REMOTE_CONNECT,
    WTS_REMOTE_DISCONNECT,
    WTS_SESSION_LOCK,
    WTS_SESSION_LOGOFF,
    WTS_SESSION_LOGON,
    WTS_SESSION_UNLOCK,
    EECPWindowsService,
    parse_windows_session_id,
    session_action,
)
from contracts.v2 import Event, EventType

NOW = datetime(2026, 10, 27, 3, 0, tzinfo=UTC)
TOKEN = "test-local-security-event-token"


def test_protected_service_environment_accepts_only_allowlisted_unique_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "service.env"
    source.write_text(
        "# privileged service only\n"
        "EECP_AGENT_ID=AGT-001\n"
        "EECP_POLICY_MODE=enforce\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("EECP_AGENT_ID", raising=False)
    monkeypatch.delenv("EECP_POLICY_MODE", raising=False)

    load_service_environment(source)

    assert os.environ["EECP_AGENT_ID"] == "AGT-001"
    assert os.environ["EECP_POLICY_MODE"] == "enforce"

    source.write_text("PATH=C:\\unsafe\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid Service environment"):
        load_service_environment(source)


def _process(name: str, pid: int, created: str) -> ProcessIdentity:
    return ProcessIdentity(name=name, pid=pid, creation_time=created, username="LAB\\user")


def _event(
    event_id: str,
    *,
    session_id: str = "SES-1",
    occurred_at: datetime = NOW,
    action: str = "DETECTED",
) -> SecurityEventRecord:
    return SecurityEventRecord(
        event_id=event_id,
        session_id=session_id,
        event_type="FORBIDDEN_PROCESS_DETECTED",
        category="FORBIDDEN_PROCESS",
        action=action,
        severity="WARNING",
        source="privileged-service",
        occurred_at=occurred_at,
        correlation_id=event_id,
        details={"process_name": "blocked.exe", "pid": 42},
    )


def _enforcer(tmp_path: Path, provider, runner, events: list[dict]):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    return WindowsPolicyEnforcer(
        tmp_path / "state.json",
        hosts_path=hosts,
        runner=runner,
        process_provider=provider,
        event_sink=lambda **values: events.append(values),
    )


def test_process_termination_success_records_detected_then_blocked(tmp_path: Path) -> None:
    process = _process("blocked.exe", 42, "created-1")
    inventories = iter(((process,), (process,), ()))
    calls = []
    events: list[dict] = []

    def runner(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    enforcer = _enforcer(tmp_path, lambda: next(inventories), runner, events)
    results = enforcer._terminate_denied(["blocked.exe"], session_id="SES-1")

    assert results[0].outcome == "TERMINATED"
    assert calls == [["taskkill", "/F", "/PID", "42"]]
    assert [(event["event_type"], event["action"]) for event in events] == [
        ("FORBIDDEN_PROCESS_DETECTED", "DETECTED"),
        ("ENFORCEMENT_ACTION", "BLOCKED"),
    ]


def test_process_failure_never_reports_blocked_and_does_not_spam(tmp_path: Path) -> None:
    process = _process("blocked.exe", 42, "created-1")
    events: list[dict] = []
    enforcer = _enforcer(
        tmp_path,
        lambda: (process,),
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 1, "", "denied"),
        events,
    )

    first = enforcer._terminate_denied(["blocked.exe"], session_id="SES-1")
    second = enforcer._terminate_denied(["blocked.exe"], session_id="SES-1")

    assert first[0].outcome == second[0].outcome == "FAILED"
    assert "BLOCKED" not in [event["action"] for event in events]
    assert [event["action"] for event in events] == ["DETECTED", "FAILED"]
    assert enforcer.health_error is not None


def test_process_exits_before_taskkill_and_pid_reuse_is_not_killed(tmp_path: Path) -> None:
    original = _process("blocked.exe", 42, "created-1")
    reused = _process("allowed.exe", 42, "created-2")
    inventories = iter(((original,), (reused,)))
    calls = []
    events: list[dict] = []
    enforcer = _enforcer(
        tmp_path,
        lambda: next(inventories),
        lambda args, **_kwargs: calls.append(args),
        events,
    )

    result = enforcer._terminate_denied(["blocked.exe"], session_id="SES-1")

    assert result[0].outcome == "ALREADY_EXITED"
    assert result[0].termination_attempted is False
    assert calls == []


def test_two_same_name_processes_have_distinct_identity_and_events(tmp_path: Path) -> None:
    first = _process("blocked.exe", 41, "created-1")
    second = _process("blocked.exe", 42, "created-2")
    inventories = iter(((first, second), (first, second), (second,), (second,), ()))
    events: list[dict] = []
    enforcer = _enforcer(
        tmp_path,
        lambda: next(inventories),
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""),
        events,
    )

    results = enforcer._terminate_denied(["blocked.exe"], session_id="SES-1")

    assert {(item.process.pid, item.process.creation_time) for item in results} == {
        (41, "created-1"),
        (42, "created-2"),
    }
    assert sum(event["action"] == "DETECTED" for event in events) == 2


def test_service_restart_restores_process_dedup_state(tmp_path: Path) -> None:
    process = _process("blocked.exe", 42, "created-1")
    state = {
        "observed_processes": [
            {"pid": 42, "creation_time": "created-1", "outcome": "FAILED"}
        ]
    }
    events: list[dict] = []
    enforcer = _enforcer(
        tmp_path,
        lambda: (process,),
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 1, "", ""),
        events,
    )
    enforcer._load_process_observations(state)

    enforcer._terminate_denied(["blocked.exe"], session_id="SES-1")

    assert events == []


def test_session_snapshot_and_service_accounts_do_not_create_events(tmp_path: Path) -> None:
    journal = SecurityEventJournal(tmp_path / "events.db")
    runtime = AgentServiceRuntime.build(
        policy_mode="audit",
        state_path=tmp_path / "state.json",
        service_version="test",
        security_journal=journal,
    )
    runtime.seed_session_snapshot({1: "student", 2: "SYSTEM"})
    runtime.record_session_change(
        action="LOGON", windows_session_id=1, username="student", domain="LAB"
    )
    runtime.record_session_change(
        action="LOGON", windows_session_id=2, username="SYSTEM", domain="NT AUTHORITY"
    )

    assert journal.pending(session_id="SES-1") == ()


def test_windows_service_entrypoint_and_wts_mapping_are_importable() -> None:
    assert EECPWindowsService.__module__ == "agent.service.windows_service"
    assert "<locals>" not in EECPWindowsService.__qualname__
    assert EECPWindowsService._svc_name_ == "EECPAgentService"
    assert session_action(WTS_CONSOLE_CONNECT) is None
    assert session_action(WTS_CONSOLE_DISCONNECT) is None
    assert {
        session_action(WTS_SESSION_LOGON),
        session_action(WTS_SESSION_LOGOFF),
        session_action(WTS_SESSION_LOCK),
        session_action(WTS_SESSION_UNLOCK),
        session_action(WTS_REMOTE_CONNECT),
        session_action(WTS_REMOTE_DISCONNECT),
    } == {"LOGON", "LOGOFF", "LOCK", "UNLOCK", "REMOTE_CONNECT", "REMOTE_DISCONNECT"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (7, 7),
        ((7,), 7),
        ([7], 7),
        ({"SessionId": 7}, 7),
        ({"session_id": 7}, 7),
        ((7, 8), None),
        (("7",), None),
        (-1, None),
        (True, None),
        (None, None),
    ],
)
def test_windows_session_notification_data_is_parsed_safely(raw, expected) -> None:
    assert parse_windows_session_id(raw) == expected


def test_all_six_session_actions_use_snapshot_fallback_and_active_exam(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"policy_hash": "a" * 64, "session_id": "SES-1"}),
        encoding="utf-8",
    )
    journal = SecurityEventJournal(tmp_path / "session-events.db")
    runtime = AgentServiceRuntime.build(
        policy_mode="audit",
        state_path=state_path,
        service_version="test",
        security_journal=journal,
    )
    runtime.seed_session_snapshot({7: ("student", "LAB")})

    runtime.record_session_change(
        action="LOCK", windows_session_id=7, username="student", domain="LAB"
    )
    runtime.record_session_change(
        action="UNLOCK", windows_session_id=7, username="student", domain="LAB"
    )
    runtime.record_session_change(
        action="REMOTE_CONNECT", windows_session_id=7, username="student", domain="LAB"
    )
    runtime.record_session_change(
        action="REMOTE_DISCONNECT", windows_session_id=7, username="student", domain="LAB"
    )
    runtime.record_session_change(
        action="LOGOFF", windows_session_id=7, username=None, domain=None
    )
    runtime.record_session_change(
        action="LOGON", windows_session_id=8, username="student", domain="LAB"
    )

    events = [event for _sequence, event in journal.pending(session_id="SES-1")]
    assert [event.action for event in events] == [
        "LOCK",
        "UNLOCK",
        "REMOTE_CONNECT",
        "REMOTE_DISCONNECT",
        "LOGOFF",
        "LOGON",
    ]
    assert events[4].details == {
        "windows_session_id": 7,
        "username": "student",
        "domain": "LAB",
    }


def test_session_change_no_active_exam_seeds_snapshot_and_no_event(tmp_path: Path) -> None:
    journal = SecurityEventJournal(tmp_path / "no-session.db")
    runtime = AgentServiceRuntime.build(
        policy_mode="audit",
        state_path=tmp_path / "nonexistent-state.json",
        service_version="test",
        security_journal=journal,
    )
    # Session not RUNNING (active_session_id is None)
    runtime.record_session_change(
        action="LOGON", windows_session_id=10, username="student1", domain="LAB"
    )
    assert journal.pending(session_id="SES-NONE") == ()
    assert runtime._session_snapshot.get(10) == ("student1", "LAB")


def test_session_change_ignores_system_accounts_and_restart_duplicates(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"policy_hash": "a" * 64, "session_id": "SES-1"}),
        encoding="utf-8",
    )
    journal = SecurityEventJournal(tmp_path / "sys-accounts.db")
    runtime = AgentServiceRuntime.build(
        policy_mode="audit",
        state_path=state_path,
        service_version="test",
        security_journal=journal,
    )
    # System accounts must not be journaled as student logon events
    for sys_account in ("SYSTEM", "LOCAL SERVICE", "NETWORK SERVICE"):
        runtime.record_session_change(
            action="LOGON", windows_session_id=1, username=sys_account, domain="NT AUTHORITY"
        )
    assert journal.pending(session_id="SES-1") == ()

    # Seed startup snapshot
    runtime.seed_session_snapshot({10: ("student1", "LAB")})
    # Receiving LOGON for the already logged in student after service restart must be ignored
    runtime.record_session_change(
        action="LOGON", windows_session_id=10, username="student1", domain="LAB"
    )
    assert journal.pending(session_id="SES-1") == ()


def test_windows_service_cli_choices_include_restart() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scm",
        choices=("install", "remove", "start", "stop", "restart", "debug"),
    )
    args = parser.parse_args(["--scm", "restart"])
    assert args.scm == "restart"


def test_journal_persists_pages_and_ack_is_session_bound(tmp_path: Path) -> None:
    path = tmp_path / "security.db"
    journal = SecurityEventJournal(path)
    journal.append(_event("evt-1"))
    journal.append(_event("evt-2"))
    journal.append(_event("evt-other", session_id="SES-2"))

    reopened = SecurityEventJournal(path)
    page = reopened.pending(session_id="SES-1", limit=1)
    next_page = reopened.pending(
        session_id="SES-1", after_sequence=page[0][0], limit=1
    )
    wrong_session_ack = reopened.acknowledge("SES-2", ("evt-1",))
    correct_ack = reopened.acknowledge("SES-1", ("evt-1",))

    assert page[0][1].event_id == "evt-1"
    assert next_page[0][1].event_id == "evt-2"
    assert wrong_session_ack == 0
    assert correct_ack == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_journal_full_does_not_remove_pending_event(tmp_path: Path) -> None:
    journal = SecurityEventJournal(tmp_path / "full.db", max_rows=1)
    journal.append(_event("evt-1"))

    with pytest.raises(OSError, match="full"):
        journal.append(_event("evt-2"))

    assert [event.event_id for _, event in journal.pending(session_id="SES-1")] == [
        "evt-1"
    ]


def test_retention_removes_only_old_acknowledged_events(tmp_path: Path) -> None:
    now = NOW
    journal = SecurityEventJournal(
        tmp_path / "retention.db", retention_days=14, clock=lambda: now
    )
    journal.append(_event("evt-old", occurred_at=NOW - timedelta(days=20)))
    journal.append(_event("evt-pending", occurred_at=NOW - timedelta(days=20)))
    journal.acknowledge("SES-1", ("evt-old",))
    journal.append(_event("evt-new"))

    with sqlite3.connect(journal.path) as connection:
        ids = {row[0] for row in connection.execute("SELECT event_id FROM security_events")}
    assert ids == {"evt-pending", "evt-new"}


def test_ipc_rejects_bad_token_and_non_allowlisted_operation(tmp_path: Path) -> None:
    journal = SecurityEventJournal(tmp_path / "ipc.db")
    handler = SecurityEventIpcHandler(journal, TOKEN, "agent-1")
    base = {
        "protocol_version": 2,
        "request_id": "req-1",
        "agent_id": "agent-1",
        "session_id": "SES-1",
        "after_sequence": 0,
        "limit": 10,
        "event_ids": [],
    }

    rejected = json.loads(
        handler.try_handle(
            json.dumps({**base, "operation": "READ_SECURITY_EVENTS", "token": "x" * 16}).encode()
        )
    )
    unsupported = handler.try_handle(
        json.dumps({**base, "operation": "RUN_SHELL", "token": TOKEN}).encode()
    )

    assert rejected["status"] == "REJECTED"
    assert unsupported is None


def test_sensor_does_not_ack_before_durable_report_succeeds(tmp_path: Path) -> None:
    journal = SecurityEventJournal(tmp_path / "handoff.db")
    journal.append(_event("evt-1"))
    handler = SecurityEventIpcHandler(journal, TOKEN, "agent-1")

    class Transport:
        def request(self, payload: bytes) -> bytes:
            response = handler.try_handle(payload)
            assert response is not None
            return response

    attempts = 0

    def report(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("Agent spool unavailable")

    sensor = SecurityEventSensor(Transport(), TOKEN, "agent-1", report)
    sensor.activate("SES-1", {})

    with pytest.raises(OSError, match="spool"):
        sensor.poll()
    assert journal.pending(session_id="SES-1")[0][1].event_id == "evt-1"

    sensor.poll()
    assert journal.pending(session_id="SES-1") == ()
    assert attempts == 2


def test_sensor_drains_pending_session_after_active_policy_deactivation(tmp_path: Path) -> None:
    journal = SecurityEventJournal(tmp_path / "late-handoff.db")
    journal.append(_event("evt-late"))
    handler = SecurityEventIpcHandler(journal, TOKEN, "agent-1")
    reported: list[tuple[str, str]] = []

    class Transport:
        def request(self, payload: bytes) -> bytes:
            response = handler.try_handle(payload)
            assert response is not None
            return response

    sensor = SecurityEventSensor(
        Transport(),
        TOKEN,
        "agent-1",
        lambda session_id, _event_type, _payload, **identity: reported.append(
            (session_id, identity["event_id"])
        ),
    )
    sensor.activate("SES-1", {})
    sensor.deactivate()
    sensor.poll()

    assert reported == [("SES-1", "evt-late")]
    assert journal.pending(session_id="SES-1") == ()


def test_sensor_restart_after_spool_persist_before_journal_ack_is_idempotent(
    tmp_path: Path,
) -> None:
    journal = SecurityEventJournal(tmp_path / "crash-window.db")
    journal.append(_event("evt-crash-window"))
    handler = SecurityEventIpcHandler(journal, TOKEN, "agent-1")
    spooled: set[str] = set()

    class Transport:
        def request(self, payload: bytes) -> bytes:
            response = handler.try_handle(payload)
            assert response is not None
            return response

    def persist_then_crash(_session_id, _event_type, _payload, **identity):
        spooled.add(identity["event_id"])
        raise OSError("simulated Client crash before journal ACK")

    first = SecurityEventSensor(Transport(), TOKEN, "agent-1", persist_then_crash)
    with pytest.raises(OSError, match="crash"):
        first.poll()
    assert spooled == {"evt-crash-window"}
    assert journal.pending(session_id="SES-1")

    restarted = SecurityEventSensor(
        Transport(),
        TOKEN,
        "agent-1",
        lambda _session_id, _event_type, _payload, **identity: spooled.add(
            identity["event_id"]
        ),
    )
    restarted.poll()

    assert spooled == {"evt-crash-window"}
    assert journal.pending(session_id="SES-1") == ()


def test_security_event_ipc_rejects_agent_identity_mismatch(tmp_path: Path) -> None:
    journal = SecurityEventJournal(tmp_path / "agent-binding.db")
    journal.append(_event("evt-1"))
    handler = SecurityEventIpcHandler(journal, TOKEN, "agent-1")

    response = handler.try_handle(
        json.dumps(
            {
                "protocol_version": 2,
                "operation": "LIST_PENDING_SECURITY_SESSIONS",
                "request_id": "req-1",
                "agent_id": "agent-2",
                "token": TOKEN,
                "after_sequence": 0,
                "limit": 10,
                "event_ids": [],
            }
        ).encode()
    )

    assert response is not None
    assert json.loads(response)["status"] == "REJECTED"


@pytest.mark.parametrize(
    ("responses", "expected_outcome", "attempted", "degraded"),
    [
        ((ProcessIdentityStatus.NOT_FOUND,), "ALREADY_EXITED", False, False),
        ((ProcessIdentityStatus.QUERY_FAILED,), "QUERY_FAILED", False, True),
        (
            (ProcessIdentityStatus.EXISTS, ProcessIdentityStatus.NOT_FOUND),
            "TERMINATED",
            True,
            False,
        ),
        (
            (ProcessIdentityStatus.EXISTS, ProcessIdentityStatus.QUERY_FAILED),
            "QUERY_FAILED",
            True,
            True,
        ),
    ],
)
def test_process_identity_tri_state_controls_termination(
    tmp_path: Path,
    responses: tuple[ProcessIdentityStatus, ...],
    expected_outcome: str,
    attempted: bool,
    degraded: bool,
) -> None:
    process = _process("blocked.exe", 42, "created")
    values = iter(responses)
    calls: list[list[str]] = []

    def runner(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    enforcer = WindowsPolicyEnforcer(
        tmp_path / "state.json",
        runner=runner,
        process_provider=lambda: (process,),
        process_identity_checker=lambda _process: next(values),
    )
    result = enforcer._terminate_denied(["blocked.exe"], session_id="SES-1")

    assert result[0].outcome == expected_outcome
    assert result[0].termination_attempted is attempted
    assert any(call[:3] == ["taskkill", "/F", "/PID"] for call in calls) is attempted
    assert (enforcer.health_error is not None) is degraded


def test_process_identity_query_failure_retries_on_next_maintenance_cycle(
    tmp_path: Path,
) -> None:
    process = _process("blocked.exe", 42, "created")
    values = iter(
        (
            ProcessIdentityStatus.QUERY_FAILED,
            ProcessIdentityStatus.EXISTS,
            ProcessIdentityStatus.NOT_FOUND,
        )
    )
    taskkills: list[list[str]] = []

    def runner(args, **_kwargs):
        if args[:3] == ["taskkill", "/F", "/PID"]:
            taskkills.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    enforcer = WindowsPolicyEnforcer(
        tmp_path / "state.json",
        runner=runner,
        process_provider=lambda: (process,),
        process_identity_checker=lambda _process: next(values),
    )

    first = enforcer._terminate_denied(["blocked.exe"], session_id="SES-1")
    second = enforcer._terminate_denied(["blocked.exe"], session_id="SES-1")

    assert first[0].outcome == "QUERY_FAILED"
    assert second[0].outcome == "TERMINATED"
    assert taskkills == [["taskkill", "/F", "/PID", "42"]]


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (3, "", ProcessIdentityStatus.NOT_FOUND),
        (1, "query failed", ProcessIdentityStatus.QUERY_FAILED),
        (0, "not-json", ProcessIdentityStatus.QUERY_FAILED),
        (
            0,
            json.dumps({"Pid": 42, "CreationTime": "created"}),
            ProcessIdentityStatus.EXISTS,
        ),
        (
            0,
            json.dumps({"Pid": 42, "CreationTime": "reused"}),
            ProcessIdentityStatus.NOT_FOUND,
        ),
    ],
)
def test_windows_process_identity_query_distinguishes_failure_and_absence(
    tmp_path: Path,
    returncode: int,
    stdout: str,
    expected: ProcessIdentityStatus,
) -> None:
    def runner(args, **_kwargs):
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    enforcer = WindowsPolicyEnforcer(tmp_path / "state.json", runner=runner)

    assert enforcer._windows_process_identity_exists(
        _process("blocked.exe", 42, "created")
    ) == expected


def test_hosts_integrity_ignores_unmanaged_lines_and_detects_managed_change(
    tmp_path: Path,
) -> None:
    events: list[dict] = []
    enforcer = _enforcer(
        tmp_path,
        lambda: (),
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""),
        events,
    )
    enforcer._hosts_path.write_text(
        "10.0.0.1 internal.example\n"
        f"{POLICY_MARKER_START}\n"
        "127.0.0.1 blocked.example\n"
        "127.0.0.1 www.blocked.example\n"
        f"{POLICY_MARKER_END}\n",
        encoding="utf-8",
    )

    assert enforcer._hosts_match(("blocked.example",)) is True
    enforcer._hosts_path.write_text(
        enforcer._hosts_path.read_text(encoding="utf-8").replace(
            "127.0.0.1 blocked.example", "127.0.0.1 changed.example"
        ),
        encoding="utf-8",
    )
    assert enforcer._hosts_match(("blocked.example",)) is False


def test_hosts_recovery_reports_changed_then_verified_recovered(tmp_path: Path) -> None:
    events: list[dict] = []
    enforcer = _enforcer(
        tmp_path,
        lambda: (),
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""),
        events,
    )

    enforcer._recover_hosts("SES-1", ("blocked.example",))

    assert [event["action"] for event in events] == ["CONFIG_CHANGED", "RECOVERED"]
    assert events[-1]["details"]["recovery_status"] == "RECOVERY_SUCCEEDED"
    assert enforcer._hosts_match(("blocked.example",)) is True


def test_usb_recovery_failure_is_critical_and_degrades_enforcer(tmp_path: Path) -> None:
    events: list[dict] = []

    def runner(args, **_kwargs):
        if args[:2] == ["reg", "add"]:
            return subprocess.CompletedProcess(args, 1, "", "denied")
        return subprocess.CompletedProcess(args, 0, "Start REG_DWORD 0x3", "")

    enforcer = _enforcer(tmp_path, lambda: (), runner, events)
    enforcer._recover_usb("SES-1")

    assert [event["action"] for event in events] == [
        "CONFIG_CHANGED",
        "RECOVERY_FAILED",
    ]
    assert events[-1]["severity"] == "CRITICAL"
    assert enforcer.health_error is not None


def test_journal_supports_concurrent_writers_without_duplicate_ids(tmp_path: Path) -> None:
    journal = SecurityEventJournal(tmp_path / "concurrent.db")
    errors: list[Exception] = []

    def append(index: int) -> None:
        try:
            journal.append(_event(f"evt-{index}"))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=append, args=(index,)) for index in range(25)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(journal.pending(session_id="SES-1", limit=100)) == 25


def test_event_contract_rejects_payload_over_64_kib() -> None:
    with pytest.raises(ValidationError, match="64 KiB"):
        Event(
            protocol_version=2,
            event_id="evt-large",
            session_id="SES-1",
            agent_id="PC01",
            event_type=EventType.ENFORCEMENT_ACTION,
            occurred_at=NOW,
            payload={"value": "x" * (64 * 1024)},
        )
