#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
サクラ新聞 · Phase 1/2 采集脚本
每天 20:00 (JST) 由 launchd 触发，读取 Chrome 登录 cookie，抓取指定微博账号的
新博文（正文+图片+视频），去重后按日期落盘。不做任何发布，纯采集。

用法:
  daily_fetch.py                # 默认: 抓两个号第1页, 只留上次之后的新博文
  daily_fetch.py --since 2026-07-01   # 回溯到指定日期(翻页直到更早)
  daily_fetch.py --uid 6591486070     # 只抓某个号
  daily_fetch.py --id P9x...          # 抓指定单篇(mblogid), 用于置顶试跑/特殊指示
  daily_fetch.py --pages 3            # 每号抓前N页
  daily_fetch.py --no-media           # 不下载图片视频, 只存文本
"""
import argparse, json, os, re, sys, datetime, pathlib, time, html
import requests
import browser_cookie3

BASE = pathlib.Path(__file__).resolve().parent.parent
DATA = BASE / "data" / "weibo"
STATE = BASE / "data" / "weibo_state"
SEEN_FILE = STATE / "seen_ids.json"
LOG_FILE = STATE / "run.log"

# 采集目标：uid -> 备注
UIDS = {
    "5664006997": "搬运号(39saku_chan IG搬运)",
    "6591486070": "超话应援号(宮脇咲良唯一炽TOP)",
}
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
API = "https://weibo.com/ajax/statuses/mymblog"
LONGTEXT = "https://weibo.com/ajax/statuses/longtext"


def log(msg):
    STATE.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def build_session():
    cj = browser_cookie3.chrome(domain_name="weibo.com")
    names = {c.name for c in cj}
    if "SUB" not in names:
        raise RuntimeError("Chrome 里没读到微博登录 cookie(SUB)。请在 Chrome 登录 weibo.com 后再试。")
    s = requests.Session()
    s.cookies.update(cj)
    xsrf = next((c.value for c in cj if c.name == "XSRF-TOKEN"), "")
    s.headers.update({
        "User-Agent": UA,
        "Referer": "https://weibo.com/",
        "x-requested-with": "XMLHttpRequest",
        "x-xsrf-token": xsrf,
        "client-version": "v2.47.0",
    })
    return s


def parse_time(s):
    # "Fri Jul 10 21:21:43 +0800 2026"
    try:
        return datetime.datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
    except Exception:
        return None


def fetch_page(s, uid, page):
    r = s.get(API, params={"uid": uid, "page": page, "feature": 0}, timeout=20)
    r.raise_for_status()
    return r.json()


def get_longtext(s, mblogid):
    try:
        r = s.get(LONGTEXT, params={"id": mblogid}, timeout=20)
        return (r.json().get("data") or {}).get("longTextContent") or ""
    except Exception:
        return ""


def pic_urls(mblog):
    urls = []
    infos = mblog.get("pic_infos") or {}
    for pid in (mblog.get("pic_ids") or []):
        info = infos.get(pid) or {}
        best = info.get("largest") or info.get("large") or info.get("original") or {}
        if best.get("url"):
            urls.append(best["url"])
    return urls


def video_url(mblog):
    pi = mblog.get("page_info") or {}
    mi = pi.get("media_info") or {}
    for k in ("mp4_hd_url", "stream_url_hd", "mp4_sd_url", "stream_url"):
        if mi.get(k):
            return mi[k]
    return None


def clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def extract(s, mblog):
    """把一条 mblog 提炼成结构化记录。转发帖同时保留原文内容。"""
    text = html.unescape(mblog.get("text_raw") or "") or clean(mblog.get("text"))
    if mblog.get("isLongText"):
        lt = get_longtext(s, mblog.get("mblogid"))
        if lt:
            text = html.unescape(lt)
    def _n(v):
        try:
            return int(v)
        except Exception:
            return 0
    rec = {
        "id": str(mblog.get("id")),
        "mblogid": mblog.get("mblogid"),
        "url": f"https://weibo.com/{mblog.get('user',{}).get('id','')}/{mblog.get('mblogid')}",
        "created_at": mblog.get("created_at"),
        "created_iso": (parse_time(mblog.get("created_at")).isoformat()
                        if parse_time(mblog.get("created_at")) else None),
        "text": text,
        "source": clean(mblog.get("source")),
        "region": mblog.get("region_name"),
        "is_repost": bool(mblog.get("retweeted_status")),
        "likes": _n(mblog.get("attitudes_count")),
        "comments": _n(mblog.get("comments_count")),
        "reposts": _n(mblog.get("reposts_count")),
        "pic_urls": pic_urls(mblog),
        "video_url": video_url(mblog),
    }
    if mblog.get("retweeted_status"):
        rt = mblog["retweeted_status"]
        rt_text = html.unescape(rt.get("text_raw") or "") or clean(rt.get("text"))
        if rt.get("isLongText"):
            rt_text = html.unescape(get_longtext(s, rt.get("mblogid")) or "") or rt_text
        rec["repost_of"] = {
            "text": rt_text,
            "pic_urls": pic_urls(rt),
            "video_url": video_url(rt),
            "author": (rt.get("user") or {}).get("screen_name"),
        }
    return rec


def notify(title, text):
    """macOS 通知：launchd 静默跑，出错时得让人看见。"""
    try:
        import subprocess
        subprocess.run(["osascript", "-e",
                        f'display notification "{text}" with title "{title}"'],
                       timeout=10, capture_output=True)
    except Exception:
        pass


def download(s, url, path, retries=3):
    """下载媒体，瞬断自动重试（实测 sinaimg/videocdn 偶发 reset）。"""
    for attempt in range(1, retries + 1):
        try:
            r = s.get(url, headers={"Referer": "https://weibo.com/"}, timeout=30)
            r.raise_for_status()
            path.write_bytes(r.content)
            return True
        except Exception as e:
            if attempt < retries:
                time.sleep(attempt * 3)
                continue
            log(f"  media 下载失败(重试{retries}次) {url} -> {e}")
            return False


def save_records(records, uid, want_media, s, want_video=True):
    if not records:
        return
    day = datetime.date.today().isoformat()
    outdir = DATA / day / uid
    outdir.mkdir(parents=True, exist_ok=True)
    for rec in records:
        pdir = outdir / rec["mblogid"]
        pdir.mkdir(exist_ok=True)
        # 所有下载图源(含转发原文)
        pics = list(rec["pic_urls"]) + (rec.get("repost_of", {}).get("pic_urls", []) if rec.get("repost_of") else [])
        vids = [v for v in [rec["video_url"], (rec.get("repost_of") or {}).get("video_url")] if v]
        failed = []
        if want_media:
            for i, u in enumerate(pics, 1):
                ext = (u.split("?")[0].rsplit(".", 1)[-1] or "jpg")[:4]
                if not download(s, u, pdir / f"img_{i:02d}.{ext}"):
                    failed.append(u)
            if want_video:
                for i, u in enumerate(vids, 1):
                    if not download(s, u, pdir / f"video_{i:02d}.mp4"):
                        failed.append(u)
        if failed:
            rec["media_failed"] = failed  # 缺失台账：post.json 里可见，事后可补抓
        (pdir / "post.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    # 汇总一份当天该号的索引（按 mblogid 去重，--fresh 重跑不产生重复条目）
    idx = DATA / day / uid / "_index.json"
    existing = json.loads(idx.read_text(encoding="utf-8")) if idx.exists() else []
    have = {e.get("mblogid") for e in existing}
    existing.extend([{k: rec[k] for k in ("mblogid", "created_iso", "text", "is_repost", "url")}
                     for rec in records if rec["mblogid"] not in have])
    idx.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="回溯到该日期 YYYY-MM-DD（翻页直到更早）")
    ap.add_argument("--uid", help="只抓某个 uid")
    ap.add_argument("--id", dest="mblogid", help="抓指定单篇 mblogid（无视去重）")
    ap.add_argument("--pages", type=int, default=1, help="每号抓前 N 页（默认1）")
    ap.add_argument("--no-media", action="store_true", help="不下载图片视频")
    ap.add_argument("--no-video", action="store_true", help="只下图不下视频（视频大，做日报先只要图）")
    ap.add_argument("--fresh", action="store_true", help="忽略去重、不更新 seen（用于按日期范围全量拉取做日报）")
    args = ap.parse_args()

    want_media = not args.no_media
    since = datetime.datetime.strptime(args.since, "%Y-%m-%d").date() if args.since else None
    targets = {args.uid: UIDS.get(args.uid, "?")} if args.uid else UIDS

    try:
        s = build_session()
    except Exception as e:
        log(f"FATAL cookie: {e}")
        notify("サクラ新聞 采集失败", "微博 cookie 失效，去 Chrome 重新登录 weibo.com")
        sys.exit(2)

    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    seen = json.loads(SEEN_FILE.read_text(encoding="utf-8")) if SEEN_FILE.exists() else {}

    total_new = 0
    for uid, note in targets.items():
        seen_ids = set(seen.get(uid, []))
        new_records = []
        # 指定单篇模式：直接用 show 接口，任意单篇都能抓（不限第1页）
        if args.mblogid:
            r = s.get("https://weibo.com/ajax/statuses/show",
                      params={"id": args.mblogid}, timeout=20)
            mb = r.json()
            if isinstance(mb, dict) and mb.get("id"):
                new_records.append(extract(s, mb))
            log(f"[{uid}] 指定单篇 {args.mblogid}: 命中 {len(new_records)} 条")
        else:
            stop = False
            for page in range(1, args.pages + 1 if since is None else 20):
                d = fetch_page(s, uid, page)
                if d.get("ok") != 1:
                    log(f"[{uid}] API ok!={d.get('ok')}（cookie 可能失效，请重登 weibo.com）")
                    notify("サクラ新聞 采集异常", f"weibo API ok!={d.get('ok')}，cookie 可能失效")
                    break
                lst = d.get("data", {}).get("list") or []
                if not lst:
                    break
                if page == 1:
                    sn = (lst[0].get("user") or {}).get("screen_name")
                    log(f"[{uid}] 账号名: {sn}")
                for mb in lst:
                    mid = str(mb.get("id"))
                    t = parse_time(mb.get("created_at"))
                    if since and t and t.date() < since:
                        if mb.get("isTop"):
                            continue   # 置顶老帖：跳过但继续往后看新帖
                        stop = True; break
                    if mid in seen_ids and not args.fresh:
                        continue
                    new_records.append(extract(s, mb))
                    seen_ids.add(mid)
                if stop or since is None:  # 无 --since 时只看第1页(或 --pages 指定)
                    if since is None and page >= args.pages:
                        break
                if stop:
                    break
                time.sleep(1.2)
            log(f"[{uid}] {note}: 新博文 {len(new_records)} 条")

        save_records(new_records, uid, want_media, s, want_video=not args.no_video)
        if not args.mblogid and not args.fresh:
            seen[uid] = list(seen_ids)[-500:]  # 只保留最近500个id
        total_new += len(new_records)

    if not args.mblogid and not args.fresh:
        SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"完成: 本次共 {total_new} 条新博文 -> {DATA}")


if __name__ == "__main__":
    main()
