"""P1-3 部署边界：宿主机端口只能绑定 127.0.0.1。"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"
START_LOCAL = ROOT / "start-local.ps1"
START_SH = ROOT / "start.sh"


def test_compose_ports_bind_loopback_only():
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    for name in ("postgres", "backend", "frontend"):
        ports = [str(item) for item in services[name].get("ports") or []]
        assert ports, f"{name} 缺少宿主机端口映射"
        for mapping in ports:
            host_ip = mapping.split(":", 1)[0]
            assert host_ip == "127.0.0.1", f"{name} 端口未绑定本机: {mapping}"


def test_start_local_binds_loopback():
    script = START_LOCAL.read_text(encoding="utf-8")
    assert "--host 127.0.0.1 --port $BackendPort" in script
    assert "preview --port $FrontendPort --host 127.0.0.1" in script


def test_shell_start_binds_loopback_by_default():
    script = START_SH.read_text(encoding="utf-8")
    assert '--host "${SERVER_HOST:-127.0.0.1}"' in script
