from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    host: str = field(default_factory=lambda: _env("CAPOEIRA_HOST", "127.0.0.1"))
    http_port: int = field(default_factory=lambda: int(_env("CAPOEIRA_HTTP_PORT", "8765")))
    ws_port: int = field(default_factory=lambda: int(_env("CAPOEIRA_WS_PORT", "8766")))
    models_file: str = field(default_factory=lambda: _env("CAPOEIRA_MODELS_FILE", "models.json"))
    timeout: float = field(default_factory=lambda: float(_env("CAPOEIRA_TIMEOUT", "180")))
    queue_size: int = field(default_factory=lambda: int(_env("CAPOEIRA_QUEUE", "10")))