# 采集层（抖音 + 小红书）—— 已接通 MediaCrawler

本目录负责「把公开内容采集进来」，输出统一的 `../data/leads.csv`。
采集器用 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)（**同时支持抖音 xhs/dy**），
字段映射、评论合并、一键 runner 都已对齐它的真实输出并测过。

## 一键采集

```bash
./run.sh crawl                      # 抖音+小红书都采
./run.sh crawl --platform xhs --max 15   # 只采小红书，最多 15 条
```

`run_mediacrawler.py` 会：用 `../keywords.txt` 当搜索词跑 MediaCrawler（json 输出 + 抓评论），
找到最新的 contents/comments，调 `mediacrawler_adapter.py` 归一化进 `../data/leads.csv`，
并把**热评摘进正文**（高价值意图「多少钱/怎么寄/清关麻烦吗」常只在评论里）。

> **唯一手动一步**：MediaCrawler 起来后，用你自己的手机**扫码登录你自己的账号**
> （`LOGIN_TYPE=qrcode`）。本项目不碰你的账号密码、不自动登录、不发布。

## 安装 MediaCrawler（一次性）

```bash
# 1) clone 到 vendor/（已 gitignore）。也可放别处并设 MEDIACRAWLER_HOME 指向它
git clone --depth 1 https://github.com/NanmiCoder/MediaCrawler.git vendor/MediaCrawler

# 2) 装依赖（用独立 venv，别污染系统）
cd vendor/MediaCrawler
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
cd ../..
```

runner 会自动优先用 `vendor/MediaCrawler/.venv` 里的 Python。

## 关于浏览器模式（CDP）

MediaCrawler 默认 `ENABLE_CDP_MODE=True`（用真实 Chrome + 远程调试端口 9222，反检测更稳）。
首次跑它会自动拉起一个 Chrome 并在端口 9222 调试；**保持非无头**（默认 `CDP_HEADLESS=False`）
你才能看到并扫二维码。无需自己手动开 Chrome——它会自己拉起。

> 已在本机实测：MediaCrawler 能正常启动、拉起浏览器并走到登录阶段；
> 之后就是你扫码这一步。

## 手动起步（不想马上装采集器）

```bash
python3 add_lead.py        # 交互式录入；把热评里的「多少钱/怎么寄」也摘进正文，打分更准
```

## 合规边界（重要）

- 只采集**公开**数据、控制频率、遵守各平台 ToS；用于发现 + 人工跟进。
- 采集是为了发现，不是自动群发。
- 本项目不提供、也不会加自动发布 / 多账号群控 / 绕风控的能力。
