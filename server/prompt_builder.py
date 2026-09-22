from __future__ import annotations

from typing import Any

PROVIDERS = ("gemini", "claude", "copilot365", "chatgpt")


def build_system_envelope(system_prompt: str, options: dict[str, Any] | None = None, tools: str | None = None) -> str:
    """Retorna o system prompt verbatim, sem qualquer encapsulamento.

    A formatação/encapsulamento do texto (tags, instruções, contratos) é
    responsabilidade exclusiva da ferramenta que consome o CapoeiraHost.
    ``options`` e ``tools`` são aceitos por compatibilidade, mas ignorados."""
    return (system_prompt or "").strip()


def build_chat_transcript(messages: list[Any]) -> str:
    lines: list[str] = []
    for msg in messages:
        role = getattr(msg, "role", None)
        content = (getattr(msg, "content", None) or "").strip()
        if not content:
            continue
        if role == "system":
            lines.append(content)
            continue
        label = role.upper()
        lines.append(f"[{label}] {content}".strip())
    return "\n".join(line for line in lines if line)


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
    new_chat: bool = True,
) -> dict[str, Any]:
    return {
        "provider": profile.provider,
        "model": profile.name,
        "newChat": new_chat,
        "systemPrompt": system,
        "prompt": prompt,
    }