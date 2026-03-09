#!/bin/bash
# 安装 MindSpider Deep Crawl systemd 服务

set -e

SERVICE_FILE="/etc/systemd/system/mindspider-deep-crawl.service"
PROJECT_DIR="/deploy/parallel-universe/mind-spider"
LOG_DIR="$PROJECT_DIR/logs"

# 创建日志目录
mkdir -p "$LOG_DIR"

# 复制 service 文件
cp "$PROJECT_DIR/deploy/mindspider-deep-crawl.service" "$SERVICE_FILE"

# 重新加载 systemd
systemctl daemon-reload

# 启用开机自启
systemctl enable mindspider-deep-crawl

echo "=========================================="
echo "MindSpider Deep Crawl 服务已安装"
echo "=========================================="
echo ""
echo "常用命令:"
echo "  启动:   systemctl start mindspider-deep-crawl"
echo "  停止:   systemctl stop mindspider-deep-crawl"
echo "  重启:   systemctl restart mindspider-deep-crawl"
echo "  状态:   systemctl status mindspider-deep-crawl"
echo "  日志:   tail -f $LOG_DIR/deep_crawl.log"
echo "  实时:   journalctl -u mindspider-deep-crawl -f"
echo ""
echo "是否现在启动服务? (y/n)"
read -r answer
if [ "$answer" = "y" ]; then
    systemctl start mindspider-deep-crawl
    systemctl status mindspider-deep-crawl
fi
