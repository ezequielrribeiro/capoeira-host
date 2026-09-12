from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from .errors import BadRequest, BridgeOffline, UnsupportedError
from .gateway import Gateway
from .ollama_dto import VERSION, ChatMessage, ChatRequest, GenerateRequest
from .prompt_builder import PROVIDERS
from .registry import Profile

ALLOWED_CHAT_ROLES = ("user", "assistant", "system", "tool")


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _num(value: str):
    try:
        return float(value) if ("." in value or "e" in value.lower()) else int(value)
    except ValueError:
        return value


def _parse_options(form) -> dict:
    options = {}
    for key, value in form.multi_items():
        if key.startswith("option."):
            options[key[len("option."):]] = _num(value)
    return options


def _require_online(bridge, provider: str) -> None:
    if not bridge.is_online(provider):
        raise BridgeOffline(f"no bridge available for provider '{provider}'")


def _parse_messages(form) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    current: ChatMessage | None = None
    for key, value in form.multi_items():
        if key == "role":
            current = ChatMessage(role=value)
            messages.append(current)
        elif key == "content" and current is not None:
            current.content = value
        elif key == "tool_call_id" and current is not None:
            current.tool_call_id = value
    return [m for m in messages if m.role in ALLOWED_CHAT_ROLES]


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/")
    @router.get("/api/version")
    def version() -> PlainTextResponse:
        return PlainTextResponse(VERSION)

    # ------------------------------------------------------------------ tags

    @router.get("/api/tags")
    def tags(request: Request) -> PlainTextResponse:
        registry = request.app.state.registry
        lines = []
        for p in registry.list():
            streaming = "true" if p.streaming else "false"
            modified = p.created_at.isoformat().replace("+00:00", "Z")
            lines.append(f"{p.name} | provider={p.provider} | streaming={streaming} | modified_at={modified}")
        return PlainTextResponse("\n".join(lines))

    # ------------------------------------------------------------------ ps

    @router.get("/api/ps")
    def ps(request: Request) -> PlainTextResponse:
        registry = request.app.state.registry
        bridge = request.app.state.bridge
        scheduler = request.app.state.scheduler
        lines = []
        for p in registry.list():
            if not bridge.is_online(p.provider):
                continue
            status = "generating" if scheduler.is_busy(p.provider) else "idle"
            lines.append(f"{p.name} | provider={p.provider} | status={status}")
        return PlainTextResponse("\n".join(lines))

    # ------------------------------------------------------------------ generate

    @router.post("/api/generate", response_model=None)
    async def generate(request: Request) -> PlainTextResponse | StreamingResponse:
        form = await request.form()
        model = (form.get("model") or "").strip()
        prompt = (form.get("prompt") or "").strip()
        if not model:
            raise BadRequest("campo 'model' é obrigatório")
        if not prompt:
            raise BadRequest("campo 'prompt' é obrigatório")

        registry = request.app.state.registry
        bridge = request.app.state.bridge
        gateway: Gateway = request.app.state.gateway
        settings = request.app.state.settings

        profile: Profile = registry.require(model)
        _require_online(bridge, profile.provider)
        deadline = time.monotonic() + settings.timeout
        new_chat = settings.new_chat if form.get("new_chat") is None else _bool(form.get("new_chat"))
        req = GenerateRequest(
            model=model,
            prompt=prompt,
            system=(form.get("system") or "").strip() or None,
            template=form.get("template") or None,
            stream=_bool(form.get("stream")),
            new_chat=_bool(form.get("new_chat")) if form.get("new_chat") is not None else None,
            options=_parse_options(form),
        )

        if req.stream:
            return StreamingResponse(
                gateway.generate_text(profile, req, deadline, new_chat=new_chat),
                media_type="text/plain; charset=utf-8",
            )
        return PlainTextResponse(await gateway.generate(profile, req, deadline, new_chat=new_chat))

    # ------------------------------------------------------------------ chat

    @router.post("/api/chat", response_model=None)
    async def chat(request: Request) -> PlainTextResponse | StreamingResponse:
        form = await request.form()
        model = (form.get("model") or "").strip()
        if not model:
            raise BadRequest("campo 'model' é obrigatório")

        registry = request.app.state.registry
        bridge = request.app.state.bridge
        gateway: Gateway = request.app.state.gateway
        settings = request.app.state.settings

        profile: Profile = registry.require(model)

        messages = _parse_messages(form)
        if not messages:
            raise BadRequest("campo 'messages' vazio: envie pares role/content")
        tools = (form.get("tools") or "").strip() or None
        tool_mode = bool(tools)
        for m in messages:
            if not tool_mode and m.role == "tool":
                raise BadRequest(
                    "role 'tool' exige o campo 'tools' no request (tool calling simulado)"
                )

        _require_online(bridge, profile.provider)

        deadline = time.monotonic() + settings.timeout
        new_chat = settings.new_chat if form.get("new_chat") is None else _bool(form.get("new_chat"))
        req = ChatRequest(
            model=model,
            messages=messages,
            tools=tools,
            stream=_bool(form.get("stream")),
            new_chat=_bool(form.get("new_chat")) if form.get("new_chat") is not None else None,
            options=_parse_options(form),
        )

        if req.stream:
            return StreamingResponse(
                gateway.chat_text(profile, req, deadline, new_chat=new_chat),
                media_type="text/plain; charset=utf-8",
            )
        return PlainTextResponse(await gateway.chat(profile, req, deadline, new_chat=new_chat))

    # ------------------------------------------------------------------ show

    @router.post("/api/show")
    async def show(request: Request) -> PlainTextResponse:
        form = await request.form()
        model = (form.get("model") or "").strip()
        if not model:
            raise BadRequest("campo 'model' é obrigatório")
        profile = request.app.state.registry.require(model)
        options = "; ".join(f"{k}={v}" for k, v in (profile.options or {}).items())
        lines = [
            f"model: {profile.name}",
            f"display: {profile.display_name or profile.name}",
            f"provider: {profile.provider}",
            f"streaming: {'true' if profile.streaming else 'false'}",
        ]
        if profile.template:
            lines.append(f"template: {profile.template}")
        if options:
            lines.append(f"options: {options}")
        return PlainTextResponse("\n".join(lines))

    # ------------------------------------------------------------------ create / copy / delete

    @router.post("/api/create")
    async def create(request: Request) -> PlainTextResponse:
        form = await request.form()
        model = (form.get("model") or "").strip()
        if not model:
            raise BadRequest("campo 'model' é obrigatório")
        registry = request.app.state.registry

        source = registry.get(form.get("from")) if form.get("from") else None
        if source is not None:
            provider = source.provider
            base_options = dict(source.options)
        elif form.get("from") in PROVIDERS:
            provider = form.get("from")
            base_options = {}
        elif form.get("from"):
            raise BadRequest(f"from '{form.get('from')}' não é um perfil nem provedor conhecido")
        else:
            provider = registry.default_provider
            base_options = {}
        for key, value in form.multi_items():
            if key.startswith("parameter."):
                base_options[key[len("parameter."):]] = _num(value)

        registry.create(
            Profile(
                name=model,
                provider=provider,
                system_prompt=(form.get("system") or "").strip(),
                template=form.get("template") or None,
                options=base_options,
            )
        )
        return PlainTextResponse("ok")

    @router.post("/api/copy")
    async def copy(request: Request) -> PlainTextResponse:
        form = await request.form()
        source = (form.get("source") or "").strip()
        destination = (form.get("destination") or "").strip()
        if not source or not destination:
            raise BadRequest("campos 'source' e 'destination' são obrigatórios")
        request.app.state.registry.copy(source, destination)
        return PlainTextResponse("ok")

    @router.delete("/api/delete")
    async def delete(request: Request) -> PlainTextResponse:
        form = await request.form()
        model = (form.get("model") or "").strip()
        if not model:
            raise BadRequest("campo 'model' é obrigatório")
        request.app.state.registry.delete(model)
        return PlainTextResponse("ok")

    # ------------------------------------------------------------------ não aplicáveis

    @router.post("/api/pull")
    def pull() -> None:
        raise UnsupportedError("pull não é suportado: modelos são acessados via Browser Bridge")

    @router.post("/api/push")
    def push() -> None:
        raise UnsupportedError("push não é suportado")

    @router.api_route("/api/blobs/{digest}", methods=["GET", "HEAD", "POST"])
    def blob(digest: str) -> None:
        raise UnsupportedError("blobs não são usados no CapoeiraHost")

    @router.post("/api/embed")
    @router.post("/api/embeddings")
    def embeddings() -> None:
        raise UnsupportedError("embeddings estão fora de escopo no CapoeiraHost")

    return router