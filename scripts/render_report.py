#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
サクラ新聞 · 日报渲染脚本
把 issueNN.html 里的每个 .page（376×501 的 3:4 版面）截成 1080×1440 PNG。

用法:
  render_report.py --html report/issue01/issue01.html            # 输出到同目录 pages/
  render_report.py --html <路径> --out <目录> [--wait 2500]

原理: Playwright 以 device_scale_factor=2.872 打开本地 HTML
      （376 × 2.872 ≈ 1080），逐个 .page 元素截图。
"""
import argparse, pathlib, sys
from playwright.sync_api import sync_playwright

SCALE = 1080 / 376  # .page 宽 376px → 1080px


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", required=True, help="报纸 HTML 文件路径")
    ap.add_argument("--out", help="输出目录（默认 = HTML 同目录下 pages/）")
    ap.add_argument("--wait", type=int, default=2500, help="加载等待毫秒（图片多可调大）")
    args = ap.parse_args()

    html = pathlib.Path(args.html).resolve()
    if not html.exists():
        print("HTML 不存在:", html); sys.exit(2)
    out = pathlib.Path(args.out) if args.out else html.parent / "pages"
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 460, "height": 560},
            device_scale_factor=SCALE,
        )
        page = ctx.new_page()
        page.goto(html.as_uri())
        page.wait_for_timeout(args.wait)
        pages = page.locator(".page")
        n = pages.count()
        if n == 0:
            print("没找到 .page 元素"); sys.exit(2)
        for i in range(n):
            f = out / f"page_{i+1}.png"
            pages.nth(i).screenshot(path=str(f))
            print(f"  ✓ {f.name}")
        browser.close()
    print(f"完成: {n} 页 -> {out}")


if __name__ == "__main__":
    main()
