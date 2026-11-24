"""端到端冒烟测试 — 核心演示路径

跑法: cd backend && python -m tests.smoke_test
账号: 默认 admin / admin123（可用环境变量 SMOKE_STUDENT_NO / SMOKE_PASSWORD 覆盖）
输出: 每步 PASS/FAIL + 关键数据 + 错误信息

注意: 本模块会被 pytest 收集，所以不能在任何导入时修改 sys.stdout，
UTF-8 包装只在 __main__ 里执行。
"""

import asyncio
import io
import json
import os
import sys
import time

import httpx

BASE = "http://localhost:8001/api/v1"
TIMEOUT = 180.0

PASS = "[PASS]"
FAIL = "[FAIL]"
INFO = "[INFO]"

# 登录后填充
TOKEN: str = ""
STUDENT_ID: str = ""


class StepFailed(Exception):
    pass


def header(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def ok(msg: str) -> None:
    print(f"  {PASS} {msg}")


def bad(msg: str) -> None:
    print(f"  {FAIL} {msg}")
    raise StepFailed(msg)


def info(msg: str) -> None:
    print(f"  {INFO} {msg}")


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}


async def collect_sse_events(client: httpx.AsyncClient, path: str, payload: dict, hard_cap_events: int = 20000) -> list[dict]:
    """发起 SSE 请求并解析所有事件 (有硬上限避免 LLM 抽风卡死)"""
    events: list[dict] = []
    t0 = time.time()
    headers = {"Accept": "text/event-stream", **auth_headers()}
    async with client.stream(
        "POST",
        f"{BASE}{path}",
        json=payload,
        headers=headers,
        timeout=TIMEOUT,
    ) as resp:
        if resp.status_code != 200:
            text = await resp.aread()
            raise RuntimeError(f"HTTP {resp.status_code}: {text.decode('utf-8', errors='replace')[:300]}")

        async for line in resp.aiter_lines():
            if len(events) >= hard_cap_events:
                break
            if time.time() - t0 > TIMEOUT - 5:
                break
            if not line or not line.startswith("data: "):
                continue
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                continue
    return events


async def step0_health(client: httpx.AsyncClient) -> None:
    header("Step 0: Health Check")
    r = await client.get("http://localhost:8001/health", timeout=5)
    if r.status_code == 200 and r.json().get("status") == "healthy":
        ok(f"/health 200, body={r.text[:60]}")
    else:
        bad(f"/health {r.status_code}: {r.text[:200]}")


