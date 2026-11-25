"""HTTP layer: OpenAI-compatible endpoints, Bearer authentication, streaming (SSE)."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from typing import Any

from aiohttp import web

from api.backend import ApiBackend, ApiError, Completion
from api.settings import ApiSettings
from bot.persona import PERSONAS, DEFAULT_PERSONA_KEY

logger = logging.getLogger(__name__)

ROLES = {"system", "user", "assistant"}
BACKEND_KEY = web.AppKey("backend", ApiBackend)
SETTINGS_KEY = web.AppKey("settings", ApiSettings)


def error_response(status: int, message: str, error_type: str) -> web.Response:
    body = {"error": {"message": message, "type": error_type, "code": None}}
    return web.json_response(body, status=status)


@web.middleware
async def auth_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    if request.path == "/health":
        return await handler(request)
    expected = request.app[SETTINGS_KEY].api_key.encode("utf-8")
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme != "Bearer" or not hmac.compare_digest(token.strip().encode("utf-8"), expected):
        return error_response(401, "invalid or missing API key", "authentication_error")
    return await handler(request)


@web.middleware
async def error_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    try:
        return await handler(request)
    except ApiError as exc:
        return error_response(exc.status, str(exc), exc.error_type)


def message_text(message: dict[str, Any]) -> str:
    """Return the text of one chat message. Content may be a string or a list of text parts."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
        return "".join(parts)
    raise ApiError(400, "message content must be a string or a list of text parts")


def build_prompt(messages: list[dict[str, Any]], continuing: bool) -> str:
    """Return the text to send. A continued conversation keeps its own history in kiro-cli."""
    system = [message_text(m) for m in messages if m["role"] == "system"]
    history = [m for m in messages if m["role"] != "system"]
    if not history or history[-1]["role"] != "user":
        raise ApiError(400, "the last message must have role user")
    last = message_text(history[-1])
    if continuing or len(history) == 1:
        earlier: list[str] = []
    else:
        earlier = [f"{'Пользователь' if m['role'] == 'user' else 'Ассистент'}: {message_text(m)}" for m in history[:-1]]
    parts: list[str] = []
    if system:
        parts.append("\n".join(system))
    if earlier:
        parts.append("Предыдущий диалог:\n" + "\n".join(earlier))
    parts.append(last)
    return "\n\n".join(parts)


def parse_request(body: Any, settings: ApiSettings) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ApiError(400, "request body must be a JSON object")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ApiError(400, "messages must be a non-empty list")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ROLES:
            raise ApiError(400, "each message needs a role: system, user or assistant")
    model = body.get("model", settings.default_model)
    if not isinstance(model, str) or not model:
        raise ApiError(400, "model must be a non-empty string")
    persona_key = body.get("persona", DEFAULT_PERSONA_KEY)
    if persona_key not in PERSONAS:
        raise ApiError(400, f"unknown persona; use one of: {', '.join(PERSONAS)}")
    conversation_id = body.get("conversation_id")
    if conversation_id is not None and not isinstance(conversation_id, str):
        raise ApiError(400, "conversation_id must be a string")
    stream = body.get("stream", False)
    if not isinstance(stream, bool):
        raise ApiError(400, "stream must be true or false")
    return {
        "messages": messages,
        "model": model,
        "persona": PERSONAS[persona_key],
        "conversation_id": conversation_id,
        "stream": stream,
    }


def usage_body(completion: Completion) -> dict[str, float | None]:
    return {"credits": completion.credits, "context_usage": completion.context_usage}


def completion_body(request_id: str, created: int, model: str, completion: Completion) -> dict[str, Any]:
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "conversation_id": completion.conversation_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": completion.text},
                "finish_reason": "stop",
            }
        ],
        "usage": usage_body(completion),
    }


