from __future__ import annotations

import asyncio
import re
import time
from typing import AsyncGenerator

from .errors import BridgeError, BridgeTimeout
from .ollama_dto import ChatRequest, GenerateRequest
from .prompt_builder import apply_template, build_chat_transcript, build_prompt_payload, build_system_envelope
from .queue import RequestScheduler
from .registry import Profile


_TOOL_CALL_LINE = re.compile(r"(?m)^\s*\[TOOL_CALL\]\s+([^\s|]+)((?:\s*\|\s*[^=]+=[^|\r\n]+)*)\s*$")
_TOOL_CALL_ANY_LINE = re.compile(r"(?m)^\s*\[TOOL_CALL\].*$")


def _parse_tool_call_lines(raw: str) -> list[str] | None:
    """Extrai as linhas [TOOL_CALL] válidas telegrafadas pelo modelo Web.

    O contrato é textual (pipes '|' separando chave=valor), tolerante a prosa ao
    redor e a code fences (o innerText ressurge sem backticks). Uma linha é válida
    se tiver o marcador [TOOL_CALL] e o nome da ferramenta (| args opcionais).
    Retorna as linhas originais, ou None se não houver linha válida."""
    matched = [m.group(0).strip() for m in _TOOL_CALL_LINE.finditer(raw or "")]
    return matched or None


def _strip_tool_call_lines(raw: str) -> str:
    """Remove as linhas [TOOL_CALL] do texto (válidas ou não), deixando só a prosa."""
    return _TOOL_CALL_ANY_LINE.sub("", raw or "").strip()


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
        options = req.options or {}
        system = build_system_envelope(req.system or profile.system_prompt, options)
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
        tool_mode = bool(req.tools and req.tools.strip())
        system = build_system_envelope(profile.system_prompt, req.options or {}, tools=req.tools)
        transcript = build_chat_transcript(req.messages)
        prompt = apply_template(profile.template, system, transcript)
        payload = build_prompt_payload(profile, system, prompt, new_chat=new_chat)
        transmitted = ""
        async for kind, text, done_payload in self._run(profile, payload, deadline):
            if kind == "text":
                if tool_mode:
                    continue
                transmitted += text
                yield text
            else:
                full = (done_payload or {}).get("rawResponse") or ""
                if tool_mode:
                    calls = _parse_tool_call_lines(full)
                    if calls:
                        yield "\n".join(calls)
                        return
                    yield _strip_tool_call_lines(full)
                    return
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