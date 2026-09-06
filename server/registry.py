from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from pydantic import BaseModel, Field, model_validator

from .errors import BadRequest, ModelNotFound
from .prompt_builder import PROVIDERS


class Profile(BaseModel):
    name: str
    provider: str
    display_name: str = ""
    system_prompt: str = ""
    template: str | None = None
    streaming: bool = False
    options: dict = Field(default_factory=lambda: {"temperature": 0.7})
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _validate_provider(self) -> "Profile":
        if self.provider != "auto" and self.provider not in PROVIDERS:
            raise ValueError(f"provider desconhecido: {self.provider}")
        return self


DEFAULT_MODELS = [
    {
        "name": "gemini-pro",
        "display_name": "Gemini Pro (Web)",
        "provider": "gemini",
        "system_prompt": "Você é um assistente útil. Responda de forma concisa e direta.",
        "template": "[INST] {{ .System }} [/INST]\n\n{{ .Prompt }}",
        "streaming": False,
        "options": {"temperature": 0.7, "num_predict": 2048, "num_ctx": 32768},
    },
    {
        "name": "claude-sonnet",
        "display_name": "Claude Sonnet (Web)",
        "provider": "claude",
        "system_prompt": "Você é um assistente de IA. Responda com clareza.",
        "template": "{{ .System }}\n\n{{ .Prompt }}",
        "streaming": True,
        "options": {"temperature": 1.0, "num_predict": 4096},
    },
    {
        "name": "copilot-365",
        "display_name": "Microsoft 365 Copilot (Web)",
        "provider": "copilot365",
        "system_prompt": "Assistente profissional da Microsoft 365.",
        "streaming": False,
        "options": {},
    },
    {
        "name": "chatgpt",
        "display_name": "ChatGPT (Web)",
        "provider": "chatgpt",
        "system_prompt": "Você é ChatGPT, um assistente útil.",
        "streaming": False,
        "options": {},
    },
]


class ModelRegistry:
    def __init__(self, path: str) -> None:
        self.path = path
        self.default_provider = "gemini"
        self._models: dict[str, Profile] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            self._write_defaults()
        with open(self.path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.default_provider = data.get("default_provider", "gemini")
        self._models = {item["name"]: Profile(**item) for item in data.get("models", [])}

    def _save(self) -> None:
        payload = {
            "version": 1,
            "default_provider": self.default_provider,
            "models": [m.model_dump(mode="json") for m in self._models.values()],
        }
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    def _write_defaults(self) -> None:
        payload = {"version": 1, "default_provider": "gemini", "models": DEFAULT_MODELS}
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    def list(self) -> list[Profile]:
        return list(self._models.values())

    def get(self, name: str) -> Profile | None:
        return self._models.get(name)

    def require(self, name: str) -> Profile:
        profile = self.get(name)
        if profile is None:
            raise ModelNotFound(f"model '{name}' not found")
        return profile

    def create(self, profile: Profile) -> Profile:
        if profile.name in self._models:
            raise BadRequest(f"model '{profile.name}' already exists")
        self._models[profile.name] = profile
        self._save()
        return profile

    def copy(self, source: str, destination: str) -> Profile:
        src = self.require(source)
        if destination in self._models:
            raise BadRequest(f"model '{destination}' already exists")
        dup = src.model_copy(deep=True)
        dup.name = destination
        dup.created_at = datetime.now(timezone.utc)
        self._models[destination] = dup
        self._save()
        return dup

    def delete(self, name: str) -> None:
        if name not in self._models:
            raise ModelNotFound(f"model '{name}' not found")
        del self._models[name]
        self._save()