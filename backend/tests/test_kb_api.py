"""知识库接口测试：权限边界 + 检索预览 + 文档管理。

重点验证两件事：
    1. 知识库管理接口仅管理员可用（客户/坐席访问必须 403）；
    2. 检索预览接口返回的 accepted 与阈值判定一致——这是前端"是否展示引用"
       和后端"是否允许大模型自由作答"的共同依据。
"""

from __future__ import annotations

import pytest

from app.services.kb_indexer import index_all
from tests.conftest import login_headers, make_user


@pytest.fixture()
def kb_client(conn, client):
    index_all(conn)
    make_user(conn, "zhangsan", "customer", "客户张三")
    make_user(conn, "wanggong", "agent", "技术支持坐席王工")
    make_user(conn, "admin", "admin", "系统管理员")
    return client


# ===========================================================================
# 一、权限边界
# ===========================================================================
@pytest.mark.parametrize("username", ["zhangsan", "wanggong"])
def test_kb_endpoints_reject_non_admin(kb_client, username):
    headers = login_headers(kb_client, username)
    assert kb_client.get("/api/kb/docs", headers=headers).status_code == 403
    assert (
        kb_client.post(
            "/api/kb/search-preview", headers=headers, json={"query": "Nginx 502"}
        ).status_code
        == 403
    )


def test_kb_endpoints_require_login(kb_client):
    assert kb_client.get("/api/kb/docs").status_code == 401


# ===========================================================================
# 二、文档列表与新增
# ===========================================================================
def test_admin_can_list_documents(kb_client):
    headers = login_headers(kb_client, "admin")
    resp = kb_client.get("/api/kb/docs", headers=headers)
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) == 15
    assert all(doc["chunk_count"] >= 1 for doc in docs)


def test_admin_can_create_document_and_chunks_appear(kb_client):
    headers = login_headers(kb_client, "admin")
    resp = kb_client.post(
        "/api/kb/docs",
        headers=headers,
        json={
            "title": "自定义运维文档",
            "category": "测试",
            "markdown": "## 现象\n自定义故障现象\n\n## 处理\n自定义处理步骤\n",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["chunk_count"] == 2


# ===========================================================================
# 三、检索预览与拒答
# ===========================================================================
def test_search_preview_accepts_answerable_question(kb_client):
    headers = login_headers(kb_client, "admin")
    body = kb_client.post(
        "/api/kb/search-preview",
        headers=headers,
        json={"query": "Nginx 502 怎么排查"},
    ).json()
    assert body["accepted"] is True
    assert body["top_score"] >= body["threshold"]
    assert body["hits"]
    assert "Nginx" in body["hits"][0]["doc_title"]
    assert "[" in body["hits"][0]["citation"]


def test_search_preview_refuses_unanswerable_question(kb_client):
    headers = login_headers(kb_client, "admin")
    body = kb_client.post(
        "/api/kb/search-preview",
        headers=headers,
        json={"query": "帮我申请一张阿里云代金券"},
    ).json()
    assert body["accepted"] is False
    assert body["top_score"] < body["threshold"]


def test_search_preview_respects_custom_threshold(kb_client):
    headers = login_headers(kb_client, "admin")
    body = kb_client.post(
        "/api/kb/search-preview",
        headers=headers,
        json={"query": "Nginx 502 怎么排查", "min_score": 0.99},
    ).json()
    assert body["threshold"] == 0.99
    assert body["accepted"] is False
