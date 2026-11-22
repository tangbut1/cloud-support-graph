#!/usr/bin/env bash
# 阿里云 2C2G (Ubuntu 24.04) 幂等初始化脚本：配置 2G Swap + 安装 Docker 与 Compose v2
set -euo pipefail

echo "[1/3] 检查并配置 2 GiB Swap（防止 2C2G 构建或突发请求 OOM）..."
if ! swapon --show | grep -q "/swapfile"; then
  if [ ! -f /swapfile ]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
  fi
  sudo swapon /swapfile
  if ! grep -q "/swapfile" /etc/fstab; then
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  fi
fi
free -h

echo "[2/3] 检查并安装 Docker 与 docker-compose-v2..."
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y docker.io docker-compose-v2 git curl
  sudo systemctl enable --now docker
else
  echo "Docker 已安装: $(docker --version)"
fi

echo "[3/3] 检查 Docker Compose 版本..."
docker compose version
echo "服务器初始化完成。"
