#!/usr/bin/env bash
# 便捷入口：./run.sh <命令>
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}

case "${1:-help}" in
  setup)    ./collectors/setup_mediacrawler.sh ;;
  demo)     cp -f data/leads.sample.csv data/leads.csv; "$PY" score.py ;;
  crawl)    "$PY" collectors/run_mediacrawler.py "${@:2}" ;;
  score)    "$PY" score.py ;;
  inspect)  "$PY" inspect_leads.py "${@:2}" ;;
  add)      "$PY" add_lead.py ;;
  publish)  "$PY" publish_assist.py ;;
  web)      "$PY" serve.py ;;
  report)   "$PY" report.py ;;
  test)     "$PY" selftest.py && "$PY" collectors/selftest_mc.py && "$PY" collectors/selftest_runner.py ;;
  *)
    cat <<'EOF'
用法: ./run.sh <命令>
  setup     一次性装好「能跑」的采集器（clone MediaCrawler + 依赖 + 补丁 + 自检）
  crawl     采集抖音+小红书 -> data/leads.csv（扫码请用专门小号）
            例: ./run.sh crawl --platform xhs --max 5 [--max-comments 10] [--get-comment false]
  demo      用样例数据跑通打分（0 账号风险）
  score     去重+打分+排序 -> data/jieliu.db（+ queue.csv 快照）
  inspect   采集质量体检：量化看“搜索到不到位”（含 top 线索预览）
  add       手动录入线索
  publish   命令行人工辅助发布
  web       本地 Web 控制台（待处理 + 跟进回填）
  report    转化漏斗复盘（发现→发出→可见→回复→私域→报价→成交额）
  test      跑全部自测（核心 + 采集链路 + runner 管道）
EOF
  ;;
esac
