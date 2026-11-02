"""注册 / 移除 GFNA 后端守护的 Windows 计划任务。"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
WATCHDOG_SCRIPT = BACKEND_DIR / "scripts" / "backend_watchdog.py"

TASKS: dict[str, tuple[str, str | None]] = {
    "GFNA-Backend-Watchdog": ("MINUTE", "1"),
}
REMOVE_TASKS = ("GFNA-Backend-Watchdog", "GFNA-Backend-Startup")
BATTERY_TAGS = ("DisallowStartIfOnBatteries", "StopIfGoingOnBatteries")


def _pythonw() -> Path:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.exists() else exe


def build_task_command(executable: Path, script: Path) -> str:
    return f'"{executable}" "{script}"'


def patch_battery_xml(xml: str) -> tuple[str, list[str]]:
    """关闭“仅交流电时运行”，否则笔记本用电池时守护任务不会启动。"""
    patched = xml
    changed: list[str] = []
    for tag in BATTERY_TAGS:
        pattern = re.compile(rf"(<{tag}>)true(</{tag}>)")
        if pattern.search(patched):
            patched = pattern.sub(rf"\g<1>false\g<2>", patched)
            changed.append(tag)
    return patched, changed


def _decode_task_output(data: bytes) -> str:
    """schtasks 的 XML 声明写 UTF-16，重定向时实际输出却是 ANSI/UTF-8。"""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def allow_battery(name: str) -> int:
    """导出任务 XML → 改写电池设置 → 覆盖导入（schtasks CLI 无对应开关）。"""
    query = subprocess.run(
        ["schtasks", "/Query", "/TN", name, "/XML"], capture_output=True
    )
    if query.returncode != 0:
        message = _decode_task_output(query.stderr).strip()
        print(message or "导出任务 XML 失败")
        return query.returncode

    patched, changed = patch_battery_xml(_decode_task_output(query.stdout))
    if not changed:
        print("! 任务 XML 未找到电池设置，跳过")
        return 0

    with tempfile.NamedTemporaryFile(
        "w", suffix=".xml", encoding="utf-16", delete=False
    ) as handle:
        handle.write(patched)
        temp_path = Path(handle.name)
    try:
        result = subprocess.run(
            ["schtasks", "/Create", "/TN", name, "/XML", str(temp_path), "/F"],
            capture_output=True, text=True,
        )
        output = (result.stdout or result.stderr).strip()
        if output:
            print(output)
        if result.returncode != 0:
            return result.returncode
    finally:
        temp_path.unlink(missing_ok=True)
    print(f"~ 已允许电池供电时运行：{', '.join(changed)}")
    return 0


def install() -> int:
    command = build_task_command(_pythonw(), WATCHDOG_SCRIPT)
    for name, (schedule, interval) in TASKS.items():
        args = [
            "schtasks", "/Create", "/TN", name, "/TR", command,
            "/SC", schedule, "/IT", "/RL", "LIMITED", "/F",
        ]
        if interval is not None:
            args += ["/MO", interval]
        print("+", " ".join(args))
        result = subprocess.run(args, capture_output=True, text=True)
        output = (result.stdout or result.stderr).strip()
        if output:
            print(output)
        if result.returncode != 0:
            return result.returncode
        code = allow_battery(name)
        if code != 0:
            return code
    return 0


def remove() -> int:
    for name in REMOVE_TASKS:
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", name, "/F"],
            capture_output=True, text=True,
        )
        output = (result.stdout or result.stderr).strip()
        if output:
            print(output)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="安装 GFNA 后端守护计划任务")
    parser.add_argument("--remove", action="store_true", help="删除已注册的计划任务")
    args = parser.parse_args()
    return remove() if args.remove else install()


if __name__ == "__main__":
    raise SystemExit(main())
