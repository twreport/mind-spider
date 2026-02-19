#!/usr/bin/env bash
# MindSpider Ubuntu 服务器初始化脚本
# 用法: bash deploy/setup.sh
#
# 功能:
#   1. 安装 MongoDB 7.0
#   2. 安装系统依赖
#   3. 用 conda 创建 mindspider 环境 (Python 3.11)
#   4. pip install -e . 安装项目
#   5. 从 .env.example 生成 .env
#   6. 安装 systemd service

set -euo pipefail

# ---------- 配置 ----------
CONDA_ENV_NAME="mindspider"
PYTHON_VERSION="3.11"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="mindspider-scheduler"

echo "============================================================"
echo "MindSpider 服务器初始化"
echo "项目目录: ${PROJECT_DIR}"
echo "============================================================"

# ---------- 1. 系统依赖 ----------
echo ""
echo "[1/6] 安装系统依赖..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    build-essential \
    libxml2-dev \
    libxslt-dev \
    libffi-dev \
    libssl-dev \
    curl \
    gnupg \
    git

# ---------- 2. MongoDB 7.0 ----------
echo ""
echo "[2/6] 检查 MongoDB..."
if command -v mongod &>/dev/null; then
    echo "MongoDB 已安装: $(mongod --version | head -1)"
else
    echo "安装 MongoDB 7.0..."
    # 导入 GPG key
    curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | \
        sudo gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg

    # 添加源 (Ubuntu 22.04 jammy)
    UBUNTU_CODENAME=$(lsb_release -cs)
    echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu ${UBUNTU_CODENAME}/mongodb-org/7.0 multiverse" | \
        sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list

    sudo apt-get update -qq
    sudo apt-get install -y -qq mongodb-org

    # 启动并设为开机自启
    sudo systemctl start mongod
    sudo systemctl enable mongod
    echo "MongoDB 7.0 安装完成"
fi

# 确认 MongoDB 正在运行
if sudo systemctl is-active --quiet mongod; then
    echo "MongoDB 运行中"
else
    sudo systemctl start mongod
    echo "MongoDB 已启动"
fi

# ---------- 3. Conda 环境 ----------
echo ""
echo "[3/6] 配置 conda 环境..."

# 检测 conda
CONDA_BIN=""
if command -v conda &>/dev/null; then
    CONDA_BIN="conda"
elif [ -f "$HOME/miniconda3/bin/conda" ]; then
    CONDA_BIN="$HOME/miniconda3/bin/conda"
elif [ -f "$HOME/anaconda3/bin/conda" ]; then
    CONDA_BIN="$HOME/anaconda3/bin/conda"
else
    echo "未检测到 conda，安装 Miniconda..."
    curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o /tmp/miniconda.sh
    bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"
    rm /tmp/miniconda.sh
    CONDA_BIN="$HOME/miniconda3/bin/conda"
    echo "Miniconda 安装完成"
fi

echo "使用 conda: $($CONDA_BIN --version)"

# 创建环境（如果不存在）
if $CONDA_BIN env list | grep -q "^${CONDA_ENV_NAME} "; then
    echo "conda 环境 '${CONDA_ENV_NAME}' 已存在"
else
    echo "创建 conda 环境 '${CONDA_ENV_NAME}' (Python ${PYTHON_VERSION})..."
    $CONDA_BIN create -y -n "${CONDA_ENV_NAME}" python="${PYTHON_VERSION}"
fi

# 获取环境中 python 的绝对路径
CONDA_PREFIX=$($CONDA_BIN env list | grep "^${CONDA_ENV_NAME} " | awk '{print $NF}')
PYTHON_BIN="${CONDA_PREFIX}/bin/python"
PIP_BIN="${CONDA_PREFIX}/bin/pip"

echo "Python 路径: ${PYTHON_BIN}"

# ---------- 4. 安装项目依赖 ----------
echo ""
echo "[4/6] 安装项目依赖..."
cd "${PROJECT_DIR}"
"${PIP_BIN}" install -e . 2>&1 | tail -5
echo "依赖安装完成"

# ---------- 5. 生成 .env ----------
echo ""
echo "[5/6] 检查 .env 配置..."
if [ -f "${PROJECT_DIR}/.env" ]; then
    echo ".env 已存在，跳过"
else
    cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
    echo ".env 已从 .env.example 生成，请编辑填入实际配置:"
    echo "  vim ${PROJECT_DIR}/.env"
fi

# ---------- 6. 安装 systemd service ----------
echo ""
echo "[6/6] 安装 systemd service..."

# 生成 service 文件（替换路径变量）
CURRENT_USER=$(whoami)
sed -e "s|%PYTHON_BIN%|${PYTHON_BIN}|g" \
    -e "s|%PROJECT_DIR%|${PROJECT_DIR}|g" \
    -e "s|%USER%|${CURRENT_USER}|g" \
    "${PROJECT_DIR}/deploy/mindspider-scheduler.service" | \
    sudo tee /etc/systemd/system/${SERVICE_NAME}.service >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
echo "systemd service 已安装并设为开机自启"

# ---------- 完成 ----------
echo ""
echo "============================================================"
echo "初始化完成！"
echo ""
echo "后续步骤:"
echo "  1. 编辑 .env 填入数据库和 API 配置:"
echo "     vim ${PROJECT_DIR}/.env"
echo ""
echo "  2. 启动调度器:"
echo "     sudo systemctl start ${SERVICE_NAME}"
echo ""
echo "  3. 查看运行状态:"
echo "     sudo systemctl status ${SERVICE_NAME}"
echo "     journalctl -u ${SERVICE_NAME} -f"
echo ""
echo "  4. 验证数据写入:"
echo "     mongosh --eval 'db.getSiblingDB(\"mindspider_raw\").hot_national.countDocuments()'"
echo "============================================================"
