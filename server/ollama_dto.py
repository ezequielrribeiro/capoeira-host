from __future__ import annotations

from dataclasses import dataclass, field

VERSION = "1.0.0"


@dataclass
class ChatMessage:
    role: str
    content: str = ""
    tool_call_id: str | None = None


@dataclass
class GenerateRequest:
    model: str
    prompt: str
    system: str | None = None
    template: str | None = None
    stream: bool = False
    new_chat: bool | None = None
    options: dict = field(default_factory=dict)


@dataclass
class ChatRequest:
    model: str
    messages: list[ChatMessage] = field(default_factory=list)
    tools: str | None = None
    stream: bool = False
    new_chat: bool | None = None
    options: dict = field(default_factory=dict)