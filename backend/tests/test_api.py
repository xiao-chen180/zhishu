"""后端 API 契约与鉴权测试

设计：
- 不依赖数据库，保证 CI 环境可稳定运行
- 覆盖：公开接口可访问、受保护接口未登录必须 401、核心路由存在
- 需要数据库的端到端流程请跑 tests/smoke_test.py
"""

import os

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------- 公开接口

@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_root(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "SmartHub" in response.json()["message"]


@pytest.mark.asyncio
async def test_mindmap_examples_is_public(client):
    response = await client.get("/api/v1/mindmap/examples")
    assert response.status_code == 200


# ---------------------------------------------------------------- 鉴权门禁

PROTECTED_ENDPOINTS = [
    ("GET", "/api/v1/profile/me"),
    ("GET", "/api/v1/dashboard/stats"),
    ("GET", "/api/v1/resource/list"),
    ("GET", "/api/v1/admin/stats"),
    ("GET", "/api/v1/admin/users"),
    ("GET", "/api/v1/wrong-questions"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
async def test_protected_endpoint_requires_auth(client, method, path):
    """未携带 token 的受保护接口必须返回 401，不能泄露数据"""
    response = await client.request(method, path)
    assert response.status_code == 401, f"{path} 未鉴权却返回 {response.status_code}"


@pytest.mark.asyncio
async def test_invalid_token_rejected(client):
    response = await client.get(
        "/api/v1/profile/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------- 路由契约

def test_openapi_exposes_core_routes():
    """核心路由必须存在，防止重构时误删"""
    paths = set(app.openapi()["paths"])
    expected = {
        "/health",
        "/",
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/auth/send-code",
        "/api/v1/profile/me",
        "/api/v1/profile/assess/stream",
        "/api/v1/chat/stream",
        "/api/v1/dashboard/stats",
        "/api/v1/resource/list",
        "/api/v1/resource/exercises/generate/stream",
        "/api/v1/study-plan/generate-path",
        "/api/v1/mindmap/generate",
        "/api/v1/wrong-questions",
        "/api/v1/evaluation/record",
        "/api/v1/admin/stats",
        "/api/v1/admin/exercises",
    }
    missing = expected - paths
    assert not missing, f"缺失核心路由: {sorted(missing)}"


def test_stream_endpoints_are_post():
    """流式生成接口必须是 POST（避免 GET 被浏览器/代理缓存重放）"""
    paths = app.openapi()["paths"]
    for path in [
        "/api/v1/chat/stream",
        "/api/v1/profile/assess/stream",
        "/api/v1/resource/exercises/generate/stream",
    ]:
        assert "post" in paths[path], f"{path} 应为 POST"


# ---------------------------------------------------------------- 可选：真实登录

@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="需要本地数据库，设置 RUN_DB_TESTS=1 启用",
)
async def test_login_returns_token(client):
    response = await client.post(
        "/api/v1/auth/login",
        json={"student_no": os.getenv("SMOKE_STUDENT_NO", "admin"),
              "password": os.getenv("SMOKE_PASSWORD", "admin123")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("token")
    assert data.get("student", {}).get("id")