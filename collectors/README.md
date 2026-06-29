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

## 安装 MediaCrawler（一键，一次性）

```bash
./collectors/setup_mediacrawler.sh
```

这个脚本（幂等，可重复跑）会：clone MediaCrawler 到 `vendor/`（已 gitignore）→ 建独立 venv 装依赖
→ **应用本仓库 `collectors/patches/` 里的补丁** → macOS 下清掉 `.venv` 隔离属性 → 自检 import。
也可 `MEDIACRAWLER_HOME=/path/to/MediaCrawler ./collectors/setup_mediacrawler.sh` 指向别处。
runner 会自动优先用 `vendor/MediaCrawler/.venv` 里的 Python。

### 为什么需要补丁（`collectors/patches/`）

`vendor/` 不入库，但让采集真正跑通的修复必须可复现，因此以补丁形式纳入：

| 补丁 | 修什么 | 不打的后果 |
|------|--------|------------|
| `0001-cdp-autolaunch.patch` | `CDP_CONNECT_EXISTING=False`，让程序**自己拉起**系统 Chrome | 默认配置会去连"已存在的调试浏览器"，没有则卡死超时 |
| `0002-xhs-sign-xhshow-compat.patch` | 兼容新版 `xhshow` 签名（跳过过时的 a3 补丁；GET 改用 `sign_headers_get`） | 搜索/抓评论崩 `sign_state` 冲突或 `'float'.encode` |

> macOS 额外坑：pip 装的原生库（如 `lxml`）带 `com.apple.quarantine`，会被系统策略拦
> （`library load disallowed by system policy`）。脚本会自动 `xattr -r -d` 清掉。

> ⚠️ MediaCrawler 上游每隔约 1–3 个月会因平台风控变更而局部失效；届时 `git -C vendor/MediaCrawler pull`
> 升级后，补丁可能需要重新核对（脚本对应用失败会告警而非中断）。

## 关于浏览器模式（CDP）

打完补丁后 `ENABLE_CDP_MODE=True` + `CDP_CONNECT_EXISTING=False`：程序**自己拉起系统 Chrome**
（独立 profile `vendor/MediaCrawler/browser_data/`，不污染你日常浏览器），用真实浏览器算签名、反检测更稳。
`CDP_HEADLESS=False` 让你能看到并扫二维码；登录态会存进该 profile，**之后免扫码**。

## 手动起步（不想马上装采集器）

```bash
python3 add_lead.py        # 交互式录入；把热评里的「多少钱/怎么寄」也摘进正文，打分更准
```

## 合规边界（重要）

- 只采集**公开**数据、控制频率、遵守各平台 ToS；用于发现 + 人工跟进。
- 采集是为了发现，不是自动群发。
- 本项目不提供、也不会加自动发布 / 多账号群控 / 绕风控的能力。
