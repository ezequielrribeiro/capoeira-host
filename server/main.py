from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from .bridge import BridgeServer
from .config import Settings
from .errors import OllamaError
from .gateway import Gateway
from .http_api import build_router
from .queue import RequestScheduler
from .registry import ModelRegistry


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bridge = BridgeServer(settings.host, settings.ws_port)
        scheduler = RequestScheduler(bridge, queue_size=settings.queue_size, timeout=settings.timeout)
        registry = ModelRegistry(settings.models_file)
        gateway = Gateway(scheduler)

        app.state.settings = settings
        app.state.bridge = bridge
        app.state.scheduler = scheduler
        app.state.registry = registry
        app.state.gateway = gateway

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