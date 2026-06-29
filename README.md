# 自动化截流 · MVP（抖音 + 小红书）

把「找链接 → 去重打分排序 → 待处理 → 人工辅助发布 → 跟进回填 → 复盘漏斗」跑成一个最小闭环。
业务层纯 Python 标准库（线索库用自带 `sqlite3`），**无需 pip 安装额外依赖**、无后台。
（仅采集器 MediaCrawler 需要一次性装依赖，见 `collectors/`。）

```
关键词 → 采集(MediaCrawler/手动) → 去重·打分·排序 → 待处理队列
       → 人工辅助发布 → 跟进回填(可见性/回复/加私域/报价/成交) → 转化漏斗复盘
```

## 快速开始

```bash
./collectors/setup_mediacrawler.sh   # 一次性：装好「能跑」的采集器（clone+依赖+补丁+自检）
./run.sh crawl --platform xhs --max 5 # 采集（扫码登录请用专门的小号，别用触达号）
./run.sh score                        # 去重 + 打分 + 排序 -> data/jieliu.db（+ queue.csv 快照）
./run.sh inspect                      # 采集质量体检：搜得准不准（召回/评论合并/买家意图/留存）
./run.sh web                          # 本地 Web 控制台（待处理 + 跟进回填，推荐）
./run.sh report                       # 转化漏斗复盘（发现→发出→可见→回复→私域→报价→成交额）

./run.sh demo                         # 用样例数据跑通打分（0 账号风险，先看输出长相）
./run.sh test                         # 全部自测（核心 + 采集链路 + runner 管道）
```

> 不用 `run.sh` 也行：`python3 score.py` / `python3 serve.py` / `python3 inspect_leads.py` 等。

## 数据怎么存（重要变化）

- **`data/jieliu.db`（sqlite）= 单一真相源**：每条线索一行，承载全生命周期。
  发出评论后**不删行**，可继续回填「对外是否可见 / 对方回复 / 加私域 / 报价 / 成交额」。
- `data/leads.csv`：采集层原始交接格式（adapter 产出）。
- `data/queue.csv`：由 db 导出的**只读快照**（给 `inspect` 与人工查看，向后兼容）。
- `data/collect_log.csv`：每次采集的结果与风控判定（正常/疑似风控/无结果）。
- `data/*` 均不入库（`.gitignore`）。

## 文件总览

| 文件 | 作用 |
|------|------|
| `config.json` | 关键词分级、意图词(强/弱)、排除词、卖家/科普负向词、海外属地、打分权重（**最常调**）|
| `keywords.txt` | 给采集端用的搜索词 |
| `comments.json` | 顶评模板 `templates` + 回复评论者的承接式 `reply_templates`（每条强制填 `{hook}`）|
| `score.py` | **核心**：去重 + 打分 + 排序 → 写入 db + 导出 queue 快照 |
| `store.py` | 数据层：sqlite 线索库（生命周期/回填/漏斗）+ CSV 读写 |
| `inspect_leads.py` | 采集质量体检（量化判断「搜得准不准」）|
| `serve.py` | Web 控制台：**待处理**(发布) + **跟进中**(可见性/漏斗回填) |
| `publish_assist.py` | 命令行版人工辅助发布 |
| `report.py` | 转化漏斗复盘（贯穿到成交额）|
| `add_lead.py` | 手动录入线索 |
| `collectors/setup_mediacrawler.sh` | 一键装好可跑的采集器（含补丁、macOS 去隔离）|
| `collectors/run_mediacrawler.py` | 一键采集：跑 MediaCrawler → 归一化（含风控三态判定）|
| `collectors/mediacrawler_adapter.py` | 字段映射 + 热评合并 + **抽取评论者线索** + 落盘脱敏 |
| `collectors/patches/` | 让 MediaCrawler 真正跑通的补丁（CDP 自启 / xhshow 签名兼容）|

## 排序逻辑（最值钱的部分）

`分数 = 关键词分级基分 + 意图加分(强>弱,评论×倍率) + 时效 + 评论活跃 + 海外属地`，
命中**排除词 / 科普·B2B·国内大件 / 作者是货代卖家** 直接丢弃。

- **关键词分级**：搬家回国 / 家具海运 / 大件托运 / 清关 / 双清包税 / 钢琴托运 …（high）
- **意图信号**（最高价值，常在评论区）：强=多少钱/报价/求渠道，弱=怎么发/多久到
- **评论者线索**：把评论区「问价/问怎么寄」的人抽成 `lead_type=commenter`，**可直接回复 TA**（转化更高、风险更低）
- **海外属地**：发帖/评论者在美/澳/加/英… → 加分（最廉价的「海运回国真买家」判别特征）
- **降权丢弃**：货代/集运卖家号、货代知识/FBA/国内大件 等噪声
- **去重**：内容/评论 ID + 链接；同一作者 14 天内不重复触达（`config.json` 可调）

## 每天怎么跑

```bash
./run.sh crawl --platform xhs --max 5   # 小号采集（评论太多可加 --max-comments 10 或 --get-comment false）
./run.sh score && ./run.sh inspect      # 打分 + 体检
./run.sh web                            # 待处理里发布；跟进中里回填可见性/回复/成交
./run.sh report                         # 看漏斗：可见率长期偏低 = 账号被影子限流，该换号/降频
```

发布流程：工具帮你**打开链接 + 复制草稿到剪贴板**，**你**粘贴、改成针对该帖/该评论的真实回复、自己点发送，
再标 `posted`；之后在「跟进中」回填**对外是否可见**与后续转化。

## 边界（刻意为之）

这套**只做**发现 / 去重 / 排序 / 人工辅助发布 / 转化度量。
**不做**自动点发送、多账号群控、绕风控——那是负 ROI 的封号跑步机，且违反平台 ToS。
最后那一下由人来点：成本最低的「反风控」，转化也更高。

## 合规提示

- 采集只取**公开**数据、控制频率、用专门小号、遵守各平台 ToS；评论原文落盘前已**脱敏**手机号/微信/QQ/邮箱。
- 评论务必每条不同、针对该帖/该评论，别一字不差刷模板。
- 不存储他人手机号/微信号等敏感联系方式；数据仅本地自用、不转卖。
