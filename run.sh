#!/usr/bin/env bash
# 便捷入口：./run.sh <命令>
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}

case "${1:-help}" in
  demo)     cp -f data/leads.sample.csv data/leads.csv; "$PY" score.py ;;
  score)    "$PY" score.py ;;
  add)      "$PY" add_lead.py ;;
  publish)  "$PY" publish_assist.py ;;
  web)      "$PY" serve.py ;;
  report)   "$PY" report.py ;;
  test)     "$PY" selftest.py ;;
  *)
    cat <<'EOF'
用法: ./run.sh <命令>
  demo      用样例数据跑通打分（生成 data/queue.csv）
  score     去重+打分+排序
  add       手动录入线索
  publish   命令行人工辅助发布
  web       打开本地 Web 控制台
  report    复盘统计
  test      跑自测
EOF
  ;;
esac
