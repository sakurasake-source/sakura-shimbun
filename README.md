# サクラ新聞 🌸

宮脇咲良近况自动搬运流水线 —— 从微博 @樱绽 采集 → 理解总结 → 生成符合 sakusaku 风格的小红书「发帖包」。
**最终发布由本人手动完成，工具只产出草案，不自动发帖。**

## 环境

- Python: `.venv/`（python 3.13）
- agent-reach: 独立 venv `~/.agent-reach-venv/`，已自动注册为 Claude Code skill

## 自有脚本（scripts/）——流水线主力

| 脚本 | 作用 |
|---|---|
| `daily_fetch.py` | ①采集：读 Chrome 微博 cookie → ajax API 抓 2 个号 → 去重落盘（媒体失败自动重试3次，仍失败记入 post.json 的 `media_failed`；出错弹 macOS 通知） |
| `render_report.py` | 日报渲染：issueNN.html 的每个 .page → 1080×1440 PNG（`--html report/issueNN/issueNN.html`） |
| `xhs_publish.py` | Phase 3 上稿：Playwright 传图/视频+填文案+设「仅自己可见」+发布（发布前校验 标题≤20字/tag≤10/正文≤1000/图≤18；goto 自动重试3次） |

## 已装工具（tools/，均为辅助/备用）

| 工具 | 位置 | 状态 |
|---|---|---|
| **weiboSpider** | `tools/weiboSpider/` | ⚠️ 已弃用——采集由 `daily_fetch.py`（直连 ajax API）实现，此库从未投产 |
| **XHS-Downloader** | `tools/XHS-Downloader/` | 风格分析原料采集用过一轮，现闲置备用 |
| **agent-reach** | `~/.agent-reach-venv/` | 读 X/INS/网页做总结·翻译（只读，无发帖），Claude 直接调用 |

## agent-reach 渠道状态

- ✅ 现成可用：任意网页（Jina Reader）、RSS/Atom
- 🔒 待解锁（需登录/cookie，后续按需配）：Twitter/X、Instagram、小红书、B站等
- ⚪ 可选未装：gh CLI（本机无 brew）、Exa 全网语义搜索（mcporter + npm）

## 流水线设计

```
①采集      weiboSpider 抓 @樱绽 最新 N 条
②去重筛选   对比上次记录，只留新的 + 值得发的
③理解总结   agent-reach / Claude 读图文 → 这是什么新闻(可翻译日韩源)
④媒体处理   下载图/视频，按需裁封面
⑤文案生成   挂《风格手册》→ Claude 生成 标题×3 + 正文 + tag + cr
⑥出发帖包   images/ + draft.md，扔进日期文件夹
──────────  以下手动 ──────────
⑦本人审核 → 小红书 App 发布
```

## 暂缓的工具（有意不装）

- **browser-use / xiaohongshu-cli**：都指向「自动发帖」，封号风险高，与「本人手动发布」原则冲突，暂挂。

## ①采集 · 每日微博自动抓取（Phase 1/2 已上线）

- **脚本**：`scripts/daily_fetch.py`（读 Chrome 登录 cookie → 抓 2 个号 → 去重 → 落盘）
- **目标号**：`5664006997`(搬运号) + `6591486070`(超话应援号)
- **cookie**：`browser_cookie3` 自动读 Chrome 的 weibo.com 登录态（**保持 Chrome 登录即可，无需手动维护**）
- **定时**：launchd `com.sakusaku.sakuranews.weibo`，每天 **20:00(JST)** 自动跑
- **落盘**：`data/weibo/YYYY-MM-DD/{uid}/{mblogid}/` = `post.json` + 图片/视频；日志 `data/weibo_state/`

