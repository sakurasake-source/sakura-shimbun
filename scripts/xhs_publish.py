#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
サクラ新聞 · Phase 3 半自动上稿（Playwright 驱动小红书官方网页版）
- 读 Chrome 的小红书登录 cookie（browser_cookie3）注入 Playwright
- 从工作文件夹直接 setInputFiles 上传图片（不弹原生框、不用手选）
- 填标题/正文/tag
- 默认 mode=stop：填完就停，绝不点发布，开着窗口等你审核后亲手发
- mode=draft：填完点「存草稿」进草稿箱

用法:
  xhs_publish.py --dir "<含 publish_payload.json 和 img_*.jpg 的文件夹>" [--mode stop|draft] [--hold 1800]
"""
import argparse, json, pathlib, sys, time, re
import browser_cookie3
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0 Safari/537.36")
PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"


def load_cookies():
    cj = browser_cookie3.chrome(domain_name="xiaohongshu.com")
    out = []
    for c in cj:
        out.append({
            "name": c.name, "value": c.value,
            "domain": c.domain if c.domain.startswith(".") else "." + c.domain.lstrip("."),
            "path": c.path or "/",
            "secure": bool(c.secure),
            "httpOnly": False,
            "expires": float(c.expires) if c.expires else -1,
        })
    if not any(c["name"] == "web_session" for c in out):
        raise RuntimeError("没读到小红书登录 cookie(web_session)，请在 Chrome 登录 xiaohongshu.com")
    return out


def first_visible(page, selectors, timeout=45000):
    """依次尝试多个 selector，返回第一个可见的 locator。"""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        for sel in selectors:
            loc = page.locator(sel).first
            try:
                if loc.count() > 0 and loc.is_visible():
                    return loc
            except Exception:
                pass
        time.sleep(0.5)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--mode", choices=["stop", "draft", "private", "probe_vis"], default="stop")
    ap.add_argument("--hold", type=int, default=1800, help="stop 模式下窗口保持打开的秒数")
    args = ap.parse_args()

    d = pathlib.Path(args.dir)
    payload = json.loads((d / "publish_payload.json").read_text(encoding="utf-8"))
    images = [str(d / n) for n in payload.get("images", [])]
    video = str(d / payload["video"]) if payload.get("video") else None

    # ── payload 校验（发布前把 XHS 的限制全查一遍，别到页面上才炸）──
    errs = []
    title = payload.get("title", "")
    if not title:
        errs.append("缺 title")
    elif len(title) > 20:
        errs.append(f"标题 {len(title)} 字 > 20 字上限: {title}")
    body = payload.get("body", "")
    if len(body) > 1000:
        errs.append(f"正文 {len(body)} 字 > 1000 字上限")
    tags = payload.get("tags", [])
    if len(tags) > 10:
        errs.append(f"tag {len(tags)} 个 > 10 个上限")
    if not images and not video:
        errs.append("images 和 video 都为空")
    if images and video:
        errs.append("images 和 video 只能二选一")
    if len(images) > 18:
        errs.append(f"图片 {len(images)} 张 > 18 张上限")
    for p in (images + ([video] if video else [])):
        if not pathlib.Path(p).exists():
            errs.append(f"缺文件: {p}")
    if errs:
        print("payload 校验不通过:")
        for e in errs:
            print("  ✗", e)
        sys.exit(2)

    cookies = load_cookies()
    print(f"cookie ok（{len(cookies)}个），{'视频1个' if video else f'图{len(images)}张'}，准备启动浏览器…")

    with sync_playwright() as p:
        # 持久化 profile：固定 user_data_dir，设备指纹稳定，
        # 风控视角下每次发帖都是同一台机器（而不是每次一台新设备）
        profile_dir = pathlib.Path(__file__).resolve().parent.parent / "data" / "xhs_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        ctx = p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
            user_agent=UA,
            viewport={"width": 1440, "height": 900},
        )
        ctx.add_cookies(cookies)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # goto 偶发网络超时（已实测发生过），自动重试 3 次
        for attempt in range(1, 4):
            try:
                page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=45000)
                break
            except Exception as e:
                if attempt == 3:
                    raise
                print(f"goto 第{attempt}次失败（{type(e).__name__}），{attempt*5}s 后重试…")
                page.wait_for_timeout(attempt * 5000)
        page.wait_for_timeout(4000)
        page.screenshot(path=str(d / "xhs_load.png"), full_page=True)
        print("加载态已截图 xhs_load.png，标题=", page.title())

        if video:
            # 视频：默认就是「上传视频」tab，找视频上传框
            want = [".mp4", ".mov", ".mkv", ".m4v", "video"]
            title_timeout = 240000  # 视频处理久
        else:
            # 切到「上传图文」——用 JS click 绕过视口/遮挡
            tab = first_visible(page, ["text=上传图文"], timeout=15000)
            if tab:
                try:
                    tab.evaluate("el => el.click()")
                except Exception:
                    tab.click(force=True)
                print("已切到『上传图文』")
            else:
                print("警告: 没找到『上传图文』tab")
            page.wait_for_timeout(1800)
            want = [".jpg", ".jpeg", ".png", ".webp", "image"]
            title_timeout = 90000

        # 找对应上传框（accept 匹配），绕过原生弹框
        finp = None
        for _ in range(40):
            inputs = page.locator('input[type="file"]')
            for j in range(inputs.count()):
                el = inputs.nth(j)
                acc = (el.get_attribute("accept") or "").lower()
                if any(x in acc for x in want):
                    finp = el
                    break
            if finp:
                break
            page.wait_for_timeout(500)
        if not finp:
            page.screenshot(path=str(d / "xhs_debug.png"), full_page=True)
            print("没找到上传框，已截图 xhs_debug.png"); page.wait_for_timeout(args.hold * 1000); return
        finp.set_input_files(video if video else images)
        print(f"已 setInputFiles（{'视频框' if video else '图片框'}），等待上传解析…")

        # 等编辑器出现（标题输入框）
        title_loc = first_visible(page, [
            'input[placeholder*="标题"]',
            'input[placeholder*="填写标题"]',
            'textarea[placeholder*="标题"]',
        ], timeout=title_timeout)
        if not title_loc:
            page.screenshot(path=str(d / "xhs_debug.png"), full_page=True)
            print("没等到标题框，已截图 xhs_debug.png，请人工看下选择器")
            page.wait_for_timeout(args.hold * 1000)
            return

        title_loc.click()
        title_loc.fill(payload["title"])
        print("标题已填")

        # 正文（contenteditable 富文本）
        body_loc = first_visible(page, [
            'div[contenteditable="true"]',
            '.ql-editor',
            'div[data-placeholder*="正文"]',
            'textarea[placeholder*="正文"]',
        ], timeout=15000)
        if body_loc:
            body_loc.click()
            page.keyboard.type(payload["body"])
            page.keyboard.press("Enter")     # tag 另起一行
            # tag：输「#关键词」→ 等下拉 → 点下拉首项落地为「真话题」（空格兜底）
            # 只靠空格会让 #词 停留为纯文本；必须选中下拉项才是可点击话题
            topic_sel = [
                ".mention-list__item", ".mention-item",
                "[class*='topic'] [class*='item']",
                ".d-popover [class*='item']",
                "#creator-editor-topic-container [class*='item']",
            ]
            made = 0
            for t in payload["tags"]:
                page.keyboard.type("#" + t)
                page.wait_for_timeout(1200)  # 等话题下拉加载
                picked = False
                for sel in topic_sel:
                    it = page.locator(sel).first
                    try:
                        if it.count() and it.is_visible():
                            it.click(timeout=1500)
                            picked = True
                            break
                    except Exception:
                        pass
                if not picked:
                    page.keyboard.type(" ")  # 兜底：空格触发（可能仅纯文本）
                page.wait_for_timeout(400)
                page.keyboard.type(" ")      # tag 间加空格分隔
                made += 1
            page.keyboard.press("Escape")    # 关掉最后一个话题下拉浮层
            page.wait_for_timeout(500)
            print(f"正文+tag 已填（tag {made} 个，尽量选中下拉真话题）")
        else:
            print("没找到正文编辑器，仅完成了图片+标题")

        page.wait_for_timeout(1500)
        shot = d / "xhs_filled.png"
        page.screenshot(path=str(shot), full_page=True)
        print("已截图:", shot)

        if args.mode == "probe_vis":
            # 探测「可见范围/仅自己可见」控件位置与文案，不发布
            page.screenshot(path=str(d / "xhs_vis_probe.png"), full_page=True)
            try:
                els = page.eval_on_selector_all(
                    "button,[role=button],span,div,label",
                    "ns => ns.filter(n=>/仅自己|谁可以看|公开可见|可见范围|权限|所有人可见|仅互关/.test((n.textContent||''))"
                    " && (n.textContent||'').trim().length<16)"
                    ".map(n=>({tag:n.tagName,cls:(n.className||'').toString().slice(0,45),txt:(n.textContent||'').trim()}))"
                    ".slice(0,20)")
                print("可见范围候选元素:", els)
            except Exception as e:
                print("dump失败", e)
            print("probe_vis 完成，已截图 xhs_vis_probe.png")
        elif args.mode == "private":
            # 1) 打开可见范围下拉并选「仅自己可见」
            card = page.locator(".permission-card-wrapper").first
            card.scroll_into_view_if_needed()
            page.wait_for_timeout(600)
            card.click()
            page.wait_for_timeout(900)
            page.screenshot(path=str(d / "xhs_vis_open.png"), full_page=True)
            for sel in ['.custom-option:has-text("仅自己可见")',
                        '.d-grid-item:has-text("仅自己可见")', 'text=仅自己可见']:
                loc = page.locator(sel).first
                try:
                    loc.wait_for(state="visible", timeout=4000)
                    loc.click()
                    break
                except Exception:
                    continue
            page.wait_for_timeout(900)
            # 2) 硬验证：读可见范围卡片文本
            cur = ""
            try:
                cur = page.locator(".permission-card-wrapper").first.inner_text()
            except Exception:
                pass
            page.screenshot(path=str(d / "xhs_vis_set.png"), full_page=True)
            print("可见范围文本:", cur.replace("\n", " ")[:60])
            if "仅自己可见" not in cur:
                print("!! 未确认为『仅自己可见』，安全中止，绝不发布。窗口保留供你检查。")
                page.wait_for_timeout(args.hold * 1000)
                return
            # 3) 确认无误 → 点 footer 红色「发布」（精确文本，避开发布笔记/定时发布）
            print("已确认『仅自己可见』，定位发布按钮…")
            pub = None
            for st in [
                lambda: page.get_by_text("发布", exact=True),
                lambda: page.locator("div", has_text=re.compile(r"^发布$")),
                lambda: page.locator("span", has_text=re.compile(r"^发布$")),
            ]:
                try:
                    loc = st().last
                    loc.wait_for(state="visible", timeout=3000)
                    pub = loc
                    break
                except Exception:
                    continue
            if pub:
                pub.scroll_into_view_if_needed()
                pub.click()
                print("已点『发布』(定位器)")
            else:
                # 坐标兜底：可见范围已硬验证=仅自己可见，footer红色发布键固定在底部中右
                print("定位器未命中，改用坐标点击底部『发布』红键")
                vw = page.viewport_size or {"width": 1440, "height": 900}
                page.mouse.click(int(vw["width"] * 0.515), vw["height"] - 45)
            page.wait_for_timeout(5000)
            page.screenshot(path=str(d / "xhs_published.png"), full_page=True)
            print("发布动作完成（仅自己可见）。当前URL:", page.url)
        elif args.mode == "draft":
            # 稳妥定位「暂存离开」：滚动到视口 + 自动等待
            btn = None
            for sel in ['button:has-text("暂存离开")', 'text=暂存离开',
                        'button:has-text("存草稿")', 'text=保存草稿']:
                loc = page.locator(sel).first
                try:
                    loc.wait_for(state="visible", timeout=6000)
                    loc.scroll_into_view_if_needed(timeout=3000)
                    btn = loc
                    break
                except Exception:
                    continue
            if btn:
                btn.click()
                print("已点『暂存离开』，等待可能的二次确认…")
                page.wait_for_timeout(1500)
                page.screenshot(path=str(d / "xhs_draft_1.png"), full_page=True)
                # 处理可能的二次确认弹窗
                confirm = first_visible(page, [
                    'button:has-text("确认")', 'button:has-text("确定")',
                    'button:has-text("离开")', 'text=确认离开', 'button:has-text("继续")',
                    '.d-modal button:has-text("确")',
                ], timeout=4000)
                if confirm:
                    confirm.click()
                    print("已点二次确认")
                    page.wait_for_timeout(3500)
                else:
                    print("未发现二次确认弹窗（可能已直接保存）")
                page.screenshot(path=str(d / "xhs_draft_2.png"), full_page=True)
                print("draft 流程结束，当前URL:", page.url)
            else:
                page.screenshot(path=str(d / "xhs_draft_notfound.png"), full_page=True)
                try:
                    els = page.eval_on_selector_all(
                        "button,[role=button],span,div",
                        "ns => ns.filter(n=>/暂存|存草稿|离开|发布/.test((n.textContent||''))"
                        " && (n.textContent||'').trim().length<12)"
                        ".map(n=>({tag:n.tagName,cls:(n.className||'').toString().slice(0,40),txt:(n.textContent||'').trim()}))"
                        ".slice(0,12)")
                    print("候选[暂存/离开/发布]元素:", els)
                except Exception as e:
                    print("dump失败", e)
                print("frames:", [f.url for f in page.frames])
                print("没找到存草稿按钮，已截图 xhs_draft_notfound.png")
        else:
            print(f"=== FILLED 完成，绝不自动发布。窗口保持 {args.hold}s，请审核后自己点『发布』 ===")
            page.wait_for_timeout(args.hold * 1000)

        ctx.close()


if __name__ == "__main__":
    main()
