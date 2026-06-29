# 采集层安装（抖音 + 小红书）

采集用 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)（同时支持小红书 xhs / 抖音 dy）。
采集与归一化的逻辑都在根目录的 `jieliu.py` 里；本目录只放**一次性安装**所需的东西。

## 一键安装

```bash
python3 jieliu.py setup        # 等价于直接跑 ./collectors/setup_mediacrawler.sh
```

`setup_mediacrawler.sh`（幂等，可重复跑）会：clone MediaCrawler 到 `../vendor/`（已 gitignore）
→ 建独立 venv 装依赖 → **应用 `patches/` 里的补丁** → macOS 下清掉 `.venv` 隔离属性 → 自检 import。
也可 `MEDIACRAWLER_HOME=/path/to/MediaCrawler ./collectors/setup_mediacrawler.sh` 指向别处。

## 为什么需要补丁（`patches/`）

`vendor/` 不入库，但让采集真正跑通的修复必须可复现，因此以补丁形式纳入：

| 补丁 | 修什么 | 不打的后果 |
|------|--------|------------|
| `0001-cdp-autolaunch.patch` | `CDP_CONNECT_EXISTING=False`，让程序**自己拉起**系统 Chrome | 默认会去连"已存在的调试浏览器"，没有则卡死超时 |
| `0002-xhs-sign-xhshow-compat.patch` | 兼容新版 `xhshow` 签名（跳过过时 a3 补丁；GET 改用 `sign_headers_get`）| 搜索/抓评论崩 `sign_state` 冲突或 `'float'.encode` |

> macOS 额外坑：pip 装的原生库（如 `lxml`）带 `com.apple.quarantine`，会被系统策略拦
> （`library load disallowed by system policy`）。脚本会自动 `xattr -r -d` 清掉。

> ⚠️ MediaCrawler 上游每隔约 1–3 个月会因平台风控变更而局部失效；届时
> `git -C vendor/MediaCrawler pull` 升级后，补丁可能需重新核对（脚本对应用失败会告警而非中断）。

## 采集怎么跑

装好后回根目录跑 `python3 jieliu.py`（详见根 `README.md`）。首次会自动拉起系统 Chrome
让你扫码登录——**请用专门的小号**。登录态存在 `vendor/MediaCrawler/browser_data/`，之后免扫码。
