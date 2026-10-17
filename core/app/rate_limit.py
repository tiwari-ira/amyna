"""[2.1] Per-IP rate limiting and an authentication-failure circuit.

Two guards, one module, zero dependencies (deployment is single-process today,
decision D11; the state moves to Redis before any multi-worker scale-out):

1. A sliding window per client IP across all routes (``RATE_LIMIT_PER_MINUTE``,
   default 600 -- generous for a patient flow, brutal for a script).
2. An auth-failure circuit: after ``MAX_401S`` rejected tokens inside the
   window, every further request from that IP is answered 429 without touching
   business logic. Guessing service tokens must be expensive.

``/health`` is exempt (it is the liveness probe). Everything else -- including
the webhooks and the recording playback route -- is limited.
"""

import threading
import time
from collections import deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

WINDOW_S = 60.0
MAX_401S = 20
RETRY_AFTER_S = 60


class _Window:
    def __init__(self) -> None:
        self._hits: deque[float] = deque()
        self._auth_failures: deque[float] = deque()

    def _prune(self, now: float, hits: deque[float]) -> None:
        while hits and now - hits[0] > WINDOW_S:
            hits.popleft()

    def hit(self, now: float, limit: int) -> bool:
        """Record a request; False when the window is full (rate limited)."""
        self._prune(now, self._hits)
        if len(self._hits) >= limit:
            return False
        self._hits.append(now)
        return True

    def auth_failed(self, now: float) -> None:
        self._prune(now, self._auth_failures)
        self._auth_failures.append(now)

    def locked_out(self, now: float) -> bool:
        self._prune(now, self._auth_failures)
        return len(self._auth_failures) >= MAX_401S


def install_rate_limit(app: FastAPI, *, per_minute: int) -> None:
    state: dict[str, _Window] = {}
    lock = threading.Lock()

    def _window_for(key: str) -> _Window:
        window = state.get(key)
        if window is None:
            window = state[key] = _Window()
            if len(state) > 10_000:  # bound: oldest entries age out with the window
                state.clear()
        return window

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path == "/health":
            return await call_next(request)
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with lock:
            window = _window_for(client)
            if window.locked_out(now) or not window.hit(now, per_minute):
                return JSONResponse(
                    status_code=429,
                    content={"detail": {"code": "rate_limited", "message": "Too many requests."}},
                    headers={"Retry-After": str(RETRY_AFTER_S)},
                )
        response = await call_next(request)
        if response.status_code == 401:
            with lock:
                _window_for(client).auth_failed(time.monotonic())
        return response
