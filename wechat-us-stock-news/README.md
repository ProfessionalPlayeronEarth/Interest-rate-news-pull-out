# 美股利率快讯 · 微信自动推送

每天自动抓取美股相关新闻，**重点筛选与「利率波动」有关的快讯**（美联储、FOMC、国债收益率、降息/加息、通胀等），整理后推送到你的**微信**。并额外**盯住社媒舆情**，捕捉中东战争等「利率敏感地缘事件」中**早于主流媒体报道**的个人发帖，第一时间预警。

> 已实测：新闻/社媒抓取、关键词过滤、去重、早期信号预警、三种微信推送渠道（Server酱 / PushPlus / 企业微信）均跑通。

---

## ✨ 功能

- 📰 **多源聚合**：CNBC、MarketWatch、WSJ、美联储官网等 RSS 源，可自由增删
- 🎯 **利率精准过滤**：中英文关键词命中即推送；强相关内容（降息/加息/美联储等）标 ⭐ 置顶
- ⚡ **社媒舆情监测**：盯 Reddit / Mastodon / Telegram 上关于**中东战争等「利率敏感地缘事件」**的个人发帖
- 🚨 **早于主流的预警**：自动和主流 RSS 做交叉比对，找出「主流尚未覆盖」的新鲜信号，立即额外发一条微信预警（不限于定时）
- 🕐 **每天定时推送**：默认北京时间 07:30 / 12:30 / 21:30，可改
- 🚫 **自动去重**：同一新闻/发帖不会重复推
- 📱 **三种微信渠道**：Server酱 / PushPlus / 企业微信 机器人，任选其一
- 🔧 **纯配置驱动**：改 `config.yaml` 即可，基本不用动代码

> **关于 X / TikTok**：两者已无可用的免费 API（X 官方 API 最低 $100+/月，TikTok 无公开抓取接口且反爬严格），本项目的社媒模块用 **Reddit（免费、`new` 流=最早信号）、Mastodon（免费公开 API、可按话题订阅）、Telegram（经 RSSHub 桥接）**——这些才是当前 OSINT 早期情报的真正富矿。若你有 X 付费 API 密钥，可在此基础上扩展 `src/social.py`。

---

## 🚀 5 分钟上手

### 第 1 步：拿到微信推送的「钥匙」
任选一种（都不用付钱）：

