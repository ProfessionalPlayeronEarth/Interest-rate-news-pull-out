#!/usr/bin/env bash
# 快速启动脚本：激活虚拟环境并运行
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3.11 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
. .venv/bin/activate

MODE="${1:---once}"
case "$MODE" in
  once)      python -m src.main --once ;;
  dry-run)   python -m src.main --dry-run ;;
  test)      python -m src.main --test ;;
  schedule)  python -m src.main --schedule ;;
  *)         echo "用法: ./run.sh [once|dry-run|test|schedule]" ;;
esac
