from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import backend_watchdog
from backend_watchdog import build_server_command, classify_health
from install_backend_watchdog import (
    _decode_task_output, build_task_command, patch_battery_xml,
)


def test_health_payload_is_healthy() -> None:
    state, detail = classify_health({"status": "ok", "database": "connected",
                                     "latest_trade_date": "2026-10-30"})
    assert state == "healthy"
    assert "2026-10-30" in detail


def test_health_payload_is_degraded_when_database_down() -> None:
    state, _ = classify_health({"status": "ok", "database": "disconnected"})
    assert state == "degraded"


def test_health_payload_is_unhealthy_for_bad_body() -> None:
    assert classify_health({"status": "error"})[0] == "unhealthy"
    assert classify_health(None)[0] == "unhealthy"


def test_server_command_uses_uvicorn() -> None:
    command = build_server_command("127.0.0.1", 8123)
    assert command[1:4] == ["-m", "uvicorn", "app.main:app"]
    assert command[-2:] == ["--port", "8123"]


def test_spawn_flags_do_not_let_child_allocate_a_console_window() -> None:
    flags = backend_watchdog.spawn_flags()
    if os.name == "nt":
        assert flags & subprocess.CREATE_NO_WINDOW
        assert not flags & subprocess.DETACHED_PROCESS
    else:
        assert flags == 0


def test_task_command_quotes_both_paths() -> None:
    executable = Path("C:/venv/pythonw.exe")
    script = Path("C:/repo/watchdog.py")
    assert build_task_command(executable, script) == f'"{executable}" "{script}"'


def test_patch_battery_xml_disables_both_guards() -> None:
    xml = (
        "<Settings><DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>"
        "<StopIfGoingOnBatteries>true</StopIfGoingOnBatteries></Settings>"
    )
    patched, changed = patch_battery_xml(xml)
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in patched
    assert "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in patched
    assert changed == ["DisallowStartIfOnBatteries", "StopIfGoingOnBatteries"]
    assert patch_battery_xml(patched)[1] == []


def test_decode_task_output_handles_ansi_and_utf16() -> None:
    assert "Task" in _decode_task_output(b'<?xml version="1.0"?>\r\n<Task ')
    assert "Task" in _decode_task_output("<Task>".encode("utf-16"))


@pytest.fixture
def isolated_var(monkeypatch, tmp_path):
    monkeypatch.setattr(backend_watchdog, "VAR_DIR", tmp_path)
    monkeypatch.setattr(backend_watchdog, "LOCK_FILE", tmp_path / "watchdog.lock")
    monkeypatch.setattr(
        backend_watchdog, "_setup_logging", lambda: logging.getLogger("test.watchdog")
    )
    return tmp_path


def test_main_skips_when_healthy(isolated_var, monkeypatch) -> None:
    monkeypatch.setattr(
        backend_watchdog, "_health_state", lambda *args, **kwargs: ("healthy", "ok")
    )
    assert backend_watchdog.main(["--port", "8123"]) == 0


def test_main_refuses_duplicate_when_port_open_but_unhealthy(
    isolated_var, monkeypatch
) -> None:
    monkeypatch.setattr(
        backend_watchdog, "_health_state", lambda *args, **kwargs: ("down", "refused")
    )
    monkeypatch.setattr(backend_watchdog, "_port_open", lambda *args, **kwargs: True)
    assert backend_watchdog.main(["--port", "8123"]) == 2
