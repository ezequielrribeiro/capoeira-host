from __future__ import annotations

import asyncio
import time
import uuid
from typing import AsyncGenerator

from .errors import BridgeError, BridgeOffline, BridgeTimeout, UnsupportedError
from .ollama_dto import ChatRequest, GenerateRequest
from .prompt_builder import apply_template, build_chat_transcript, build_prompt_payload, build_system_envelope
from .queue import RequestScheduler
from .registry import Profile


class Gateway:
    def __init__(self, scheduler: RequestScheduler) -> None:
        self._scheduler = scheduler

    async def _run(self, profile: Profile, payload: dict, deadline: float) -> AsyncGenerator:
        job = self._scheduler.submit(profile.provider, payload)
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise BridgeTimeout("bridge timeout after the configured window")
            try:
                event = await asyncio.wait_for(job.events.get(), timeout=min(0.1, remain))
            except asyncio.TimeoutError:
                continue
            kind = event["type"]
            if kind == "text":
                yield "text", event["text"], None
            elif kind == "error":
                raise BridgeError(event["message"])
            elif kind == "done":
                yield "done", "", event["payload"]
                return

    # ------------------------------------------------------------------ generate

    async def generate_text(self, profile: Profile, req: GenerateRequest, deadline: float, new_chat: bool = True) -> AsyncGenerator[str, None]:
        system = build_system_envelope(req.system or profile.system_prompt)
        prompt = apply_template(req.template or profile.template, system, req.prompt)
        payload = build_prompt_payload(profile, system, prompt, new_chat=new_chat)
        transmitted = ""
        async for kind, text, done_payload in self._run(profile, payload, deadline):
            if kind == "text":
                transmitted += text
                yield text
            else:
                full = (done_payload or {}).get("rawResponse") or ""
                delta = full[len(transmitted):]
                if delta:
                    transmitted += delta
                    yield delta
                return

    async def generate(self, profile: Profile, req: GenerateRequest, deadline: float, new_chat: bool = True) -> str:
        chunks: list[str] = []
        async for piece in self.generate_text(profile, req, deadline, new_chat=new_chat):
            chunks.append(piece)
        return "".join(chunks)

    # ------------------------------------------------------------------ chat

    async def chat_text(self, profile: Profile, req: ChatRequest, deadline: float, new_chat: bool = True) -> AsyncGenerator[str, None]:
        system = build_system_envelope(profile.system_prompt)
        transcript = build_chat_transcript(req.messages)
        prompt = apply_template(profile.template, system, transcript)
        payload = build_prompt_payload(profile, system, prompt, new_chat=new_chat)
        transmitted = ""
        async for kind, text, done_payload in self._run(profile, payload, deadline):
            if kind == "text":
                transmitted += text
                yield text
            else:
                full = (done_payload or {}).get("rawResponse") or ""
                delta = full[len(transmitted):]
                if delta:
                    transmitted += delta
                    yield delta
                return

    async def chat(self, profile: Profile, req: ChatRequest, deadline: float, new_chat: bool = True) -> str:
        chunks: list[str] = []
        async for piece in self.chat_text(profile, req, deadline, new_chat=new_chat):
            chunks.append(piece)
        return "".join(chunks)

    # ------------------------------------------------------------------ read chat

    async def read_chat(self, profile: Profile, deadline: float) -> list[dict]:
        """Busca o transcript atual da aba Web (READ_CHAT no bridge).

        Fica fora da fila FIFO de geração: é uma leitura pontual do DOM."""
        session = self._scheduler.bridge.get_session(profile.provider)
        if session is None:
            raise BridgeOffline(f"no bridge available for provider '{profile.provider}'")
        if not session.supports_transcript:
            raise UnsupportedError(f"no transcript support for provider '{profile.provider}'")

        request_id = str(uuid.uuid4())
        pending = await self._scheduler.bridge.read_chat(
            request_id, session, {"provider": profile.provider}
        )
        try:
            result = await asyncio.wait_for(
                pending.done, timeout=max(0.0, deadline - time.monotonic())
            )
        except asyncio.TimeoutError:
            self._scheduler.bridge.pending.pop(request_id, None)
            session.pending_ids.discard(request_id)
            raise BridgeTimeout("bridge timeout after the configured window") from None
        return (result or {}).get("transcript") or []