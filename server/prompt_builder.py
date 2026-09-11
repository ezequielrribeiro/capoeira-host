from __future__ import annotations

import json
from typing import Any

PROVIDERS = ("gemini", "claude", "copilot365", "chatgpt")

OPTION_HINTS = ("temperature", "top_k", "top_p", "num_predict", "num_ctx", "seed", "stop")

TOOL_CALL_CONTRACT = (
    '[MODE TOOL_CALLING] Se for necessário chamar uma ferramenta, responda APENAS com um '
    'objeto JSON válido, sem markdown e sem texto fora dele, neste formato exato: '
    '{"name": "<nome da ferramenta>", "arguments": {<argumentos>}}. Caso contrário, '
    'responda APENAS com este outro objeto JSON válido: {"text": "<sua resposta>"}.'
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
    return (
        "[AVAILABLE TOOLS] (apenas nomes aceitos em \"name\"; "
        "\"arguments\" deve respeitar o schema de 'parameters' de cada ferramenta)\n"
        + json.dumps({"tools": bodies}, ensure_ascii=False)
    )


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
    if json_mode:
        parts.append("[MODE] Responda APENAS com JSON válido, sem explicações ou texto fora do bloco JSON.")
    if tools:
        parts.append(TOOL_CALL_CONTRACT)
    return "\n".join(part for part in parts if part)


def build_chat_transcript(messages: list[Any]) -> str:
    lines: list[str] = []
    for idx, msg in enumerate(messages):
        if msg.role == "system":
            label = "SYSTEM"
        elif msg.role == "tool":
            label = "TOOL_RESULT"
        else:
            label = msg.role.upper()
        lines.append(f"[{idx}] [{label}]")
        if msg.role == "tool" and msg.tool_call_id:
            lines.append(f"(resposta da chamada de ferramenta {msg.tool_call_id})")
        if msg.role == "assistant" and msg.tool_calls:
            for call in msg.tool_calls:
                fn = (call or {}).get("function") or {}
                lines.append(
                    f"[assistant chamou ferramenta] name={fn.get('name')} "
                    f"arguments={json.dumps(fn.get('arguments'), ensure_ascii=False)}"
                )
        if msg.content:
            lines.append(msg.content or "")
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