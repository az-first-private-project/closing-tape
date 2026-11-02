"""GFNA 后端守护：探活，服务退出后拉起 uvicorn。

由 Windows 计划任务每 5 分钟调用，也可手动执行。退出码：
0 已健康或已拉起；1 拉起失败；2 端口被占用但健康检查失败；3 另一个守护实例正在运行。
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
VAR_DIR = BACKEND_DIR / "var"
WATCHDOG_LOG = VAR_DIR / "backend_watchdog.log"
UVICORN_LOG = VAR_DIR / "uvicorn.log"
PID_FILE = VAR_DIR / "backend.pid"
LOCK_FILE = VAR_DIR / "backend_watchdog.lock"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
HEALTH_TIMEOUT = 3.0
START_TIMEOUT = 30.0
UVICORN_LOG_MAX_BYTES = 20 * 1024 * 1024


def classify_health(payload: object) -> tuple[str, str]:
    """把 /health 响应归类为 healthy / degraded / unhealthy。"""
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return "unhealthy", str(payload)[:300]
    if payload.get("database") != "connected":
        return "degraded", f"database={payload.get('database')}"
    return "healthy", f"latest_trade_date={payload.get('latest_trade_date')}"


def _health_state(host: str, port: int, timeout: float = HEALTH_TIMEOUT) -> tuple[str, str]:
    url = f"http://{host}:{port}/health"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return "down", f"{type(exc).__name__}: {exc}"
    return classify_health(payload)


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _setup_logging() -> logging.Logger:
    VAR_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("gfna.watchdog")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        WATCHDOG_LOG, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    if sys.stdout is not None:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        logger.addHandler(console)
    return logger


def _acquire_lock():
    """防止计划任务与手动执行同时拉起两个 uvicorn。"""
    VAR_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_FILE, "a+", encoding="utf-8")
    if handle.tell() == 0:
        handle.write("lock\n")
        handle.flush()
    handle.seek(0)
    try:
        import msvcrt
    except ImportError:
        return handle
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def _rotate_uvicorn_log() -> None:
    if not UVICORN_LOG.exists() or UVICORN_LOG.stat().st_size < UVICORN_LOG_MAX_BYTES:
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    UVICORN_LOG.replace(UVICORN_LOG.with_name(f"uvicorn_{stamp}.log"))
    for old in sorted(VAR_DIR.glob("uvicorn_*.log"))[:-3]:
        old.unlink(missing_ok=True)


def _server_python() -> Path:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        candidate = exe.with_name("python.exe")
        if candidate.exists():
            return candidate
    return exe


def build_server_command(host: str, port: int) -> list[str]:
    return [
        str(_server_python()), "-m", "uvicorn", "app.main:app",
        "--host", host, "--port", str(port),
    ]


def _start_server(
    host: str, port: int, timeout: float, logger: logging.Logger
) -> tuple[bool, str]:
    _rotate_uvicorn_log()
    log_file = open(UVICORN_LOG, "a", encoding="utf-8")
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        proc = subprocess.Popen(
            build_server_command(host, port),
            cwd=str(BACKEND_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            close_fds=True,
        )
    finally:
        log_file.close()

    try:
        PID_FILE.write_text(
            json.dumps({
                "pid": proc.pid,
                "host": host,
                "port": port,
                "started_at": datetime.now().isoformat(timespec="seconds"),
            }),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("写入 PID 文件失败：%s", exc)

    logger.info("已拉起 uvicorn pid=%s，等待健康检查", proc.pid)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state, detail = _health_state(host, port)
        if state == "healthy":
            return True, f"pid={proc.pid} {detail}"
        if proc.poll() is not None:
            return False, f"uvicorn 退出码 {proc.returncode}，详见 {UVICORN_LOG}"
        time.sleep(0.5)
    return False, f"健康检查超时 {timeout:.0f}s，详见 {UVICORN_LOG}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GFNA 后端守护")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--start-timeout", type=float, default=START_TIMEOUT)
    args = parser.parse_args(argv)

    logger = _setup_logging()
    lock = _acquire_lock()
    if lock is None:
        logger.info("另一个守护实例正在运行，跳过")
        return 3

    try:
        state, detail = _health_state(args.host, args.port)
        if state == "healthy":
            logger.info("后端健康，跳过：%s", detail)
            return 0
        if state == "degraded":
            logger.warning("后端在运行但数据库未连接，不重启：%s", detail)
            return 0
        if _port_open(args.host, args.port):
            logger.error("端口 %s 被占用但健康检查失败，拒绝重复启动：%s", args.port, detail)
            return 2

        logger.warning("后端不可用（%s），开始拉起", detail)
        ok, message = _start_server(args.host, args.port, args.start_timeout, logger)
        if ok:
            logger.info("后端已恢复：%s", message)
            return 0
        logger.error("后端拉起失败：%s", message)
        return 1
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
