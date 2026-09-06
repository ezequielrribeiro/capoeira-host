from __future__ import annotations

from typing import Any

PROVIDERS = ("gemini", "claude", "copilot365", "chatgpt")

OPTION_HINTS = ("temperature", "top_k", "top_p", "num_predict", "num_ctx", "seed", "stop")


def build_options_instruction(options: dict[str, Any] | None) -> str:
    hints = {
        k: v for k, v in (options or {}).items() if k in OPTION_HINTS and v is not None
    }
    if not hints:
        return ""
    return "[OPTIONS] " + "; ".join(f"{k}={v}" for k, v in hints.items())


def build_system_envelope(system_prompt: str, options: dict[str, Any] | None = None, json_mode: bool = False) -> str:
    parts = ["[SYSTEM]", (system_prompt or "").strip()]
    opt = build_options_instruction(options or {})
    if opt:
        parts.append(opt)
    if json_mode:
        parts.append("[MODE] Responda APENAS com JSON válido, sem explicações ou texto fora do bloco JSON.")
    return "\n".join(part for part in parts if part)


def build_chat_transcript(messages: list[Any]) -> str:
    lines: list[str] = []
    for idx, msg in enumerate(messages):
        label = "SYSTEM" if msg.role == "system" else msg.role.upper()
        lines.append(f"[{idx}] [{label}]")
        lines.append(msg.content or "")
    return "\n".join(lines)


def apply_template(template: str | None, system: str, prompt: str) -> str:
    if not template:
        return f"{system}\n\n{prompt}".strip()
    text = (
        template.replace("{{ .System }}", system)
        .replace("{{ .Prompt }}", prompt)
        .replace("{{.System}}", system)
        .replace("{{.Prompt}}", prompt)
    )
    return text.strip()


def build_prompt_payload(
    profile: Any,
    system: str,
    prompt: str,
    *,
    json_mode: bool,
    options: dict[str, Any] | None,
    conversation: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "provider": profile.provider,
        "model": profile.name,
        "newChat": True,
        "systemPrompt": system,
        "prompt": prompt,
        "conversation": conversation or [],
        "options": dict(options or {}),
        "expectFormat": "JSON" if json_mode else "TEXT",
    }