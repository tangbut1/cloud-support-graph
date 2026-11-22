"""Day 6 会话管理与多租户隔离测试。"""

from __future__ import annotations

from tests.conftest import login_headers, make_user


def test_unauthenticated_conversation_access_returns_401(client):
    assert client.post("/api/conversations", json={}).status_code == 401
    assert client.get("/api/conversations").status_code == 401
    assert client.get("/api/conversations/any-id/messages").status_code == 401


def test_create_list_and_tenant_isolation(client, conn):
    make_user(conn, "zhangsan", "customer", "客户张三")
    make_user(conn, "lisi", "customer", "客户李四")
    make_user(conn, "wanggong", "agent", "坐席王工")

    h_zs = login_headers(client, "zhangsan")
    h_ls = login_headers(client, "lisi")
    h_agent = login_headers(client, "wanggong")

    r1 = client.post("/api/conversations", json={"title": "排查 502"}, headers=h_zs)
    assert r1.status_code == 201
    conv_zs = r1.json()
    assert conv_zs["id"] and conv_zs["thread_id"]
    assert conv_zs["id"] != conv_zs["thread_id"]
    assert conv_zs["status"] == "ai_handling"
    assert conv_zs["created_at"]

    r2 = client.post("/api/conversations", json={}, headers=h_ls)
    assert r2.status_code == 201
    conv_ls = r2.json()

    # 客户只看自己的会话
    zs_list = client.get("/api/conversations", headers=h_zs).json()
    assert [c["id"] for c in zs_list] == [conv_zs["id"]]

    ls_list = client.get("/api/conversations", headers=h_ls).json()
    assert [c["id"] for c in ls_list] == [conv_ls["id"]]

    # 坐席看全部，且支持 status 过滤
    all_list = client.get("/api/conversations", headers=h_agent).json()
    assert {c["id"] for c in all_list} == {conv_zs["id"], conv_ls["id"]}
    pending_list = client.get("/api/conversations?status=pending_human", headers=h_agent).json()
    assert pending_list == []

    # 跨租户访问他人会话/消息 -> 统一 404（不泄露存在性）
    assert client.get(f"/api/conversations/{conv_zs['id']}", headers=h_ls).status_code == 404
    assert client.get(f"/api/conversations/{conv_zs['id']}/messages", headers=h_ls).status_code == 404
    assert (
        client.post(
            f"/api/conversations/{conv_zs['id']}/chat/stream",
            data={"message": "窥探他人会话"},
            headers=h_ls,
        ).status_code
        == 404
    )

    # 本人可读取自己的消息列表
    msgs = client.get(f"/api/conversations/{conv_zs['id']}/messages", headers=h_zs)
    assert msgs.status_code == 200
    assert msgs.json() == []
