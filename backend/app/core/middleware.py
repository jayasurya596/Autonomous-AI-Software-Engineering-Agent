"""Bounded request middleware for correlation, rate and body limits."""

import ipaddress
import json
import logging
import math
import time
import uuid
from collections import OrderedDict, deque
from contextvars import ContextVar
from threading import Lock

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
logger = logging.getLogger("app.http")


def _error_response(
    scope: Scope,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = scope.get("state", {}).get("request_id")
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
            }
        },
        headers=headers,
    )


class RequestContextMiddleware:
    """Assign a safe request ID, add defensive headers and log only metadata."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        supplied_id = next(
            (
                value.decode("latin-1")
                for name, value in scope.get("headers", [])
                if name.lower() == b"x-request-id"
            ),
            "",
        )
        try:
            request_id = str(uuid.UUID(supplied_id))
        except (ValueError, AttributeError):
            request_id = str(uuid.uuid4())
        state["request_id"] = request_id

        started_at = time.perf_counter()
        response_status = 500
        response_started = False
        context_token = request_id_context.set(request_id)

        async def send_with_headers(message: Message) -> None:
            nonlocal response_status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                response_status = int(message["status"])
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _value in headers}
                for name, value in (
                    (b"x-request-id", request_id.encode("ascii")),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                ):
                    if name not in existing:
                        headers.append((name, value))
                remaining = state.get("rate_limit_remaining")
                limit = state.get("rate_limit_limit")
                if remaining is not None and limit is not None:
                    headers.extend(
                        [
                            (b"x-ratelimit-limit", str(limit).encode("ascii")),
                            (b"x-ratelimit-remaining", str(remaining).encode("ascii")),
                        ]
                    )
                if scope.get("path", "").startswith("/api/") and b"cache-control" not in existing:
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception as exc:
            logger.error(
                json.dumps(
                    {
                        "event": "api_request_failed",
                        "request_id": request_id,
                        "error_type": type(exc).__name__,
                    },
                    separators=(",", ":"),
                )
            )
            if response_started:
                raise
            response = _error_response(
                scope,
                500,
                "internal_server_error",
                "The request could not be completed.",
            )
            await response(scope, receive, send_with_headers)
        finally:
            logger.info(
                json.dumps(
                    {
                        "event": "api_request_complete",
                        "request_id": request_id,
                        "method": scope.get("method", ""),
                        "status_code": response_status,
                        "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
                    },
                    separators=(",", ":"),
                )
            )
            request_id_context.reset(context_token)


class SlidingWindowRateLimiter:
    """Thread-safe, bounded, process-local sliding-window counter."""

    def __init__(self, limit: int, window_seconds: int, max_clients: int = 10_000) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = Lock()

    def consume(self, key: str, now: float | None = None) -> tuple[bool, int, int]:
        current = time.monotonic() if now is None else now
        cutoff = current - self.window_seconds
        with self._lock:
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= self.max_clients:
                    self._events.popitem(last=False)
                events = deque()
                self._events[key] = events
            self._events.move_to_end(key)
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                retry_after = max(1, math.ceil(events[0] + self.window_seconds - current))
                return False, 0, retry_after
            events.append(current)
            return True, self.limit - len(events), 0


class RateLimitMiddleware:
    """Apply a conservative per-client limit to versioned API routes."""

    def __init__(self, app: ASGIApp, limit: int, window_seconds: int) -> None:
        self.app = app
        self.limiter = SlidingWindowRateLimiter(limit, window_seconds)
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope.get("method") == "OPTIONS"
            or not path.startswith("/api/")
            or path == "/api/v1/monitoring/health"
        ):
            await self.app(scope, receive, send)
            return

        forwarded_client = next(
            (
                value.decode("latin-1").strip()
                for name, value in scope.get("headers", [])
                if name.lower() == b"x-agent-client-ip"
            ),
            "",
        )
        try:
            client_key = str(ipaddress.ip_address(forwarded_client))
        except ValueError:
            client = scope.get("client")
            client_key = str(client[0]) if client else "unknown-client"
        allowed, remaining, retry_after = self.limiter.consume(client_key)
        scope.setdefault("state", {})["rate_limit_remaining"] = remaining
        scope["state"]["rate_limit_limit"] = self.limit
        if not allowed:
            response = _error_response(
                scope,
                429,
                "rate_limit_exceeded",
                "Too many requests. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class RequestSizeLimitMiddleware:
    """Bound buffered API request bodies, including requests without a length header."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or not scope.get("path", "").startswith("/api/")
            or scope.get("method") in {"GET", "HEAD", "OPTIONS"}
        ):
            await self.app(scope, receive, send)
            return

        content_length = next(
            (
                value
                for name, value in scope.get("headers", [])
                if name.lower() == b"content-length"
            ),
            None,
        )
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                response = _error_response(
                    scope, 400, "invalid_content_length", "Invalid Content-Length header."
                )
                await response(scope, receive, send)
                return
            if declared_size < 0:
                response = _error_response(
                    scope, 400, "invalid_content_length", "Invalid Content-Length header."
                )
                await response(scope, receive, send)
                return
            if declared_size > self.max_bytes:
                response = _error_response(
                    scope,
                    413,
                    "request_too_large",
                    "Request body exceeds the configured size limit.",
                )
                await response(scope, receive, send)
                return

        messages: list[Message] = []
        received_size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            messages.append(message)
            if message["type"] == "http.request":
                received_size += len(message.get("body", b""))
                if received_size > self.max_bytes:
                    response = _error_response(
                        scope,
                        413,
                        "request_too_large",
                        "Request body exceeds the configured size limit.",
                    )
                    await response(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break

        message_index = 0

        async def replay_receive() -> Message:
            nonlocal message_index
            if message_index < len(messages):
                message = messages[message_index]
                message_index += 1
                return message
            return await receive()

        await self.app(scope, replay_receive, send)
