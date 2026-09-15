from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

MAX_EVENTS_PER_PROVIDER = 200


@dataclass
class ChatEvent:
    revision: int
    messages: list[dict]
    updated_at: float


@dataclass
class ChatState:
    revision: int = 0
    transcript: list[dict] = field(default_factory=list)
    events: list[ChatEvent] = field(default_factory=list)
    updated_at: float = 0.0


class ChatWatcher:
    """Registro central dos CHAT_UPDATEs enviados pela extensão.

    Por provedor, guarda o transcript mais recente e um buffer de eventos
    (revision, mensagens novas) usado pelo long-poll de `POST /api/chat/watch`.
    """

    def __init__(self, max_events: int = MAX_EVENTS_PER_PROVIDER) -> None:
        self._states: dict[str, ChatState] = {}
        self._max_events = max_events
        self._condition = asyncio.Condition()

    def _ensure(self, provider: str) -> ChatState:
        if provider not in self._states:
            self._states[provider] = ChatState()
        return self._states[provider]

    def latest(self, provider: str) -> ChatState | None:
        return self._states.get(provider)

    async def ingest(self, provider: str, payload: dict) -> None:
        """Aplica um CHAT_UPDATE da extensão e acorda os long-polls esperando."""
        async with self._condition:
            state = self._ensure(provider)
            revision = int(payload.get("revision") or state.revision)
            messages: list[dict] = payload.get("messages") or []
            transcript = payload.get("transcript")

            state.revision = revision
            if transcript is not None:
                state.transcript = list(transcript)
            elif messages:
                state.transcript = state.transcript + list(messages)

            if messages:
                state.events.append(
                    ChatEvent(
                        revision=revision,
                        messages=list(messages),
                        updated_at=time.monotonic(),
                    )
                )
                if len(state.events) > self._max_events:
                    del state.events[: len(state.events) - self._max_events]

            state.updated_at = time.monotonic()
            self._condition.notify_all()

    def events_since(self, provider: str, revision: int) -> list[ChatEvent]:
        state = self._states.get(provider)
        if state is None:
            return []
        return [e for e in state.events if e.revision > revision]

    async def wait_events(self, provider: str, since_revision: int, timeout: float) -> list[ChatEvent]:
        """Long-poll: retorna os eventos com revision > since_revision assim que
        chegarem (ou imediatamente se já existirem), vazio no timeout."""
        state = self._ensure(provider)
        deadline = time.monotonic() + timeout
        while True:
            events = [e for e in state.events if e.revision > since_revision]
            if events:
                return events
            remain = deadline - time.monotonic()
            if remain <= 0:
                return []
            try:
                async with self._condition:
                    await asyncio.wait_for(self._condition.wait(), timeout=remain)
            except asyncio.TimeoutError:
                return []