from __future__ import annotations

import asyncio
import json
import re
from typing import Awaitable, Callable

import websockets

from .errors import BridgeError

# Origens aceitas no handshake WebSocket (CSWSH defense). Páginas dos provedores
# são permitidas porque o content script herda a origem da página (ex.:
# https://gemini.google.com) ao abrir ws://127.0.0.1:8766.
ALLOWED_ORIGINS = [
    None,  # conexões sem Origin (testes locais / SPA)
    re.compile(r"^chrome-extension://"),
    re.compile(
        r"^https://(gemini\.google\.com|claude\.ai|chatgpt\.com|m365\.cloud\.microsoft)$"
    ),
    re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"),
]


def _add_pna_headers(connection, request, response):
    """Private Network Access (Chrome): página pública conectando à rede privada
    exige estes headers no handshake, senão o navegador bloqueia ws://127.0.0.1."""
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    origin = request.headers.get("Origin")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
    return response


class PendingRequest:
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.done: asyncio.Future = asyncio.get_running_loop().create_future()
        self.partials: asyncio.Queue = asyncio.Queue()


class BridgeSession:
    def __init__(self, websocket, provider: str, capabilities: dict) -> None:
        self.websocket = websocket
        self.provider = provider
        self.capabilities = capabilities
        self.pending_ids: set[str] = set()

    @property
    def supports_streaming(self) -> bool:
        return bool(self.capabilities.get("supportsStreaming"))

    @property
    def supports_new_chat(self) -> bool:
        return bool(self.capabilities.get("supportsNewChat"))

    @property
    def supports_transcript(self) -> bool:
        return bool(self.capabilities.get("supportsTranscript"))


class BridgeServer:
    def __init__(
        self,
        host: str,
        port: int,
        on_chat_update: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.sessions: dict[str, BridgeSession] = {}
        self.pending: dict[str, PendingRequest] = {}
        self._sock_to_provider: dict[object, str] = {}
        self._on_chat_update = on_chat_update
        self._server = None

    async def start(self) -> None:
        self._server = await websockets.serve(
            self._handler,
            self.host,
            self.port,
            origins=ALLOWED_ORIGINS,
            process_response=_add_pna_headers,
        )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def get_session(self, provider: str) -> BridgeSession | None:
        return self.sessions.get(provider)

    def is_online(self, provider: str) -> bool:
        return provider in self.sessions

    async def send_prompt(self, request_id: str, session: BridgeSession, payload: dict) -> PendingRequest:
        return await self._send_action("SEND_PROMPT", request_id, session, payload)

    async def read_chat(self, request_id: str, session: BridgeSession, payload: dict) -> PendingRequest:
        return await self._send_action("READ_CHAT", request_id, session, payload)

    async def _send_action(self, action: str, request_id: str, session: BridgeSession, payload: dict) -> PendingRequest:
        message = {
            "version": "1.0",
            "action": action,
            "id": request_id,
            "payload": payload,
        }
        pending = PendingRequest(request_id)
        session.pending_ids.add(request_id)
        self.pending[request_id] = pending
        try:
            await session.websocket.send(json.dumps(message))
        except Exception as exc:
            session.pending_ids.discard(request_id)
            self.pending.pop(request_id, None)
            raise BridgeError(f"failed to send {action}: {exc}") from exc
        return pending

    async def _handler(self, websocket) -> None:
        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                action = msg.get("action")
                if action == "HELLO":
                    self._register(websocket, msg.get("payload") or {})
                elif action == "CHAT_UPDATE":
                    await self._dispatch_chat_update(websocket, msg)
                elif action in ("RESPONSE", "STREAM_UPDATE", "ERROR"):
                    self._dispatch(msg)
        finally:
            self._unregister(websocket)

    def _register(self, websocket, payload: dict) -> None:
        provider = payload.get("provider")
        if not provider:
            return
        payload.setdefault("supportsStreaming", False)
        payload.setdefault("supportsNewChat", True)
        payload.setdefault("supportsTranscript", False)
        old = self.sessions.get(provider)
        if old is not None and old.websocket is not websocket:
            self.sessions.pop(provider, None)
            self._sock_to_provider.pop(old.websocket, None)
            self._fail_pending(old, BridgeError(f"bridge session replaced (provider '{provider}')"))
        self.sessions[provider] = BridgeSession(websocket, provider, payload)
        self._sock_to_provider[websocket] = provider

    async def _dispatch_chat_update(self, websocket, msg: dict) -> None:
        provider = self._sock_to_provider.get(websocket) or (msg.get("payload") or {}).get("provider")
        if not provider or self._on_chat_update is None:
            return
        await self._on_chat_update(provider, msg.get("payload") or {})

    def _dispatch(self, msg: dict) -> None:
        request_id = msg.get("id")
        pending = self.pending.get(request_id)
        if pending is None:
            return
        action = msg.get("action")
        if action == "STREAM_UPDATE":
            partial = (msg.get("payload") or {}).get("partial")
            if partial:
                pending.partials.put_nowait(partial)
        elif action == "RESPONSE":
            self.pending.pop(request_id, None)
            self._drop_session_ref(request_id)
            if not pending.done.done():
                pending.done.set_result(msg.get("payload") or {})
        elif action == "ERROR":
            self.pending.pop(request_id, None)
            self._drop_session_ref(request_id)
            if not pending.done.done():
                pending.done.set_exception(BridgeError(msg.get("error") or "bridge error"))

    def _drop_session_ref(self, request_id: str) -> None:
        for session in self.sessions.values():
            session.pending_ids.discard(request_id)

    def _fail_pending(self, session: BridgeSession, exc: Exception) -> None:
        for request_id in list(session.pending_ids):
            pending = self.pending.pop(request_id, None)
            if pending is not None and not pending.done.done():
                pending.done.set_exception(exc)
        session.pending_ids.clear()

    def _unregister(self, websocket) -> None:
        for provider, session in list(self.sessions.items()):
            if session.websocket is websocket:
                self.sessions.pop(provider, None)
                self._sock_to_provider.pop(websocket, None)
                self._fail_pending(session, BridgeError(f"bridge disconnected (provider '{provider}')"))