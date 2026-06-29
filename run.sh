#!/usr/bin/env bash
# 便捷入口：./run.sh <命令>
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}

case "${1:-help}" in
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
  crawl     接 MediaCrawler 采集抖音+小红书 -> data/leads.csv（需扫码登录你的账号）
            例: ./run.sh crawl --platform xhs --max 15
  demo      用样例数据跑通打分（生成 data/queue.csv）
  score     去重+打分+排序
  inspect   采集质量体检：量化看“搜索到不到位”（含 top 线索预览）
  add       手动录入线索
  publish   命令行人工辅助发布
  web       打开本地 Web 控制台
  report    复盘统计
  test      跑全部自测（核心 + 采集链路 + runner 管道）
EOF
  ;;
esac