| 渠道 | 怎么拿 | config 里填什么 |
|---|---|---|
| **Server酱**（最简单） | 微信搜「方糖」公众号或打开 [sct.ftqq.com](https://sct.ftqq.com) 登录，复制 SENDKEY | `push.serverchan.sendkey` |
| **PushPlus** | 微信关注「PushPlus 推送加」公众号，打开 [pushplus.plus](https://www.pushplus.plus) 复制 token | `push.pushplus.token` |
| **企业微信** | 在企业微信群里「添加群机器人」拿到 webhook 地址 | `push.wecom.webhook` |

### 第 2 步：跑起来
```bash
cd wechat-us-stock-news
./run.sh            # 会自建虚拟环境并安装依赖，然后跑一次
```
> 没装 bash 的话：`python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m src.main --once`

把 `config.yaml` 里的 `push.channel` 改成你选的渠道，并填好对应的 `sendkey` / `token` / `webhook`，再跑一次就能在微信收到。

### 第 3 步：先验证渠道通不通
```bash
./run.sh test       # 发一条测试消息到你微信
```

### 第 4 步：让它「每天自动推」
推荐用 **GitHub Actions**（免费、不用开服务器，详见下方「部署」）。
想本地跑就配个 `cron`：
```bash
# 每天北京时间 07:30 / 12:30 / 21:30 各推一次
0 23,4,13 * * *  TZ=Asia/Shanghai /path/to/wechat-us-stock-news/.venv/bin/python -m src.main --once >> /path/to/log.txt 2>&1
```

### 第 5 步：看看抓了啥（不推送）
```bash
./run.sh dry-run    # 打印本次会推的内容 + 命中了哪些关键词，方便调参
```

---

## ⚙️ 配置说明（`config.yaml`）

| 配置块 | 作用 |
|---|---|
| `push` | 推送渠道与密钥（`channel` 设 `serverchan`/`pushplus`/`wecom`/`console`） |
| `sources` | RSS 新闻源列表，加 `#` 注释即可停用某个源 |
| `rate_keywords` | 利率相关关键词，命中即推送（中英文都写） |
| `strong_keywords` | 强相关关键词，命中标 ⭐ 并置顶 |
| `fetch` | `lookback_hours` 只推最近 N 小时的新闻；`max_items_per_source` 每源读取上限 |
| `schedule` | 推送时间（北京时间）、是否启动即跑一次 |
| `dedup` | 去重开关与记录文件路径 |

**想更精准 / 更宽松？** 直接增删 `rate_keywords` 即可。比如只想看美联储和收益率，就只留 `federal reserve`、`fomc`、`treasury`、`yield`、`美联储`、`收益率`。

---

## 📦 部署方式

### 方式 A：GitHub Actions（推荐，免费、零服务器）
1. 把本项目推到 GitHub 仓库
2. 仓库 `Settings → Secrets and variables → Actions` 里加密钥（至少填一个）：
   - `PUSH_CHANNEL` = `serverchan`（或 `pushplus` / `wecom`）
   - `SERVERCHAN_SENDKEY` / `PUSHPLUS_TOKEN` / `WECOM_WEBHOOK`
3. 工作流已写在 `.github/workflows/daily.yml`，每天自动跑 3 次
4. 也可在 Actions 页面点 **Run workflow** 手动触发测试

> 注意：GitHub 免费版在仓库长期无活动后可能延迟触发，介意就用方式 B。

### 方式 B：自己的服务器 / 树莓派（cron）
按上方第 4 步配 `cron` 即可，24 小时在线最稳。

> **想更快捕捉战争信号？** 把 `config.yaml` 里 `schedule.times` 加更多时间点（如每小时一次），或在 `social` 块把 `early_window_hours` 调小，早期预警会更灵敏。社媒源越多、检查越频繁，越接近「实时」。

---

## ⚡ 社媒监测怎么工作

1. 按计划抓取 Reddit（`new` 流）、Mastodon（话题时间线）、Telegram（经 RSSHub 的 RSS）的最新发帖
2. 用 `war_keywords`（中东/战争/油价等）过滤，只留相关发帖
3. **交叉比对**：同时抓取主流 RSS 近期标题，若某条社媒发帖提到的战争维度**主流还没报**，且发帖很新（< `early_window_hours`），就标记为 `⚡ 早期信号`
4. 早期信号 → **立即额外发一条微信预警**（不限于定时）；其余社媒信号进每日摘要的「⚡ 中东局势」段
5. 全部去重，不重复打扰

**想盯别的平台？** 在 `config.yaml` 的 `social.sources` 里加条目即可：
- `type: reddit` + `subreddit: 板块名`
- `type: mastodon` + `instance: 实例域名` + `tag: 话题`
- `type: rss` + `url: 任意RSS`（Telegram 频道用 `https://rsshub.app/telegram/channel/频道名`，也可自建 RSSHub 换域名）

---

## 🧩 命令行

```bash
python -m src.main --once       # 立即跑一次（正式推送）
python -m src.main --dry-run    # 只打印、不推送、不记去重（调参用）
python -m src.main --test       # 发一条测试消息验证渠道
python -m src.main --schedule   # 按 config 的 times 定时循环运行
```

---

## ❓ 常见问题

- **收不到消息？** 先 `./run.sh test` 验证渠道；确认 `push.channel` 与密钥一致；检查 GitHub Actions 的 Secrets 是否填对。
- **推送太多/太少？** 调 `rate_keywords` / `war_keywords` 和 `fetch.lookback_hours`（调小时间窗会更少）。
- **某新闻源一直失败？** 在 `sources`（主流）或 `social.sources`（社媒）里给那行加 `#` 注释掉，或换更稳的源。
- **社媒误报/漏报？** 调 `social.war_keywords` 和 `early_window_hours`；Reddit 偶尔限流返回 429 属正常，下次运行会补。
- **想加更多源？** 任意公开 RSS 地址都能加进 `sources`；社媒见上方「社媒监测怎么工作」。

---

## 📁 项目结构

```
wechat-us-stock-news/
├── config.yaml            # 你的配置（改这里：推送/新闻源/利率词/社媒/战争词）
├── requirements.txt
├── run.sh                 # 一键启动脚本
├── src/
│   ├── config.py          # 配置加载
│   ├── news.py            # 主流 RSS 抓取 + 利率关键词过滤 + 排序
│   ├── social.py          # 社媒监测（Reddit/Mastodon/Telegram）+ 早于主流判断
│   ├── pusher.py          # 微信推送（Server酱/PushPlus/企微/控制台）
│   ├── store.py           # 去重存储
│   └── main.py            # 主程序 + 命令行 + 定时调度 + 早期预警
├── data/seen.json         # 去重记录（自动生成）
└── .github/workflows/     # GitHub Actions 自动推送配置
```
