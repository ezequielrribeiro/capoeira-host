from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import AsyncGenerator

from .errors import BridgeError, BridgeTimeout
from .ollama_dto import ChatRequest, GenerateRequest
from .prompt_builder import apply_template, build_chat_transcript, build_prompt_payload, build_system_envelope
from .queue import RequestScheduler
from .registry import Profile


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_tool_call(raw: str) -> list[dict] | None:
    """Tenta extrair uma tool call do texto cru vindo da Web. Retorna a lista
    de tool_calls (formato Ollama) se o texto for um JSON de contrato válido."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("`").strip()
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if "name" in payload and "arguments" in payload:
        return [{"function": {"name": payload["name"], "arguments": payload["arguments"]}}]
    return None


def _extract_text(raw: str) -> str | None:
    """Se a resposta for o contrato {'text': ...}, devolve só o texto; senão None."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("`").strip()
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(payload, dict) and "text" in payload:
        return str(payload["text"])
    return None


def _metrics(execution_ms: float, text: str) -> dict:
    duration_ns = int(execution_ms * 1_000_000)
    return {
        "total_duration": duration_ns,
        "load_duration": 0,
        "prompt_eval_count": 0,
        "prompt_eval_duration": 0,
        "eval_count": len(text),
        "eval_duration": duration_ns,
    }


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

    async def generate_chunks(self, profile: Profile, req: GenerateRequest, deadline: float, new_chat: bool = True) -> AsyncGenerator:
        options = req.options.model_dump() if req.options else {}
        system = build_system_envelope(
            req.system or profile.system_prompt,
            options,
            json_mode=(req.format == "json"),
        )
        prompt = apply_template(req.template or profile.template, system, req.prompt)
        payload = build_prompt_payload(
            profile,
            system,
            prompt,
            json_mode=(req.format == "json"),
            options=options,
            new_chat=new_chat,
        )
        transmitted = ""
        exec_ms = 0.0
        async for kind, text, done_payload in self._run(profile, payload, deadline):
            if kind == "text":
                transmitted += text
                yield {"model": profile.name, "created_at": _now(), "response": text, "done": False}
            else:
                exec_ms = float((done_payload or {}).get("executionTimeMs") or 0)
                full = (done_payload or {}).get("rawResponse") or ""
                delta = full[len(transmitted):]
                if delta:
                    transmitted += delta
                    yield {"model": profile.name, "created_at": _now(), "response": delta, "done": False}
                yield {
                    "model": profile.name,
                    "created_at": _now(),
                    "response": "",
                    "done": True,
                    "done_reason": "stop",
                    "context": [],
                    **_metrics(exec_ms, transmitted or full),
                }
                return

    async def generate(self, profile: Profile, req: GenerateRequest, deadline: float, new_chat: bool = True) -> tuple[str, dict]:
        text = ""
        metrics: dict = {}
        async for chunk in self.generate_chunks(profile, req, deadline, new_chat=new_chat):
            if chunk.get("done"):
                metrics = _pick_metrics(chunk)
            else:
                text += chunk.get("response") or ""
        return text, metrics

    # ------------------------------------------------------------------ chat

    async def chat_chunks(self, profile: Profile, req: ChatRequest, deadline: float, new_chat: bool = True) -> AsyncGenerator:
        options = req.options.model_dump() if req.options else {}
        tool_mode = bool(req.tools)
        json_mode = tool_mode or (req.format == "json")
        system = build_system_envelope(profile.system_prompt, options, json_mode=json_mode, tools=req.tools)
        transcript = build_chat_transcript(req.messages)
        prompt = apply_template(profile.template, system, transcript)
        conversation = [m.model_dump(exclude_none=True) for m in req.messages]
        payload = build_prompt_payload(
            profile,
            system,
            prompt,
            json_mode=json_mode,
            options=options,
            conversation=conversation,
            new_chat=new_chat,
        )
        transmitted = ""
        exec_ms = 0.0
        async for kind, text, done_payload in self._run(profile, payload, deadline):
            if kind == "text":
                if tool_mode:
                    continue
                transmitted += text
                yield {"model": profile.name, "created_at": _now(), "message": {"role": "assistant", "content": text}, "done": False}
            else:
                exec_ms = float((done_payload or {}).get("executionTimeMs") or 0)
                full = (done_payload or {}).get("rawResponse") or ""
                if tool_mode:
                    tool_calls = _parse_tool_call(full)
                    if tool_calls:
                        yield {
                            "model": profile.name,
                            "created_at": _now(),
                            "message": {"role": "assistant", "content": "", "tool_calls": tool_calls},
                            "done": True,
                            "done_reason": "stop",
                            **_metrics(exec_ms, full),
                        }
                        return
                    unwrapped = _extract_text(full)
                    if unwrapped is not None:
                        full = unwrapped
                        transmitted = unwrapped
                delta = full[len(transmitted):]
                if delta and not tool_mode:
                    transmitted += delta
                    yield {"model": profile.name, "created_at": _now(), "message": {"role": "assistant", "content": delta}, "done": False}
                yield {
                    "model": profile.name,
                    "created_at": _now(),
                    "message": {"role": "assistant", "content": full if tool_mode else ""},
                    "done": True,
                    "done_reason": "stop",
                    **_metrics(exec_ms, transmitted or full),
                }
                return

    async def chat(self, profile: Profile, req: ChatRequest, deadline: float, new_chat: bool = True) -> tuple[dict, dict]:
        content = ""
        tool_calls = None
        metrics: dict = {}
        async for chunk in self.chat_chunks(profile, req, deadline, new_chat=new_chat):
            msg = chunk.get("message") or {}
            if chunk.get("done"):
                metrics = _pick_metrics(chunk)
                tool_calls = msg.get("tool_calls")
                if not content:
                    content = msg.get("content") or ""
            else:
                content += msg.get("content") or ""
        message: dict = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message, metrics


def _pick_metrics(chunk: dict) -> dict:
    keys = ("total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration")
    return {k: chunk.get(k, 0) for k in keys}