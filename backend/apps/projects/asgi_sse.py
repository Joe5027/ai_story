"""Async ASGI adapter for project SSE endpoints.

Django 3.2 iterates ``StreamingHttpResponse`` synchronously inside its ASGI
handler. A long-lived Redis subscriber would therefore block the event loop
before Daphne can flush EventSource chunks. This adapter keeps the existing
Django views for WSGI use and handles only the two SSE paths asynchronously.
"""

import asyncio
import contextlib
import json
import logging
import re
import time
from typing import Optional, Tuple

from core.redis.subscriber import RedisStreamSubscriber

from .sse_views import (
    ALL_STAGES_IDLE_TIMEOUT_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    MESSAGE_POLL_TIMEOUT_SECONDS,
    SINGLE_STAGE_IDLE_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

SINGLE_STAGE_PATH = re.compile(
    r"^/api/v1/projects/sse/projects/(?P<project_id>[^/]+)/stages/(?P<stage_name>[^/]+)/$"
)
ALL_STAGES_PATH = re.compile(
    r"^/api/v1/projects/sse/projects/(?P<project_id>[^/]+)/$"
)

SSE_HEADERS = [
    (b"content-type", b"text/event-stream; charset=utf-8"),
    (b"cache-control", b"no-cache, no-transform"),
    (b"x-accel-buffering", b"no"),
    (b"access-control-allow-origin", b"*"),
    (b"access-control-allow-methods", b"GET"),
    (b"access-control-allow-headers", b"Content-Type"),
]


def match_sse_path(path: str) -> Optional[Tuple[str, Optional[str]]]:
    """Return ``(project_id, stage_name)`` for a supported SSE path."""
    match = SINGLE_STAGE_PATH.match(path)
    if match:
        return match.group("project_id"), match.group("stage_name")

    match = ALL_STAGES_PATH.match(path)
    if match:
        return match.group("project_id"), None

    return None


def format_sse_message(data: dict) -> bytes:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


class ProjectSSEASGIApplication:
    """Route project SSE requests to a non-blocking ASGI stream."""

    def __init__(self, django_application):
        self.django_application = django_application

    async def __call__(self, scope, receive, send):
        route = match_sse_path(scope.get("path", ""))
        if scope.get("type") != "http" or scope.get("method") != "GET" or route is None:
            await self.django_application(scope, receive, send)
            return

        project_id, stage_name = route
        await self._stream(project_id, stage_name, receive, send)

    async def _stream(self, project_id, stage_name, receive, send):
        all_stages_mode = stage_name is None
        idle_timeout = (
            ALL_STAGES_IDLE_TIMEOUT_SECONDS
            if all_stages_mode
            else SINGLE_STAGE_IDLE_TIMEOUT_SECONDS
        )
        subscriber = None
        receive_task = None
        disconnected = False

        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": SSE_HEADERS,
        })

        try:
            subscriber = RedisStreamSubscriber(project_id, stage_name)
            connected = {
                "type": "connected",
                "project_id": project_id,
                "message": (
                    "SSE连接已建立(所有阶段)"
                    if all_stages_mode
                    else "SSE连接已建立"
                ),
            }
            if stage_name is not None:
                connected["stage"] = stage_name

            await send({
                "type": "http.response.body",
                "body": format_sse_message(connected),
                "more_body": True,
            })

            start_time = time.monotonic()
            last_activity_at = start_time
            last_heartbeat_at = start_time
            receive_task = asyncio.create_task(receive())

            while True:
                message = await asyncio.to_thread(
                    subscriber.get_message,
                    MESSAGE_POLL_TIMEOUT_SECONDS,
                )
                now = time.monotonic()

                while receive_task.done():
                    event = receive_task.result()
                    if event.get("type") == "http.disconnect":
                        disconnected = True
                        break
                    receive_task = asyncio.create_task(receive())
                if disconnected:
                    break

                if message:
                    last_activity_at = now
                    await send({
                        "type": "http.response.body",
                        "body": format_sse_message(message),
                        "more_body": True,
                    })
                    terminal_types = (
                        ("pipeline_done", "pipeline_error")
                        if all_stages_mode
                        else ("done", "error")
                    )
                    if message.get("type") in terminal_types:
                        break
                    continue

                if now - last_heartbeat_at >= HEARTBEAT_INTERVAL_SECONDS:
                    last_heartbeat_at = now
                    await send({
                        "type": "http.response.body",
                        "body": b": heartbeat\n\n",
                        "more_body": True,
                    })

                if now - last_activity_at >= idle_timeout:
                    logger.info(
                        "ASGI SSE idle timeout: project_id=%s, stage_name=%s",
                        project_id,
                        stage_name,
                    )
                    break

        except asyncio.CancelledError:
            disconnected = True
            raise
        except (BrokenPipeError, ConnectionResetError):
            disconnected = True
        except Exception as exc:
            logger.exception(
                "ASGI SSE stream failed: project_id=%s, stage_name=%s",
                project_id,
                stage_name,
            )
            if not disconnected:
                await send({
                    "type": "http.response.body",
                    "body": format_sse_message({
                        "type": "error",
                        "error": f"SSE流异常: {exc}",
                        "project_id": project_id,
                    }),
                    "more_body": True,
                })
        finally:
            if receive_task is not None:
                receive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await receive_task
            if subscriber is not None:
                await asyncio.to_thread(subscriber.close)
            if not disconnected:
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    await send({
                        "type": "http.response.body",
                        "body": b"",
                        "more_body": False,
                    })
