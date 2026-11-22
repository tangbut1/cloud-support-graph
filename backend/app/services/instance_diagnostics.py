r"""云实例确定性诊断：把"实例状态字段"翻译成人话结论。

【为什么诊断必须由代码而不是大模型做】
    如果让模型看 JSON 自己判断，它可能把"8080 未放行"漏掉，或者编造不存在的问题。
    这里把判断规则写成确定性代码：输入实例行，输出"检查项 + 结论 + 建议动作"。
    大模型只负责把这份结论**组织成自然的回答**，不负责判断。

【诊断规则（当前版本）】
    R1 实例状态非 running            -> critical：实例已停止
    R2 服务状态里有 stopped 的进程    -> critical：后端进程未运行
    R3 服务占用的端口不在安全组放行列表 -> critical：端口未放行（云防火墙拦截）
    R4 规格为 2C2G 且未配置 Swap      -> warning ：小内存机器易被 OOM Killer 杀进程
    R5 未完成 ICP 备案                -> warning ：域名访问可能被拦截

【演示数据对应关系】（seed_data.py）
    ubuntu-web-prod：R2（backend-8080 stopped）+ R3（8080 不在 [22,80,443]）+ R4（swap=0）
        -> 这三个叠加正是"Nginx 502"的典型根因，答辩时可以完整讲一遍
    finance-api-node：swap=1024，作为"配置正常"的对照组
    crm-backend：status=stopped，命中 R1
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# 从服务名里提取端口，例如 "backend-8080" -> 8080、"api-8443" -> 8443
_PORT_IN_NAME = re.compile(r"-(\d{2,5})$")

# 常见规格里带 "2C2G" 这类写法，用来识别小内存机器
_SMALL_SPEC_MARKERS = ("2C2G", "2c2g", "1C1G", "1c1g")


@dataclass
class DiagnosticCheck:
    """一条检查结果。"""

    code: str
    level: str          # ok / warning / critical
    message: str

    def as_dict(self) -> dict:
        return {"code": self.code, "level": self.level, "message": self.message}


@dataclass
class DiagnosisResult:
    """一台实例的完整诊断结论。"""

    instance_id: int
    instance_name: str
    risk_level: str                                   # ok / warning / critical
    checks: list[DiagnosticCheck] = field(default_factory=list)
    conclusion: str = ""
    suggested_actions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "instance_name": self.instance_name,
            "risk_level": self.risk_level,
            "checks": [check.as_dict() for check in self.checks],
            "conclusion": self.conclusion,
            "suggested_actions": self.suggested_actions,
        }


def _loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def diagnose_instance(row) -> DiagnosisResult:
    """对一行 cloud_instances 记录做确定性诊断。"""
    instance_id = int(row["id"])
    instance_name = str(row["instance_name"])
    spec = str(row["spec"] or "")
    status = str(row["status"] or "")
    firewall_ports = _loads(row["firewall_ports_json"], [])
    service_status = _loads(row["service_status_json"], {})
    icp_filed = int(row["icp_filed"] or 0)
    swap_mb = int(row["swap_mb"] or 0)

    checks: list[DiagnosticCheck] = []
    actions: list[str] = []

    # R1 实例状态
    if status != "running":
        checks.append(
            DiagnosticCheck(
                code="INSTANCE_NOT_RUNNING",
                level="critical",
                message=f"实例状态为 {status}，服务无法对外提供访问",
            )
        )
        actions.append(f"在控制台启动实例 {instance_name}")
    else:
        checks.append(DiagnosticCheck(code="INSTANCE_RUNNING", level="ok", message="实例运行中"))

    # R2 服务进程状态
    stopped_services = [name for name, state in service_status.items() if str(state) == "stopped"]
    running_services = [name for name, state in service_status.items() if str(state) == "running"]
    if stopped_services:
        checks.append(
            DiagnosticCheck(
                code="SERVICE_STOPPED",
                level="critical",
                message="以下后端进程处于停止状态：" + "、".join(stopped_services),
            )
        )
        for name in stopped_services:
            actions.append(f"启动进程 {name}（并查看其日志确认崩溃原因）")
    elif running_services:
        checks.append(
            DiagnosticCheck(
                code="SERVICE_RUNNING",
                level="ok",
                message="已登记的服务进程均在运行：" + "、".join(running_services),
            )
        )

    # R3 端口放行（从服务名推断端口，再看安全组是否放行）
    firewall_set = {int(port) for port in firewall_ports if isinstance(port, int)}
    for name in service_status:
        match = _PORT_IN_NAME.search(name)
        if not match:
            continue
        port = int(match.group(1))
        if port not in firewall_set:
            checks.append(
                DiagnosticCheck(
                    code="PORT_NOT_ALLOWED",
                    level="critical",
                    message=f"服务端口 {port}（来自 {name}）未在安全组放行列表中，"
                    f"当前放行端口为 {sorted(firewall_set)}",
                )
            )
            actions.append(f"在安全组入方向放行 TCP {port}")

    # R4 Swap
    if any(marker in spec for marker in _SMALL_SPEC_MARKERS) and swap_mb == 0:
        checks.append(
            DiagnosticCheck(
                code="SWAP_NOT_CONFIGURED",
                level="warning",
                message=f"规格 {spec} 未配置 Swap，内存紧张时进程可能被 OOM Killer 终止",
            )
        )
        actions.append("创建 2 GiB swap 文件并在 /etc/fstab 中开机挂载")

    # R5 备案
    if icp_filed == 0:
        checks.append(
            DiagnosticCheck(
                code="ICP_NOT_FILED",
                level="warning",
                message="该实例关联域名未完成 ICP 备案，大陆地域域名访问可能被拦截",
            )
        )
        actions.append("在云厂商备案系统提交 ICP 备案；备案期间可临时使用 IP 访问")

    # 汇总风险等级与结论
    levels = {check.level for check in checks}
    if "critical" in levels:
        risk_level = "critical"
    elif "warning" in levels:
        risk_level = "warning"
    else:
        risk_level = "ok"

    critical_messages = [check.message for check in checks if check.level == "critical"]
    if critical_messages:
        conclusion = "；".join(critical_messages) + "。"
        # 针对演示数据的最典型场景给一句直白结论
        backend_down = any("处于停止状态" in msg for msg in critical_messages)
        port_blocked = any("未在安全组放行" in msg for msg in critical_messages)
        if backend_down and port_blocked:
            conclusion += (
                " Nginx 作为反向代理会因连不上后端而返回 502，"
                "属于典型的'后端进程停止 + 端口未放行'叠加故障。"
            )
    elif risk_level == "warning":
        conclusion = "实例当前可提供服务，但存在配置风险，建议尽快处理警告项。"
    else:
        conclusion = "未发现异常。"

    return DiagnosisResult(
        instance_id=instance_id,
        instance_name=instance_name,
        risk_level=risk_level,
        checks=checks,
        conclusion=conclusion,
        suggested_actions=actions,
    )
