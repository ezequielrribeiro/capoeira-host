from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

VERSION = "1.0.0"


class Options(BaseModel):
    model_config = ConfigDict(extra="allow")


class GenerateRequest(BaseModel):
    model: str
    prompt: str
    system: str | None = None
    template: str | None = None
    context: list[int] | None = None
    stream: bool = False
    format: str | None = None
    raw: bool = False
    images: list[str] | None = None
    keep_alive: Any = None
    options: Options = Options()


class ChatMessage(BaseModel):
    role: str
    content: str = ""
    images: list[str] | None = None
    tool_calls: list[Any] | None = None
    tool_call_id: str | None = None


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage] = []
    tools: list[Any] | None = None
    stream: bool = False
    format: str | None = None
    keep_alive: Any = None
    options: Options = Options()


class ShowRequest(BaseModel):
    model: str


class CreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model: str
    from_: str | None = Field(default=None, alias="from")
    system: str | None = None
    template: str | None = None
    parameters: dict[str, str] | None = None
    stream: bool = False


class CopyRequest(BaseModel):
    source: str
    destination: str


class DeleteRequest(BaseModel):
    model: str


class ModelDetails(BaseModel):
    parent_model: str = ""
    format: str = "web"
    family: str = ""
    families: list[str] = []
    parameter_size: str = "web"
    quantization_level: str = "web"


class ModelInfo(BaseModel):
    name: str
    model: str
    modified_at: datetime
    size: int = 1
    digest: str = "sha256:0000000000000000000000000000000000000000000000000000"
    details: ModelDetails = ModelDetails()


class TagsResponse(BaseModel):
    models: list[ModelInfo]


class ProcessInfo(BaseModel):
    name: str
    model: str
    size: int = 0
    digest: str = "sha256:0000"
    details: ModelDetails
    expires_at: datetime = datetime(1, 1, 1)
    size_vram: int = 0
    status: str = "idle"


class PsResponse(BaseModel):
    models: list[ProcessInfo]


class ShowResponse(BaseModel):
    model: str
    license: str | None = None
    modelfile: str = ""
    parameters: dict[str, str] = {}
    template: str | None = None
    details: ModelDetails | None = None
    messages: list[Any] = []


class ErrorResponse(BaseModel):
    error: str


class GenerateResponse(BaseModel):
    model: str
    created_at: datetime
    response: str
    done: bool = True
    done_reason: str = "stop"
    context: list[int] = []
    total_duration: int = 0
    load_duration: int = 0
    prompt_eval_count: int = 0
    prompt_eval_duration: int = 0
    eval_count: int = 0
    eval_duration: int = 0


class ChatMessageOut(BaseModel):
    role: str = "assistant"
    content: str
    images: list[str] | None = None


class ChatResponse(BaseModel):
    model: str
    created_at: datetime
    message: ChatMessageOut
    done: bool = True
    done_reason: str = "stop"
    total_duration: int = 0
    load_duration: int = 0
    prompt_eval_count: int = 0
    prompt_eval_duration: int = 0
    eval_count: int = 0
    eval_duration: int = 0