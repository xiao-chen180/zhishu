"""进程内固定窗口限流 — 用于登录、验证码等敏感端点

注意：
- 仅对单进程有效；多 worker / 多实例部署需替换为 Redis 实现
- 采用固定窗口 + 惰性清理，规模受 _MAX_KEYS 约束，避免内存无限增长
"""

import threading
import time
from collections import defaultdict

_MAX_KEYS = 10_000


class FixedWindowLimiter:
    """固定窗口限流器：window_seconds 内最多 max_calls 次"""

    def __init__(self, max_calls: int, window_seconds: int):
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """记录一次调用；超出配额返回 False"""
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, ()) if now - t < self.window]
            if len(hits) >= self.max_calls:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits

            # 惰性清理，防止字典无限增长
            if len(self._hits) > _MAX_KEYS:
                self._hits = {k: v for k, v in self._hits.items() if v}
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


# 登录：同一 IP + 学号，5 分钟内最多 10 次
login_limiter = FixedWindowLimiter(max_calls=10, window_seconds=300)

# 验证码：同一 IP + 手机号，5 分钟内最多 3 次
send_code_limiter = FixedWindowLimiter(max_calls=3, window_seconds=300)