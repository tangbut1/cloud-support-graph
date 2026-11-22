"""Day 7 云实例列表隔离与管理员故障注入预设测试。"""

from __future__ import annotations

from tests.conftest import login_headers, make_instance, make_user


def test_instances_isolation_and_filter(client, conn):
    zs_id = make_user(conn, "zhangsan", "customer", "客户张三")
    ls_id = make_user(conn, "lisi", "customer", "客户李四")
    make_user(conn, "wanggong", "agent", "坐席王工")

    make_instance(conn, zs_id, "ubuntu-web-prod")
    make_instance(conn, ls_id, "finance-api-node")

    h_zs = login_headers(client, "zhangsan")
    h_ls = login_headers(client, "lisi")
    h_agent = login_headers(client, "wanggong")

    # 未登录 401
    assert client.get("/api/instances").status_code == 401

    # 客户只看自己的，且传 customer_id 企图越权会被强制忽略
    zs_items = client.get(f"/api/instances?customer_id={ls_id}", headers=h_zs).json()
    assert [i["instance_name"] for i in zs_items] == ["ubuntu-web-prod"]

    ls_items = client.get("/api/instances", headers=h_ls).json()
    assert [i["instance_name"] for i in ls_items] == ["finance-api-node"]

    # 坐席可看全部并按 customer_id 筛选
    all_items = client.get("/api/instances", headers=h_agent).json()
    assert len(all_items) == 2
    filtered = client.get(f"/api/instances?customer_id={zs_id}", headers=h_agent).json()
    assert [i["instance_name"] for i in filtered] == ["ubuntu-web-prod"]


def test_fault_preset_admin_only_and_state_change(client, conn):
    zs_id = make_user(conn, "zhangsan", "customer", "客户张三")
    make_user(conn, "wanggong", "agent", "坐席王工")
    make_user(conn, "admin", "admin", "系统管理员")
    inst_id = make_instance(conn, zs_id, "ubuntu-web-prod")

    h_zs = login_headers(client, "zhangsan")
    h_agent = login_headers(client, "wanggong")
    h_admin = login_headers(client, "admin")

    # 客户和坐席无权调用
    assert (
        client.patch(
            f"/api/admin/instances/{inst_id}/fault-preset",
            json={"preset": "nginx_502"},
            headers=h_zs,
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/admin/instances/{inst_id}/fault-preset",
            json={"preset": "nginx_502"},
            headers=h_agent,
        ).status_code
        == 403
    )

    # 管理员切换为 nginx_502 故障预设
    r1 = client.patch(
        f"/api/admin/instances/{inst_id}/fault-preset",
        json={"preset": "nginx_502"},
        headers=h_admin,
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["status"] == "running"
    assert body1["service_status"]["backend-8080"] == "stopped"
    assert 8080 not in body1["firewall_ports"]
    assert body1["swap_mb"] == 0

    # 管理员一键恢复为 healthy
    r2 = client.patch(
        f"/api/admin/instances/{inst_id}/fault-preset",
        json={"preset": "healthy"},
        headers=h_admin,
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["service_status"]["backend-8080"] == "running"
    assert 8080 in body2["firewall_ports"]
    assert body2["swap_mb"] == 2048

    # 不存在实例 -> 404
    assert (
        client.patch(
            "/api/admin/instances/99999/fault-preset",
            json={"preset": "healthy"},
            headers=h_admin,
        ).status_code
        == 404
    )
