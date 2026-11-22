"""云实例台账与管理员一键故障注入路由。

权限边界：
    - GET /api/instances：客户只看自己名下实例；坐席/管理员看全部，可按 customer_id 筛选；
    - PATCH /api/admin/instances/{id}/fault-preset：仅管理员可用，用于答辩演示一键切换故障场景。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.deps import AdminOnly, CurrentUser
from app.db.deps import get_db
from app.schemas.instance import FaultPresetRequest, InstanceOut

router = APIRouter(tags=["云实例"])

FAULT_PRESETS: dict[str, dict] = {
    "healthy": {
        "status": "running",
        "firewall_ports": [22, 80, 443, 8080],
        "service_status": {"nginx": "running", "backend-8080": "running"},
        "icp_filed": 1,
        "swap_mb": 2048,
    },
    "nginx_502": {
        "status": "running",
        "firewall_ports": [22, 80, 443],
        "service_status": {"nginx": "running", "backend-8080": "stopped"},
        "icp_filed": 1,
        "swap_mb": 0,
    },
    "port_8080_blocked": {
        "status": "running",
        "firewall_ports": [22, 80, 443],
        "service_status": {"nginx": "running", "backend-8080": "running"},
        "icp_filed": 1,
        "swap_mb": 1024,
    },
    "icp_unfiled": {
        "status": "running",
        "firewall_ports": [22, 80, 443],
        "service_status": {"nginx": "running"},
        "icp_filed": 0,
        "swap_mb": 1024,
    },
    "instance_stopped": {
        "status": "stopped",
        "firewall_ports": [22, 80],
        "service_status": {"nginx": "stopped", "backend-8080": "stopped"},
        "icp_filed": 1,
        "swap_mb": 0,
    },
}


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _instance_out(row: sqlite3.Row) -> InstanceOut:
    try:
        ports = json.loads(row["firewall_ports_json"] or "[]")
    except json.JSONDecodeError:
        ports = []
    try:
        services = json.loads(row["service_status_json"] or "{}")
    except json.JSONDecodeError:
        services = {}
    return InstanceOut(
        id=row["id"],
        owner_id=row["owner_id"],
        owner_name=row["owner_name"] if "owner_name" in row.keys() else None,
        instance_name=row["instance_name"],
        public_ip=row["public_ip"],
        region=row["region"],
        spec=row["spec"],
        os_image=row["os_image"],
        status=row["status"],
        firewall_ports=[int(p) for p in ports],
        service_status={str(k): str(v) for k, v in services.items()},
        icp_filed=bool(row["icp_filed"]),
        swap_mb=int(row["swap_mb"]),
        updated_at=row["updated_at"],
    )


@router.get("/api/instances", response_model=list[InstanceOut], summary="云实例列表（客户只看自己）")
def list_instances(
    user: CurrentUser,
    customer_id: int | None = Query(default=None, description="按客户 ID 筛选（仅坐席/管理员生效）"),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[InstanceOut]:
    sql = """
        SELECT ci.*, u.display_name AS owner_name
        FROM cloud_instances AS ci
        JOIN users AS u ON u.id = ci.owner_id
    """
    conditions: list[str] = []
    params: list[object] = []

    if user["role"] == "customer":
        # 客户强制按本人过滤，忽略外部传入的 customer_id，防止越权枚举
        conditions.append("ci.owner_id = ?")
        params.append(user["id"])
    elif customer_id is not None:
        conditions.append("ci.owner_id = ?")
        params.append(customer_id)

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY ci.id ASC"

    rows = conn.execute(sql, params).fetchall()
    return [_instance_out(row) for row in rows]


@router.patch(
    "/api/admin/instances/{instance_id}/fault-preset",
    response_model=InstanceOut,
    summary="管理员一键切换实例故障预设（仅管理员）",
)
def apply_fault_preset(
    instance_id: int,
    payload: FaultPresetRequest,
    admin: AdminOnly,
    conn: sqlite3.Connection = Depends(get_db),
) -> InstanceOut:
    _ = admin
    exists = conn.execute("SELECT id FROM cloud_instances WHERE id = ?", (instance_id,)).fetchone()
    if exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="云实例不存在")

    preset = FAULT_PRESETS[payload.preset]
    now = _now_text()
    conn.execute(
        """
        UPDATE cloud_instances
        SET status = ?,
            firewall_ports_json = ?,
            service_status_json = ?,
            icp_filed = ?,
            swap_mb = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            preset["status"],
            json.dumps(preset["firewall_ports"], ensure_ascii=False),
            json.dumps(preset["service_status"], ensure_ascii=False),
            int(preset["icp_filed"]),
            int(preset["swap_mb"]),
            now,
            instance_id,
        ),
    )
    conn.commit()

    row = conn.execute(
        """
        SELECT ci.*, u.display_name AS owner_name
        FROM cloud_instances AS ci
        JOIN users AS u ON u.id = ci.owner_id
        WHERE ci.id = ?
        """,
        (instance_id,),
    ).fetchone()
    return _instance_out(row)
