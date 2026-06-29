#!/usr/bin/env bash
# setup_mediacrawler.sh —— 一键装出「能跑」的采集层
#
# 做的事（幂等，可重复跑）：
#   1) clone MediaCrawler 到 vendor/MediaCrawler（已存在则跳过）
#   2) 建独立 venv + 装依赖（CDP 模式用系统 Chrome；playwright chromium 可选）
#   3) 应用本仓库 collectors/patches/ 里的补丁：
#        - CDP 自动拉起系统 Chrome（不再死等已存在的调试浏览器）
#        - 兼容新版 xhshow 签名（修 sign_state 冲突 / 'float'.encode 崩溃）
#   4) macOS：清理 .venv 的 com.apple.quarantine（否则 lxml 等 .so 被系统策略拦）
#   5) 自检 import
#
# 用法：  ./collectors/setup_mediacrawler.sh
# 可选：  MEDIACRAWLER_HOME=/path/to/MediaCrawler ./collectors/setup_mediacrawler.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MC_HOME="${MEDIACRAWLER_HOME:-$ROOT/vendor/MediaCrawler}"
PATCH_DIR="$SCRIPT_DIR/patches"
MC_REPO="https://github.com/NanmiCoder/MediaCrawler.git"

echo "▶ MediaCrawler 目录: $MC_HOME"

# 1) clone（已存在则跳过）
if [ ! -f "$MC_HOME/main.py" ]; then
  echo "▶ clone MediaCrawler ..."
  git clone --depth 1 "$MC_REPO" "$MC_HOME"
else
  echo "✓ 已存在 MediaCrawler，跳过 clone"
fi

# 2) venv + 依赖
PY_BIN="$MC_HOME/.venv/bin/python"
if [ ! -x "$PY_BIN" ]; then
  echo "▶ 创建独立 venv ..."
  python3 -m venv "$MC_HOME/.venv"
fi
echo "▶ 安装依赖（可能需要几分钟）..."
"$PY_BIN" -m pip install -q --upgrade pip
"$PY_BIN" -m pip install -q -r "$MC_HOME/requirements.txt"
# CDP 模式直接用系统 Chrome；chromium 仅在关掉 CDP 时才需要，装不上不影响主流程
"$PY_BIN" -m playwright install chromium \
  || echo "⚠ playwright chromium 未装（CDP 模式用系统 Chrome，不影响采集）"

# 3) 应用补丁（幂等：已应用则跳过；版本漂移则告警不中断）
apply_patch () {
  local patch="$1"
  local name; name="$(basename "$patch")"
  if git -C "$MC_HOME" apply --reverse --check "$patch" 2>/dev/null; then
    echo "✓ 已应用，跳过: $name"
  elif git -C "$MC_HOME" apply --check "$patch" 2>/dev/null; then
    git -C "$MC_HOME" apply "$patch"
    echo "✓ 应用成功: $name"
  else
    echo "⚠ 应用失败（MediaCrawler 版本可能已变，需人工核对）: $name"
  fi
}
echo "▶ 应用本仓库补丁 ..."
if [ -d "$PATCH_DIR" ]; then
  for p in "$PATCH_DIR"/*.patch; do
    [ -e "$p" ] && apply_patch "$p"
  done
fi

# 4) macOS：去隔离属性（lxml 等原生库的 'library load disallowed by system policy'）
if [ "$(uname)" = "Darwin" ]; then
  echo "▶ macOS：清理 .venv 隔离属性 ..."
  xattr -r -d com.apple.quarantine "$MC_HOME/.venv" 2>/dev/null || true
fi

# 5) 自检
echo "▶ 自检依赖 import ..."
"$PY_BIN" -c "import lxml.etree, playwright, httpx, parsel, execjs, xhshow; print('✓ 采集层依赖 OK')"

echo ""
echo "✅ 采集层就绪。下一步："
echo "   cd \"$ROOT\""
echo "   ./run.sh crawl --platform xhs --max 5"
echo "   （首次会自动拉起系统 Chrome，请用手机扫码登录——建议用小号；登录态会存下来，之后免扫码）"
