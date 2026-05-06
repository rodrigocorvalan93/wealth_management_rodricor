# -*- coding: utf-8 -*-
"""
api/ratelimit.py

Rate limiter simple in-memory (sliding-window).

NO es production-grade para deploy multi-worker — para PythonAnywhere
single-worker o gunicorn con 1 worker funciona, pero con múltiples
workers cada uno tiene su contador (más permisivo).

Para multi-worker real, reemplazar por Redis + leaky-bucket.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional


class SlidingWindowLimiter:
    """Sliding-window counter. Cada (key, action) tiene su propia deque.

    Default: 60 requests / 60 segundos por (token, ip).
    Para mutations (POST/PUT/DELETE), usamos límites más estrictos.
    """

    def __init__(self):
        self._buckets: dict[tuple[str, str], deque] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, action: str, limit: int, window_s: int) -> tuple[bool, int]:
        """Registra un hit. Devuelve (allowed, remaining)."""
        now = time.monotonic()
        bucket_key = (key, action)
        with self._lock:
            dq = self._buckets.setdefault(bucket_key, deque())
            cutoff = now - window_s
            # Drop hits viejos
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= limit:
                return (False, 0)
            dq.append(now)
            return (True, max(0, limit - len(dq)))

    def reset(self, key: Optional[str] = None):
        """Útil para tests."""
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                for k in list(self._buckets.keys()):
                    if k[0] == key:
                        del self._buckets[k]


_limiter = SlidingWindowLimiter()


def check(key: str, action: str = "default", limit: int = 120,
          window_s: int = 60) -> tuple[bool, int]:
    """Wrapper conveniente. Usar desde un before_request hook."""
    return _limiter.hit(key, action, limit, window_s)


def reset_all():
    _limiter.reset()
