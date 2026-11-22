"""云实例与故障注入预设 Pydantic 模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FaultPresetName = Literal[
    "healthy",
    "nginx_502",
    "port_8080_blocked",
    "icp_unfiled",
    "instance_stopped",
]


class InstanceOut(BaseModel):
    """云实例台账条目。"""

    id: int
    owner_id: int
    owner_name: str | None = None
    instance_name: str
    public_ip: str | None = None
    region: str | None = None
    spec: str | None = None
    os_image: str | None = None
    status: str
    firewall_ports: list[int] = Field(default_factory=list)
    service_status: dict[str, str] = Field(default_factory=dict)
    icp_filed: bool
    swap_mb: int
    updated_at: str


class FaultPresetRequest(BaseModel):
    """一键切换仿真实例故障预设请求。"""

    preset: FaultPresetName