def chunk_body(
    request_id: str,
    created: int,
    model: str,
    conversation_id: str | None,
    delta: dict[str, Any],
    finish: str | None,
    usage: dict[str, float | None] | None = None,
) -> str:
    body: dict[str, Any] = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "conversation_id": conversation_id,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage is not None:
        body["usage"] = usage
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def list_models(request: web.Request) -> web.Response:
    backend: ApiBackend = request.app[BACKEND_KEY]
    data = [{"id": m, "object": "model", "created": 0, "owned_by": "kiro"} for m in backend.models]
    return web.json_response({"object": "list", "data": data})


async def chat_completions(request: web.Request) -> web.StreamResponse:
    settings: ApiSettings = request.app[SETTINGS_KEY]
    backend: ApiBackend = request.app[BACKEND_KEY]
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise ApiError(400, "request body is not valid JSON") from exc
    params = parse_request(body, settings)
    continuing = params["conversation_id"] is not None
    prompt = build_prompt(params["messages"], continuing)
    request_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    model = params["model"]
    logger.info("chat request model=%s persona=%s stream=%s", model, params["persona"].key, params["stream"])
    run = dict(
        model=model,
        persona=params["persona"],
        conversation_id=params["conversation_id"],
        prompt=prompt,
    )
    if not params["stream"]:
        completion = await backend.complete(**run)
        return web.json_response(completion_body(request_id, created, model, completion))
    return await stream_completion(request, backend, run, request_id, created)


async def stream_completion(
    request: web.Request,
    backend: ApiBackend,
    run: dict[str, Any],
    request_id: str,
    created: int,
) -> web.StreamResponse:
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    task = asyncio.create_task(backend.complete(**run, on_text=queue.put_nowait))
    task.add_done_callback(lambda _: queue.put_nowait(None))
    model = run["model"]
    response: web.StreamResponse | None = None
    streamed = False
    try:
        first = await queue.get()
        if first is None:
            completion = await task  # errors here are still plain JSON errors
            response = await open_stream(request)
            conversation_id = completion.conversation_id
            if completion.text:
                await response.write(
                    chunk_body(request_id, created, model, conversation_id, {"role": "assistant", "content": completion.text}, None).encode("utf-8")
                )
        else:
            response = await open_stream(request)
            conversation_id = None
            await response.write(
                chunk_body(request_id, created, model, None, {"role": "assistant", "content": first}, None).encode("utf-8")
            )
            streamed = True
            while (item := await queue.get()) is not None:
                await response.write(chunk_body(request_id, created, model, None, {"content": item}, None).encode("utf-8"))
            completion = await task
            conversation_id = completion.conversation_id
        final = chunk_body(request_id, created, model, conversation_id, {}, "stop", usage_body(completion))
        await response.write(final.encode("utf-8"))
    except ApiError as exc:
        if response is None:
            raise
        await response.write(f"data: {json.dumps({'error': {'message': str(exc), 'type': exc.error_type}})}\n\n".encode("utf-8"))
    await response.write(b"data: [DONE]\n\n")
    await response.write_eof()
    return response


async def open_stream(request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse(
        status=200,
        headers={"Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache"},
    )
    await response.prepare(request)
    return response


async def on_startup(app: web.Application) -> None:
    backend: ApiBackend = app[BACKEND_KEY]
    await backend.start()


async def on_cleanup(app: web.Application) -> None:
    await app[BACKEND_KEY].close()


def create_app(settings: ApiSettings) -> web.Application:
    backend = ApiBackend(
        kiro_bin=settings.kiro_bin,
        env=dict(settings.kiro_env),
        acp_home=settings.acp_home,
        workdir=settings.workdir,
        max_processes=settings.max_processes,
        timeout=settings.timeout,
    )
    app = web.Application(middlewares=[error_middleware, auth_middleware])
    app[SETTINGS_KEY] = settings
    app[BACKEND_KEY] = backend
    app.router.add_get("/health", health)
    app.router.add_get("/v1/models", list_models)
    app.router.add_post("/v1/chat/completions", chat_completions)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app
