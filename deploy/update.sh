#!/usr/bin/env bash
# MindSpider 更新脚本
# 用法: bash deploy/update.sh
#
# 流程: git pull → 重装依赖 → 重启 service

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="mindspider-scheduler"
CONDA_ENV_NAME="mindspider"

echo "============================================================"
echo "MindSpider 更新"
echo "============================================================"

# ---------- 1. Git pull ----------
echo ""
echo "[1/3] 拉取最新代码..."
cd "${PROJECT_DIR}"
git pull --ff-only
echo "代码更新完成"

# ---------- 2. 重装依赖 ----------
echo ""
echo "[2/3] 更新依赖..."

# 检测 conda
CONDA_BIN=""
if command -v conda &>/dev/null; then
    CONDA_BIN="conda"
elif [ -f "$HOME/miniconda3/bin/conda" ]; then
    CONDA_BIN="$HOME/miniconda3/bin/conda"
elif [ -f "$HOME/anaconda3/bin/conda" ]; then
    CONDA_BIN="$HOME/anaconda3/bin/conda"
fi

if [ -z "$CONDA_BIN" ]; then
    echo "错误: 未找到 conda" >&2
    exit 1
fi

CONDA_PREFIX=$($CONDA_BIN env list | grep "^${CONDA_ENV_NAME} " | awk '{print $NF}')
PIP_BIN="${CONDA_PREFIX}/bin/pip"

"${PIP_BIN}" install -e . 2>&1 | tail -5
echo "依赖更新完成"

# ---------- 3. 重启服务 ----------
echo ""
echo "[3/3] 重启服务..."
sudo systemctl restart ${SERVICE_NAME}

# 等待几秒确认启动成功
sleep 3
if sudo systemctl is-active --quiet ${SERVICE_NAME}; then
    echo "服务重启成功"
    sudo systemctl status ${SERVICE_NAME} --no-pager -l
else
    echo "警告: 服务启动失败，请检查日志:" >&2
    echo "  journalctl -u ${SERVICE_NAME} -n 50 --no-pager" >&2
    exit 1
fi

echo ""
echo "更新完成"
