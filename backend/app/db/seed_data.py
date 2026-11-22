"""种子数据（只有数据，没有逻辑）。逻辑写在 seed.py 里。

演示场景设计（Day 3/4 的 MCP 诊断与故障靶场会用到）：
- ubuntu-web-prod：后端 8080 进程 stopped + 防火墙未放行 8080 + swap=0
  → 一次演示就能覆盖「Nginx 502 / 端口未放行 / 小内存 OOM」三类故障；
- finance-api-node：swap=1024MB，作为「同一问题但配置正常」的对照组；
- crm-backend：status=stopped，用于「实例已停止」分支。

注意：这些是**编造的仿真数据**，IP 不对应真实主机，答辩时要主动说明。
"""

from __future__ import annotations

# 所有演示账号统一密码（仅演示环境，生产必须改）
DEMO_PASSWORD = "Passw0rd!"

# 4 类演示用户：2 个客户（互相隔离验证越权）+ 1 坐席 + 1 管理员
USERS: list[dict] = [
    {"username": "zhangsan", "role": "customer", "display_name": "客户张三"},
    {"username": "lisi", "role": "customer", "display_name": "客户李四"},
    {"username": "wanggong", "role": "agent", "display_name": "技术支持坐席王工"},
    {"username": "admin", "role": "admin", "display_name": "系统管理员"},
]

# 5 台仿真云实例；owner_username 用来在 seed.py 里查 owner_id
INSTANCES: list[dict] = [
    {
        "owner_username": "zhangsan",
        "instance_name": "ubuntu-web-prod",
        "public_ip": "47.94.101.23",
        "region": "华北2（北京）",
        "spec": "2C2G",
        "os_image": "Ubuntu 22.04 LTS",
        "status": "running",
        "firewall_ports": [22, 80, 443],
        "service_status": {"nginx": "running", "backend-8080": "stopped"},
        "icp_filed": 1,
        "swap_mb": 0,
    },
    {
        "owner_username": "zhangsan",
        "instance_name": "app-test-node",
        "public_ip": "47.94.101.88",
        "region": "华北2（北京）",
        "spec": "2C4G",
        "os_image": "Ubuntu 22.04 LTS",
        "status": "running",
        "firewall_ports": [22, 8080],
        "service_status": {"app-8080": "running"},
        "icp_filed": 0,
        "swap_mb": 2048,
    },
    {
        "owner_username": "zhangsan",
        "instance_name": "win-rdp-jump",
        "public_ip": "47.94.102.15",
        "region": "华东1（杭州）",
        "spec": "2C2G",
        "os_image": "Windows Server 2022",
        "status": "running",
        "firewall_ports": [3389],
        "service_status": {"rdp": "running"},
        "icp_filed": 1,
        "swap_mb": 0,
    },
    {
        "owner_username": "lisi",
        "instance_name": "finance-api-node",
        "public_ip": "8.140.55.201",
        "region": "华北2（北京）",
        "spec": "2C2G",
        "os_image": "Ubuntu 22.04 LTS",
        "status": "running",
        "firewall_ports": [22, 443],
        "service_status": {"api-8443": "running"},
        "icp_filed": 1,
        "swap_mb": 1024,
    },
    {
        "owner_username": "lisi",
        "instance_name": "crm-backend",
        "public_ip": "8.140.55.77",
        "region": "华东1（杭州）",
        "spec": "2C4G",
        "os_image": "CentOS 7.9",
        "status": "stopped",
        "firewall_ports": [22, 80],
        "service_status": {"tomcat-8080": "running", "mysql-3306": "running"},
        "icp_filed": 0,
        "swap_mb": 0,
    },
]
