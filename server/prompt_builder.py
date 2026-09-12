from __future__ import annotations

import json
from typing import Any

PROVIDERS = ("gemini", "claude", "copilot365", "chatgpt")

OPTION_HINTS = ("temperature", "top_k", "top_p", "num_predict", "num_ctx", "seed", "stop")

TOOL_CALL_CONTRACT = (
    '[MODE TOOL_CALLING] Se for necessário chamar uma ferramenta, emita EXATAMENTE '
    'uma linha por chamada neste formato (sem blocos de código e sem markdown):\n'
    '\n'
    '[TOOL_CALL] nome_da_ferramenta {"chave": "valor"}\n'
    '\n'
    'Cada chamada é uma linha começando com [TOOL_CALL], seguido do nome da '
    'ferramenta e dos argumentos como um objeto JSON válido respeitando o schema '
    'de "parameters" da ferramenta. Para chamadas paralelas, emita uma linha por '
    'chamada. Pode haver texto antes e depois das linhas. Se não for chamar '
    'ferramenta, responda normalmente com texto.'
)

JSON_OUTPUT_CONTRACT = (
    '[MODE_JSON] Responda com o JSON EXATAMENTE entre as linhas de marcador abaixo, '
    'sem markdown e sem texto fora delas:\n'
    '[JSON_START]\n'
    '{"chave": "valor"}\n'
    '[JSON_END]'
)


def _normalize_tools(tools: list[Any]) -> list[Any]:
    """Normaliza tools no formato Ollama (lista com chave 'function') e OpenAI
    (plano, sem o wrapper 'function')."""
    normalized: list[Any] = []
    for tool in tools:
        if isinstance(tool, dict) and "function" in tool:
            normalized.append(tool["function"])
        else:
            normalized.append(tool)
    return normalized


def build_tools_instruction(tools: list[Any] | None) -> str:
    if not tools:
        return ""
    bodies = _normalize_tools(tools)
    lines = ["[TOOLS] ferramentas disponíveis (uma por linha, respeitando o schema de 'parameters'):"]
    lines.extend("[TOOL] " + json.dumps(body, ensure_ascii=False) for body in bodies)
    return "\n".join(lines)


def build_options_instruction(options: dict[str, Any] | None) -> str:
    hints = {
        k: v for k, v in (options or {}).items() if k in OPTION_HINTS and v is not None
    }
    if not hints:
        return ""
    return "[OPTIONS] " + "; ".join(f"{k}={v}" for k, v in hints.items())


def build_system_envelope(
    system_prompt: str,
    options: dict[str, Any] | None = None,
    json_mode: bool = False,
    tools: list[Any] | None = None,
) -> str:
    parts = ["[SYSTEM]", (system_prompt or "").strip()]
    opt = build_options_instruction(options or {})
    if opt:
        parts.append(opt)
    tool_instr = build_tools_instruction(tools)
    if tool_instr:
        parts.append(tool_instr)
    if json_mode and not tools:
        parts.append(JSON_OUTPUT_CONTRACT)
    if tools:
        parts.append(TOOL_CALL_CONTRACT)
    return "\n".join(part for part in parts if part)


def build_chat_transcript(messages: list[Any]) -> str:
    lines: list[str] = []
    for msg in messages:
        if msg.role == "system":
            lines.append(f"[SYSTEM] {msg.content}".strip())
            continue
        if msg.role == "tool":
            prefix = f"[TOOL_RESULT] ({msg.tool_call_id}) " if msg.tool_call_id else "[TOOL_RESULT] "
            lines.append(f"{prefix}{msg.content}".strip())
            continue
        if msg.role == "assistant" and msg.tool_calls:
            for call in msg.tool_calls:
                fn = (call or {}).get("function") or {}
                lines.append(
                    f"[TOOL_CALL] {fn.get('name')} "
                    f"{json.dumps(fn.get('arguments'), ensure_ascii=False)}"
                )
        label = msg.role.upper()
        lines.append(f"[{label}] {msg.content}".strip())
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
    json_mode: bool,
    options: dict[str, Any] | None,
    conversation: list[dict[str, Any]] | None = None,
    new_chat: bool = True,
) -> dict[str, Any]:
    return {
        "provider": profile.provider,
        "model": profile.name,
        "newChat": new_chat,
        "systemPrompt": system,
        "prompt": prompt,
        "conversation": conversation or [],
        "options": dict(options or {}),
        "expectFormat": "JSON" if json_mode else "TEXT",
    }