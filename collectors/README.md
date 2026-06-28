# 采集层（抖音 + 小红书）

本目录负责「把公开内容采集进来」这一步，输出统一的 `../data/leads.csv`。

## 推荐方案：MediaCrawler

[MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) 是开源里最成熟的多平台采集器，
**同时支持抖音和小红书**，基于 Playwright 保留登录态、用 JS 取签名，不用自己逆向加密。

大致流程（具体命令以它的 README 为准，不同版本会变）：

1. 单独 clone 并安装 MediaCrawler（独立于本项目）。
2. 用本项目的 `../keywords.txt` 作为搜索词，按关键词搜索抓「笔记/视频 + 评论 + 点赞数」。
3. 把结果导出成 CSV 或 JSON。
4. 跑适配器归一化到 `data/leads.csv`：

   ```bash
   python3 collectors/mediacrawler_adapter.py <导出文件> --platform xiaohongshu
   python3 collectors/mediacrawler_adapter.py <导出文件> --platform douyin
   ```

5. 回到项目根目录跑 `python3 score.py`。

> 适配器只做字段映射。MediaCrawler 各版本导出字段名不一样，
> 改 `mediacrawler_adapter.py` 顶部的 `FIELD_MAP` 即可对上。

## 也可以先手动起步

不想马上接采集器，可以人工搜关键词、把命中的帖子按 `../data/leads.sample.csv`
的字段填进 `../data/leads.csv`，照样能跑通后面的去重 / 打分 / 排序 / 辅助发布。
**高价值意图（“怎么寄/多少钱/清关麻烦吗”）经常在评论区**，填 `content_excerpt`
时把热评也摘进去，打分会准很多。

## 合规边界（重要）

- 只采集**公开**数据，控制频率，别碰个人隐私信息，遵守各平台 ToS。
- 采集是为了**发现 + 人工跟进**，不是自动群发。
- 本项目不提供、也不会加自动发布 / 多账号群控 / 绕风控的能力。
