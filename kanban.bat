@echo off
REM 在项目文件夹里输入 kanban（或双击本文件）即可启动看板，浏览器会自动打开。
REM 国内网络：首次自动安装如果慢/失败，先执行  set JIELIU_CN=1  再运行本脚本；
REM 或先单独跑一次  python jieliu.py setup --cn （走清华源、跳过 chromium 下载）。
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动截流看板... 浏览器将自动打开 http://127.0.0.1:8787
echo （关闭看板：在本窗口按 Ctrl+C）
python jieliu.py serve %*
if errorlevel 1 (
  echo.
  echo 启动失败。请确认已安装 Python 并加入 PATH（命令行输入 python --version 能出版本）。
  echo 首次使用还需先装采集器：python jieliu.py setup
)
pause