常用命令：
```bash
VENV=.venv/bin/python
$VENV scripts/daily_fetch.py                 # 手动跑一次(默认: 只留上次之后的新博文)
$VENV scripts/daily_fetch.py --since 2026-07-01   # 回溯到某日
$VENV scripts/daily_fetch.py --uid 6591486070 --id <mblogid>  # 抓指定单篇(试 Phase3/特殊指示)
$VENV scripts/daily_fetch.py --no-media       # 只存文本不下媒体
launchctl kickstart -k gui/$(id -u)/com.sakusaku.sakuranews.weibo  # 立刻触发定时任务
launchctl unload ~/Library/LaunchAgents/com.sakusaku.sakuranews.weibo.plist  # 停用每日任务
```
⚠️ cookie 失效表现：日志出现 `API ok!=1（cookie 可能失效）`→ 去 Chrome 重新登录 weibo.com 即可。
⚠️ 首次由 launchd 触发时，macOS 可能弹一次「python 想访问 Chrome Safe Storage」，点**始终允许**。

## 进度

- [x] 装好工具链（weiboSpider / XHS-Downloader / agent-reach）
- [x] 采集自己主页近 32 篇笔记 → `data/style_raw/sakusaku_notes.json`
      （方式：Chrome 登录态 + 页内 JS 读 noteDetailMap，逐篇带 token 跳转，token 不外泄）
- [x] 全站笔记数点清：**924 篇**（"369"是专辑选择器数字，非发帖数）
- [x] 按点赞排 Top50 完整正文 → `sakusaku_top50_by_likes.json`
- [x] 点赞 51-200 语料（51-100 全文 + 101-200 索引）→ `sakusaku_daily_rank51_200.json`
- [x] 242 条作者评论回复 → `sakusaku_author_replies.json`
- [x] 建成《风格手册》v4（爆款 §8.5 + 日常 §8.6 + 评论 §8.7）→ `风格手册.md`

- [x] ①采集自动化上线：`daily_fetch.py` + launchd 每日 20:00 抓 2 个微博号（Phase 1/2）
- [x] **Phase 3 闭环**：`scripts/xhs_publish.py`（Playwright）自动传图+填文案+设「仅自己可见」+发布

## ②③ 发帖包生成 + 上稿（Phase 3 已闭环）

```
抓取的 post.json+图  →  按《风格手册》(⛔红线+§8.8去AI味) 生成
  publish_payload.json = {title, body, tags(≤10), images[]}
  →  xhs_publish.py --mode private
       · browser_cookie3 读 Chrome 小红书登录态
       · setInputFiles 从工作文件夹直传图（不用手选）
       · 填标题/正文，tag「#词+空格」逐个触发话题
       · 可见范围设「仅自己可见」+ 读文本硬验证（没确认绝不发）
       · 点发布 → 发成【仅你可见】的私密贴
  →  你在小红书 App/主页 审核、调整 → 满意后自己把权限改「公开」
```
命令：`.venv/bin/python -u scripts/xhs_publish.py --dir "<发帖包目录>" --mode private`
（`--mode stop` = 只填不发、停发布前；`--mode probe_vis` = 探测可见范围控件）
⚠️ 我全程只发「仅自己可见」，**公开这一步永远你自己点**。

## 日报（サクラ新聞）目录规范

```
report/issueNN/          # 一期一目录，互不覆盖
  issueNN.html           # 版面源文件（.page = 376×501，图片相对路径 ../../data/... 和 ../../assets/...）
  pages/page_N.png       # render_report.py 产物（1080×1440，git 忽略、可重生成）
  publish_payload.json   # {title, body, tags(≤10), images[]}
```
渲染：`.venv/bin/python scripts/render_report.py --html report/issueNN/issueNN.html`

## 版本管理

本目录是本地 git 仓库（无远端）。`data/`、`tools/`、`.venv/`、渲染产物均被 .gitignore。
改完脚本/手册/模板记得 `git commit`。

## 下一步

- [x] 偶发页面加载慢导致失败 → goto 自动重试3次（2026-07-12）
- [x] 渲染脚本落盘 `render_report.py`（2026-07-12）
- [ ] 把"生成发帖包"也脚本化，串成：采集 → 生成 payload → xhs_publish 一条龙
- [ ] （可选）cr 图片水印位确认
- [ ] data/weibo 媒体保留策略（目前 850MB/3天，无限增长）
