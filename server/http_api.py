from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .errors import BadRequest, BridgeOffline, OllamaError, UnsupportedError
from .gateway import Gateway
from .ollama_dto import (
    VERSION,
    ChatMessageOut,
    ChatRequest,
    ChatResponse,
    CreateRequest,
    CopyRequest,
    DeleteRequest,
    ErrorResponse,
    GenerateRequest,
    GenerateResponse,
    ModelDetails,
    ModelInfo,
    ProcessInfo,
    PsResponse,
    ShowRequest,
    ShowResponse,
    TagsResponse,
)
from .prompt_builder import PROVIDERS
from .registry import Profile


def _require_online(bridge, provider: str) -> None:
    if not bridge.is_online(provider):
        raise BridgeOffline(f"no bridge available for provider '{provider}'")


def _validate_chat(req: ChatRequest) -> None:
    tool_mode = bool(req.tools)
    for msg in req.messages:
        if msg.images:
            raise BadRequest("imagens via API não são suportadas; use a UI Web")
        if not tool_mode and (msg.tool_calls or msg.role == "tool"):
            raise BadRequest(
                "tool calls / role 'tool' exigem a lista 'tools' no request (tool calling simulado)"
            )


def _num(value: str):
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/")
    @router.get("/api/version")
    def version() -> dict:
        return {"version": VERSION}

    # ------------------------------------------------------------------ tags

    @router.get("/api/tags")
    def tags(request: Request) -> TagsResponse:
        registry = request.app.state.registry
        models = [
            ModelInfo(
                name=p.name,
                model=p.name,
                modified_at=p.created_at,
                details=ModelDetails(family=p.provider, families=[p.provider]),
            )
            for p in registry.list()
        ]
        return TagsResponse(models=models)

    # ------------------------------------------------------------------ ps

    @router.get("/api/ps")
    def ps(request: Request) -> PsResponse:
        registry = request.app.state.registry
        bridge = request.app.state.bridge
        scheduler = request.app.state.scheduler
        models = []
        for p in registry.list():
            if not bridge.is_online(p.provider):
                continue
            status = "generating" if scheduler.is_busy(p.provider) else "idle"
            models.append(
                ProcessInfo(
                    name=p.name,
                    model=p.name,
                    details=ModelDetails(family=p.provider, families=[p.provider]),
                    status=status,
                )
            )
        return PsResponse(models=models)

    # ------------------------------------------------------------------ generate

    @router.post("/api/generate", response_model=None)
    async def generate(req: GenerateRequest, request: Request) -> JSONResponse | StreamingResponse:
        if req.images:
            raise BadRequest("envio de imagens via API não é suportado; use a UI Web")

        registry = request.app.state.registry
        bridge = request.app.state.bridge
        gateway: Gateway = request.app.state.gateway
        settings = request.app.state.settings

        profile: Profile = registry.require(req.model)
        _require_online(bridge, profile.provider)
        deadline = time.monotonic() + settings.timeout
        new_chat = settings.new_chat if req.new_chat is None else req.new_chat

        if req.stream:

            async def ndjson():
                try:
                    async for chunk in gateway.generate_chunks(profile, req, deadline, new_chat=new_chat):
                        yield json.dumps(chunk, ensure_ascii=False) + "\n"
                except OllamaError as exc:
                    yield json.dumps(ErrorResponse(error=exc.message).model_dump(), ensure_ascii=False) + "\n"

            return StreamingResponse(ndjson(), media_type="application/x-ndjson")

        text, metrics = await gateway.generate(profile, req, deadline, new_chat=new_chat)
        return GenerateResponse(model=profile.name, created_at=_now_dt(), response=text, **metrics)

    # ------------------------------------------------------------------ chat

    @router.post("/api/chat", response_model=None)
    async def chat(req: ChatRequest, request: Request) -> JSONResponse | StreamingResponse:
        _validate_chat(req)

        registry = request.app.state.registry
        bridge = request.app.state.bridge
        gateway: Gateway = request.app.state.gateway
        settings = request.app.state.settings

        profile: Profile = registry.require(req.model)
        _require_online(bridge, profile.provider)
        deadline = time.monotonic() + settings.timeout
        new_chat = settings.new_chat if req.new_chat is None else req.new_chat

        if req.stream:

            async def ndjson():
                try:
                    async for chunk in gateway.chat_chunks(profile, req, deadline, new_chat=new_chat):
                        yield json.dumps(chunk, ensure_ascii=False) + "\n"
                except OllamaError as exc:
                    yield json.dumps(ErrorResponse(error=exc.message).model_dump(), ensure_ascii=False) + "\n"

            return StreamingResponse(ndjson(), media_type="application/x-ndjson")

        message, metrics = await gateway.chat(profile, req, deadline, new_chat=new_chat)
        return ChatResponse(
            model=profile.name,
            created_at=_now_dt(),
            message=ChatMessageOut(**message),
            **metrics,
        )

    # ------------------------------------------------------------------ show

    @router.post("/api/show")
    def show(req: ShowRequest, request: Request) -> ShowResponse:
        registry = request.app.state.registry
        profile = registry.require(req.model)
        parameters = {k: str(v) for k, v in (profile.options or {}).items()}
        modelfile = [
            f"# CapoeiraHost profile",
            f"display: {profile.display_name or profile.name}",
            f"provider: {profile.provider}",
            f'SYSTEM: "{profile.system_prompt}"',
        ]
        return ShowResponse(
            model=profile.name,
            modelfile="\n".join(modelfile),
            parameters=parameters,
            template=profile.template,
            details=ModelDetails(family=profile.provider, families=[profile.provider]),
            license=f"Termos de uso do provedor '{profile.provider}'",
        )

    # ------------------------------------------------------------------ create / copy / delete

    @router.post("/api/create")
    def create(req: CreateRequest, request: Request) -> dict:
        registry = request.app.state.registry
        source = registry.get(req.from_) if req.from_ else None
        if source is not None:
            provider = source.provider
            base_options = dict(source.options)
        elif req.from_ in PROVIDERS:
            provider = req.from_
            base_options = {}
        elif req.from_:
            raise BadRequest(f"from '{req.from_}' não é um perfil nem provedor conhecido")
        else:
            provider = registry.default_provider
            base_options = {}

        if req.parameters:
            base_options = {**base_options, **{k: _num(v) for k, v in req.parameters.items()}}

        registry.create(
            Profile(
                name=req.model,
                provider=provider,
                system_prompt=req.system or "",
                template=req.template,
                options=base_options,
            )
        )
        return {"status": "success"}

    @router.post("/api/copy")
    def copy(req: CopyRequest, request: Request) -> dict:
        request.app.state.registry.copy(req.source, req.destination)
        return {}

    @router.delete("/api/delete")
    def delete(req: DeleteRequest, request: Request) -> dict:
        request.app.state.registry.delete(req.model)
        return {}

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