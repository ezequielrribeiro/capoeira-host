from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

logger = logging.getLogger("capoeirahost.app")


@dataclass(frozen=True)
class RegisteredApp:
    name: str = ""
    host: str = "127.0.0.1"
    port: int = 0


class AppRegistrar:
    """Slot único de aplicação consumidora das respostas do LLM.

    Enquanto uma aplicação estiver registrada, o CapoeiraHost entrega a resposta
    do LLM somente para ela (host/porta do registro). Re-registro substitui o
    anterior; ``unregister`` reestabelece o destino para a porta padrão.
    """

    def __init__(self) -> None:
        self._app: RegisteredApp | None = None

    def register(self, port: int, host: str = "127.0.0.1", name: str = "") -> RegisteredApp:
        app = RegisteredApp(name=name or "", host=host or "127.0.0.1", port=port)
        self._app = app
        return app

    def unregister(self) -> None:
        self._app = None

    def current(self) -> RegisteredApp | None:
        return self._app


class AppClient:
    """Entrega best-effort da resposta do LLM à API da aplicação (POST JSON).

    O contrato: ``POST http://<host>:<port><path>`` com corpo JSON contendo
    ``request_id``, ``model``, ``provider``, ``endpoint``, ``stream``, ``text`` e
    ``timestamp`` (e ``error`` em falha de geração). Qualquer falha (timeout,
    recusa, porta fechada) é registrada em log e ignorada — nunca afeta o
    chamador e nunca faz retry.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8767,
        path: str = "/api/capoeira/response",
        timeout: float = 5.0,
    ) -> None:
        self.default_host = host
        self.default_port = port
        self.path = path
        self.timeout = timeout

    def target(self, app: RegisteredApp | None) -> tuple[str, int]:
        if app is not None:
            return app.host, app.port
        return self.default_host, self.default_port

    async def notify(
        self,
        app: RegisteredApp | None,
        payload: dict[str, Any],
    ) -> None:
        host, port = self.target(app)
        url = f"http://{host}:{port}{self.path}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._send, req)
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.warning("push à aplicação falhou (%s): %s", url, exc)

    def _send(self, req: urllib_request.Request) -> None:
        try:
            with urllib_request.urlopen(req, timeout=self.timeout) as resp:
                resp.read()
                if not 200 <= resp.status < 300:
                    raise RuntimeError(f"HTTP {resp.status}")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(str(exc)) from exc