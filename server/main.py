from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from .app_client import AppClient, AppRegistrar, build_push_payload
from .bridge import BridgeServer
from .config import Settings
from .errors import OllamaError
from .gateway import Gateway
from .http_api import build_router
from .queue import RequestScheduler
from .registry import ModelRegistry
from .watcher import ChatWatcher


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        watcher = ChatWatcher()
        registry = ModelRegistry(settings.models_file)
        app_registrar = AppRegistrar()
        app_client = AppClient(
            host=settings.app_host,
            port=settings.app_port,
            path=settings.app_path,
            timeout=settings.app_timeout,
        )
        last_pushed: dict[str, str] = {}
        pending_text: dict[str, str] = {}
        pending_timer: dict[str, asyncio.Task] = {}
        background_tasks: set = set()
        settle_seconds = settings.watch_settle

        async def _push_chat_update(provider: str, text: str) -> None:
            profile = next(
                (p for p in registry.list() if p.provider == provider), None
            )
            await app_client.notify(
                app_registrar.current(),
                build_push_payload(
                    request_id=str(uuid.uuid4()),
                    model=profile.name if profile is not None else provider,
                    provider=provider,
                    endpoint="chat",
                    text=text,
                    stream=False,
                ),
            )

        async def _flush_chat_update(provider: str) -> None:
            """Espera o texto estabilizar e relaya a última versão do turno.

            O watcher da extensão emite um ``CHAT_UPDATE`` a cada mudança de DOM
            (a LLM gera de forma incremental), então coalescemos os parciais
            antes de chamar a aplicação — evita executar um comando truncado."""
            try:
                await asyncio.sleep(settle_seconds)
            except asyncio.CancelledError:
                return
            pending_timer.pop(provider, None)
            text = pending_text.pop(provider, None)
            if not text or last_pushed.get(provider) == text:
                return
            last_pushed[provider] = text
            await _push_chat_update(provider, text)

        async def on_chat_update(provider: str, payload: dict) -> None:
            """Relaya à aplicação registrada os turnos do assistente detectados
            pelo watcher (conversa digitada direto na aba Web, que o host não
            iniciou). Sem app registrada, cai no destino padrão (best-effort).

            A entrega roda em task própria (com debounce) para não bloquear o
            loop de recepção do bridge enquanto a app responde."""
            text = await watcher.ingest(provider, payload)
            if not text:
                return
            pending_text[provider] = text
            timer = pending_timer.get(provider)
            if timer is not None and not timer.done():
                timer.cancel()
            task = asyncio.create_task(_flush_chat_update(provider))
            pending_timer[provider] = task
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)

        bridge = BridgeServer(
            settings.host,
            settings.ws_port,
            on_chat_update=on_chat_update,
        )
        scheduler = RequestScheduler(bridge, queue_size=settings.queue_size, timeout=settings.timeout)
        gateway = Gateway(scheduler)

        app.state.settings = settings
        app.state.bridge = bridge
        app.state.scheduler = scheduler
        app.state.registry = registry
        app.state.gateway = gateway
        app.state.watcher = watcher
        app.state.app_registrar = app_registrar
        app.state.app_client = app_client
        app.state.background_tasks = background_tasks

        await bridge.start()
        try:
            yield
        finally:
            await bridge.stop()

    app = FastAPI(title="CapoeiraHost", version="1.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(OllamaError)
    async def ollama_error_handler(request, exc: OllamaError) -> PlainTextResponse:
        return PlainTextResponse(status_code=exc.status_code, content=exc.message)

    app.include_router(build_router())
    return app


def main() -> None:
    settings = Settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.http_port)


if __name__ == "__main__":
    main()