async def step_login(client: httpx.AsyncClient) -> None:
    global TOKEN, STUDENT_ID
    header("Step 1: Login (POST /auth/login)")
    student_no = os.getenv("SMOKE_STUDENT_NO", "admin")
    password = os.getenv("SMOKE_PASSWORD", "admin123")
    r = await client.post(f"{BASE}/auth/login", json={"student_no": student_no, "password": password}, timeout=15)
    if r.status_code != 200:
        bad(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    TOKEN = data.get("token", "")
    STUDENT_ID = data.get("student", {}).get("id", "")
    if not TOKEN or not STUDENT_ID:
        bad("登录响应缺少 token/student.id")
    info(f"student_no={student_no}, id={STUDENT_ID[:8]}..., token 长度={len(TOKEN)}")
    ok("登录成功，后续请求将携带 Authorization")


async def step_profile_assess_stream(client: httpx.AsyncClient) -> None:
    header("Step 2: F1 对话式画像 (POST /profile/assess/stream)")
    t0 = time.time()
    try:
        events = await collect_sse_events(client, "/profile/assess/stream", {})
    except Exception as e:
        bad(f"SSE error in {time.time()-t0:.1f}s: {e}")
        return

    dur = time.time() - t0
    types = [e.get("type") for e in events]
    tokens = [e for e in events if e.get("type") == "token"]
    errors = [e for e in events if e.get("type") == "error"]
    dones = [e for e in events if e.get("type") == "done"]
    info(f"events: {len(events)}, types={dict((t, types.count(t)) for t in set(types))}")
    info(f"tokens: {len(tokens)} ({sum(len(t.get('content','')) for t in tokens)} chars)")
    if errors:
        bad(f"server error: {errors[0].get('message', '?')[:200]}")
    if not tokens:
        bad("no token events (画像对话未返回内容)")
    if not dones:
        bad("no done event (stream 未正常结束)")
    ok(f"SSE 200 in {dur:.1f}s, {len(tokens)} tokens")


async def step_chat_stream(client: httpx.AsyncClient) -> None:
    header("Step 3: F4 对话流式 (POST /chat/stream) — 真逐 token")
    payload = {"student_id": STUDENT_ID, "message": "什么是反向传播算法?"}
    t0 = time.time()
    try:
        events = await collect_sse_events(client, "/chat/stream", payload)
    except Exception as e:
        bad(f"SSE error in {time.time()-t0:.1f}s: {e}")
        return

    dur = time.time() - t0
    types = [e.get("type") for e in events]
    tokens = [e for e in events if e.get("type") == "token"]
    results = [e for e in events if e.get("type") == "result"]
    errors = [e for e in events if e.get("type") == "error"]
    dones = [e for e in events if e.get("type") == "done"]
    info(f"events: {len(events)}, types={dict((t, types.count(t)) for t in set(types))}")
    info(f"tokens: {len(tokens)} ({sum(len(t.get('content','')) for t in tokens)} chars)")
    if errors:
        bad(f"server reported error: {errors[0].get('message', '?')[:200]}")
    if not tokens:
        bad("no token events received (不是真流式!)")
    if not dones:
        bad("no done event (stream didn't close cleanly)")
    ok(f"SSE stream 200 in {dur:.1f}s, {len(tokens)} tokens, {len(results)} result(s)")


async def step_generate_path(client: httpx.AsyncClient) -> None:
    header("Step 4: F3 学习路径生成 (POST /study-plan/generate-path)")
    payload = {"target_knowledge": "机器学习", "current_level": "beginner"}
    t0 = time.time()
    r = await client.post(f"{BASE}/study-plan/generate-path", json=payload, headers=auth_headers(), timeout=TIMEOUT)
    dur = time.time() - t0
    if r.status_code != 200:
        bad(f"HTTP {r.status_code} in {dur:.1f}s: {r.text[:300]}")
    data = r.json().get("data", {}) or {}
    nodes = data.get("nodes", [])
    if not nodes:
        bad("路径生成结果缺少 nodes")
    info(f"path id: {data.get('id', '?')[:8]}, name: {data.get('name', '?')}")
    info(f"nodes: {len(nodes)}, edges: {len(data.get('edges', [])) if isinstance(data.get('edges'), list) else 'n/a'}")
    ok(f"200 in {dur:.1f}s, {len(nodes)} nodes")


async def step_dashboard(client: httpx.AsyncClient) -> None:
    header("Step 5: Dashboard Stats (GET /dashboard/stats)")
    r = await client.get(f"{BASE}/dashboard/stats", params={"student_id": STUDENT_ID}, headers=auth_headers(), timeout=15)
    if r.status_code != 200:
        bad(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    for field in ("knowledge_points", "learning_hours", "accuracy", "path_progress"):
        info(f"{field}={data.get(field)}")
    ok("dashboard 200 with real data")


async def step_evaluation_record(client: httpx.AsyncClient) -> None:
    header("Step 6: F5 学习行为记录 (POST /evaluation/record)")
    payload = {
        "student_id": STUDENT_ID,
        "action": "view",
        "resource_type": "knowledge",
        "knowledge_point": "卷积神经网络 CNN",
        "duration_seconds": 120,
    }
    r = await client.post(f"{BASE}/evaluation/record", json=payload, headers=auth_headers(), timeout=15)
    if r.status_code != 200:
        bad(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    ok(f"record saved, id={str(data.get('record_id', '?'))[:8]}")


async def step_mindmap(client: httpx.AsyncClient) -> None:
    header("Step 7: F2 思维导图 (POST /mindmap/generate)")
    payload = {"student_id": STUDENT_ID, "knowledge_point": "Transformer 架构"}
    t0 = time.time()
    r = await client.post(f"{BASE}/mindmap/generate", json=payload, headers=auth_headers(), timeout=TIMEOUT)
    dur = time.time() - t0
    if r.status_code != 200:
        bad(f"HTTP {r.status_code} in {dur:.1f}s: {r.text[:300]}")
    data = r.json()
    mindmap = data.get("mindmap", {}) or {}
    mm = mindmap.get("mermaid_code", "")
    if not mindmap:
        bad("response body missing 'mindmap' key")
    info(f"title: {mindmap.get('title', '?')}")
    info(f"mermaid_code length: {len(mm)} chars")
    if not mm:
        bad("mermaid_code is empty (LLM/parse failure)")
    info(f"mermaid preview: {mm[:80].replace(chr(10), ' / ')}")
    ok(f"200 in {dur:.1f}s, {len(mm)} chars mermaid")


async def main() -> int:
    print("=" * 70)
    print(f"  智枢 (SmartHub) 端到端冒烟测试 — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Backend: {BASE}")
    print("=" * 70)

    async with httpx.AsyncClient() as client:
        results: dict[str, bool] = {}
        steps = [
            ("0.health", step0_health),
            ("1.login", step_login),
            ("2.profile_assess", step_profile_assess_stream),
            ("3.chat_stream", step_chat_stream),
            ("4.generate_path", step_generate_path),
            ("5.dashboard", step_dashboard),
            ("6.evaluation_record", step_evaluation_record),
            ("7.mindmap", step_mindmap),
        ]

        for name, fn in steps:
            try:
                await fn(client)
                results[name] = True
            except StepFailed:
                results[name] = False
            except Exception as e:
                print(f"  {FAIL} unexpected: {e}")
                results[name] = False

    print("\n" + "=" * 70)
    print("  总结")
    print("=" * 70)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, ok_flag in results.items():
        marker = PASS if ok_flag else FAIL
        print(f"  {marker} {name}")
    print(f"\n  Total: {passed}/{total} passed")
    print("=" * 70)

    return 0 if passed == total else 1


def _force_utf8_stdout() -> None:
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


if __name__ == "__main__":
    _force_utf8_stdout()
    sys.exit(asyncio.run(main()))
