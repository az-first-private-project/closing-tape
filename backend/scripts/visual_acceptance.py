"""P3-4 生产构建浏览器验收：四档视口 × 明暗主题 × 核心页面。

对 `vite preview` 的生产产物执行同一套断言：

  - 页面主标题可见、正文非空；
  - 文档无横向溢出（scrollWidth <= innerWidth + 1）；
  - 主内容不含 `NaN` / `undefined` / `-0.0%` 假值；
  - 明暗主题确实切换（html.dark 与 body 背景色变化）；
  - 导航常驻可见；
  - 无未捕获 JS 异常；网络资源加载失败只记 warning，不判失败。

每个用例输出一张视口截图，最终写 `results.json` 并以退出码表示成败。

用法（backend/ 下）：
    python scripts/visual_acceptance.py --base-url http://127.0.0.1:4173
    python scripts/visual_acceptance.py --only-pages dashboard --viewports laptop
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

DEFAULT_BASE = os.getenv("GFNA_WEB_BASE", "http://127.0.0.1:4173")
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "var" / "visual_acceptance"

VIEWPORTS = {
    "mobile": (375, 812),
    "tablet": (768, 1024),
    "laptop": (1440, 900),
    "desktop": (1920, 1080),
}

PAGES = [
    ("home", "/", "盘后情报"),
    ("dashboard", "/dashboard", "盘后分析"),
    ("algo-center", "/algo-center", "量化选股"),
    ("paper", "/paper", "纸面组合跟踪"),
    ("stock", "/stocks/600519", ""),
]

AUDIT_JS = """() => {
  const root = document.documentElement;
  const main = document.querySelector('main');
  const text = main ? main.innerText : '';
  const nav = document.querySelector('nav');
  const card = [...document.querySelectorAll('.card')].find(
    (el) => el.textContent.includes('TOP 10')
  );
  const scoreRows = card ? card.querySelectorAll('button').length : 0;
  const sparklines = card
    ? [...card.querySelectorAll('svg[data-sparkline]')].filter(
        (el) => el.getClientRects().length > 0
      ).length
    : 0;
  return {
    darkClass: root.classList.contains('dark'),
    colorScheme: root.style.colorScheme,
    background: getComputedStyle(document.body).backgroundColor,
    scrollWidth: root.scrollWidth,
    innerWidth: window.innerWidth,
    navVisible: !!nav && nav.getClientRects().length > 0,
    mainTextLen: text.length,
    h1: (document.querySelector('main h1')?.textContent || '').trim(),
    forbidden: ['NaN', 'undefined', '-0.0%'].filter((token) => text.includes(token)),
    scoreRows,
    sparklines,
    canvas: main ? main.querySelectorAll('canvas').length : 0,
  };
}"""


@dataclass
class Result:
    page: str
    path: str
    viewport: str
    width: int
    height: int
    theme: str
    metrics: dict
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    screenshot: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P3-4 视口与主题生产验收")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--only-pages", default="", help="逗号分隔：home,dashboard,...")
    parser.add_argument("--viewports", default="mobile,tablet,laptop,desktop")
    parser.add_argument("--themes", default="dark,light")
    return parser.parse_args()


def wait_ready(page, marker: str) -> None:
    if marker:
        page.locator("main").get_by_text(marker, exact=False).first.wait_for(timeout=30000)
    else:
        page.wait_for_selector("main h1", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=6000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(300)


def run_case(browser, base_url: str, output: Path, page_spec, viewport_name: str, theme: str) -> Result:
    label, path, marker = page_spec
    width, height = VIEWPORTS[viewport_name]
    result = Result(label, path, viewport_name, width, height, theme, {})
    errors: list[str] = []
    page_errors: list[str] = []

    context = browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=1,
    )
    context.add_init_script(
        script=f"try {{ localStorage.setItem('gfna-theme', '{theme}') }} catch (e) {{}}"
    )
    page = context.new_page()
    page.on(
        "console",
        lambda m: errors.append(m.text) if m.type == "error" else None,
    )
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))

    separator = "&" if "?" in path else "?"
    try:
        page.goto(f"{base_url}{path}{separator}intro=0", wait_until="domcontentloaded")
        wait_ready(page, marker)
        metrics = page.evaluate(AUDIT_JS)
        result.metrics = metrics

        if metrics["darkClass"] != (theme == "dark"):
            result.failures.append(f"主题未生效：darkClass={metrics['darkClass']} theme={theme}")
        if metrics["colorScheme"] not in ("", theme):
            result.failures.append(f"colorScheme={metrics['colorScheme']} 与 {theme} 不符")
        if metrics["scrollWidth"] > metrics["innerWidth"] + 1:
            result.failures.append(
                f"横向溢出：scrollWidth={metrics['scrollWidth']} > innerWidth={metrics['innerWidth']}"
            )
        if metrics["forbidden"]:
            result.failures.append(f"出现假值：{metrics['forbidden']}")
        if not metrics["navVisible"]:
            result.failures.append("导航不可见")
        if metrics["mainTextLen"] < 20:
            result.failures.append(f"正文过短：{metrics['mainTextLen']}")
        if marker and marker not in metrics["h1"] and label != "home":
            result.failures.append(f"主标题不符：{metrics['h1']!r} 缺少 {marker!r}")
        if label == "stock" and not metrics["h1"]:
            result.failures.append("个股页缺少股票名称标题")
        if label == "dashboard" and metrics["scoreRows"] not in (0, 10):
            result.failures.append(f"评分榜行数异常：{metrics['scoreRows']}")
        if page_errors:
            result.failures.append(f"未捕获异常：{page_errors[:2]}")
        network_errors = [item for item in errors if "Failed to load resource" in item]
        other_errors = [item for item in errors if "Failed to load resource" not in item]
        if other_errors:
            result.failures.append(f"Console error：{other_errors[:2]}")
        if network_errors:
            result.warnings.append(f"资源加载失败 {len(network_errors)} 条")
    except Exception as exc:  # 保留失败截图与原因，不中断整轮
        result.failures.append(f"{type(exc).__name__}: {exc}")
    finally:
        shot = output / f"{label}-{width}x{height}-{theme}.png"
        try:
            page.screenshot(path=str(shot), animations="disabled")
            result.screenshot = shot.name
        except Exception as exc:
            result.warnings.append(f"截图失败：{type(exc).__name__}")
        context.close()
    return result


def main() -> int:
    args = parse_args()
    selected_pages = [item.strip() for item in args.only_pages.split(",") if item.strip()]
    pages = [spec for spec in PAGES if not selected_pages or spec[0] in selected_pages]
    viewports = [item.strip() for item in args.viewports.split(",") if item.strip()]
    themes = [item.strip() for item in args.themes.split(",") if item.strip()]
    unknown = [name for name in viewports if name not in VIEWPORTS]
    if unknown:
        raise SystemExit(f"未知视口：{unknown}，可选 {list(VIEWPORTS)}")

    args.output.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for viewport_name in viewports:
            for theme in themes:
                for spec in pages:
                    result = run_case(browser, args.base_url, args.output, spec, viewport_name, theme)
                    results.append(result)
                    status = "PASS" if not result.failures else "FAIL"
                    print(f"[{status}] {spec[0]:<11} {viewport_name:<7} {theme:<5} {result.screenshot}")
                    for reason in result.failures:
                        print(f"       - {reason}")
        browser.close()

    # 同一页面/视口下，明暗主题的背景色必须不同
    backgrounds: dict[tuple[str, str], dict[str, str]] = {}
    for item in results:
        if not item.failures:
            backgrounds.setdefault((item.page, item.viewport), {})[item.theme] = item.metrics.get(
                "background", ""
            )
    for (label, viewport_name), values in backgrounds.items():
        if len(values) == 2 and len(set(values.values())) == 1:
            reason = f"明暗主题背景色相同：{values}"
            for item in results:
                if item.page == label and item.viewport == viewport_name:
                    item.failures.append(reason)

    failed = [item for item in results if item.failures]
    summary = {
        "baseUrl": args.base_url,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "viewports": viewports,
        "themes": themes,
        "pages": [spec[0] for spec in pages],
        "total": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "failures": [
            {
                "page": item.page,
                "viewport": item.viewport,
                "theme": item.theme,
                "reasons": item.failures,
            }
            for item in failed
        ],
        "warnings": [
            {"page": item.page, "viewport": item.viewport, "theme": item.theme, "items": item.warnings}
            for item in results
            if item.warnings
        ],
        "cases": [
            {
                "page": item.page,
                "path": item.path,
                "viewport": item.viewport,
                "width": item.width,
                "height": item.height,
                "theme": item.theme,
                "ok": not item.failures,
                "screenshot": item.screenshot,
                "metrics": item.metrics,
            }
            for item in results
        ],
    }
    (args.output / "results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n{summary['passed']}/{summary['total']} passed, {summary['failed']} failed")
    print(f"results: {args.output / 'results.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
