from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ChatState:
    revision: int = 0
    transcript: list[dict] = field(default_factory=list)
    updated_at: float = 0.0


class ChatWatcher:
    """Estado por provider (transcript e revision) alimentado pelos CHAT_UPDATEs.

    Além de expor a última ``revision`` conhecida no header
    `X-Capoeira-Revision` de `POST /api/chat/read`, ``ingest`` devolve o texto
    das respostas do **assistente** presentes no delta para que o handler possa
    relayá-las à aplicação registrada (turnos digitados na Web, que o host não
    iniciou).
    """

    def __init__(self) -> None:
        self._states: dict[str, ChatState] = {}

    def _ensure(self, provider: str) -> ChatState:
        if provider not in self._states:
            self._states[provider] = ChatState()
        return self._states[provider]

    def latest(self, provider: str) -> ChatState | None:
        return self._states.get(provider)

    async def ingest(self, provider: str, payload: dict) -> str | None:
        """Aplica um CHAT_UPDATE da extensão.

        Retorna o texto concatenado das mensagens ``assistant`` do delta (ou
        ``None`` se o delta não trouxer resposta de assistente)."""
        state = self._ensure(provider)
        revision = int(payload.get("revision") or state.revision)
        transcript = payload.get("transcript")
        messages: list[dict] = payload.get("messages") or []

        state.revision = revision
        if transcript is not None:
            state.transcript = list(transcript)
        elif messages:
            state.transcript = state.transcript + list(messages)
        state.updated_at = time.monotonic()

        replies: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            if (message.get("role") or "").strip().lower() != "assistant":
                continue
            content = (message.get("content") or "").strip()
            if content:
                replies.append(content)
        return "\n".join(replies) or None