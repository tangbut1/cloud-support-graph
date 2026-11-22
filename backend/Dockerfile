# 说明：不使用 # syntax=docker/dockerfile:1 指令，避免额外拉取 Docker Hub 上的
# dockerfile frontend 镜像（国内网络下这是额外的故障点，本项目不需要它的高级特性）。
FROM python:3.12-slim

# 2C2G 小机器：不写 pyc、不缓冲输出
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖，充分利用 Docker 层缓存
COPY pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip setuptools wheel && pip install .

# 运行期数据目录（compose 里挂载宿主机 ./data）
RUN mkdir -p /app/data

EXPOSE 8000

# 单 worker + 全异步：避免多进程写 SQLite 冲突，内存压在 ~200MB
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
