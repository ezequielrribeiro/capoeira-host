from __future__ import annotations

import re
from typing import Any

PROVIDERS = ("gemini", "claude", "copilot365", "chatgpt")

OPTION_HINTS = ("temperature", "top_k", "top_p", "num_predict", "num_ctx", "seed", "stop")

_TOOL_CALL_MARKER_LINE = re.compile(r"^\s*\[TOOL_CALL\]")

TOOL_CALL_CONTRACT = (
    '[MODE TOOL_CALLING] Se for necessário chamar uma ferramenta, emita EXATAMENTE '
    'uma linha por chamada neste formato (sem blocos de código, sem JSON e sem '
    'marcação markdown):\n'
    '\n'
    '[TOOL_CALL] nome_da_ferramenta | chave1=valor1 | chave2=valor2\n'
    '\n'
    'Cada chamada é uma linha começando com [TOOL_CALL], seguido do nome da '
    'ferramenta e dos argumentos como pares chave=valor separados por "|". '
    'Use aspas simples para valores com espaço: chave=\'valor com espaço\'. '
    'Números e booleanos podem ser informados direto. Para chamadas paralelas, '
    'emita uma linha por chamada. Pode haver texto antes e depois das linhas. '
    'Se não for chamar ferramenta, responda normalmente com texto.'
)


def build_tools_instruction(tools_text: str | None) -> str:
    """Retorna o bloco [TOOLS] + linhas [TOOL] já no contrato textual chave=valor.

    O texto chega pronto do request (form field 'tools'), um tool por linha,
    opcionalmente prefixado com '[TOOL] '. Aqui apenas normaliza e injeta o header.
    """
    cleaned: list[str] = []
    for ln in (tools_text or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("[TOOLS]"):
            continue
        ln = re.sub(r"^\[TOOL\]\s*", "", ln)
        if ln:
            cleaned.append("[TOOL] " + ln)
    if not cleaned:
        return ""
    header = "[TOOLS] ferramentas disponíveis (uma por linha, formato chave=valor, sem JSON):"
    return "\n".join([header] + cleaned)


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
    tools: str | None = None,
) -> str:
    parts = ["[SYSTEM]", (system_prompt or "").strip()]
    opt = build_options_instruction(options or {})
    if opt:
        parts.append(opt)
    tool_instr = build_tools_instruction(tools)
    if tool_instr:
        parts.append(tool_instr)
        parts.append(TOOL_CALL_CONTRACT)
    return "\n".join(part for part in parts if part)


def build_chat_transcript(messages: list[Any]) -> str:
    lines: list[str] = []
    for msg in messages:
        role = getattr(msg, "role", None)
        content = (getattr(msg, "content", None) or "").strip()
        if role == "system":
            lines.append(f"[SYSTEM] {content}")
            continue
        if role == "tool":
            prefix = f"[TOOL_RESULT] ({msg.tool_call_id}) " if (msg.tool_call_id) else "[TOOL_RESULT] "
            lines.append(f"{prefix}{content}".strip())
            continue
        if role == "assistant":
            parts = [ln.strip() for ln in (msg.content or "").splitlines() if ln.strip()]
            calls = [ln for ln in parts if _TOOL_CALL_MARKER_LINE.match(ln)]
            prose = [ln for ln in parts if not _TOOL_CALL_MARKER_LINE.match(ln)]
            for ln in calls:
                lines.append(ln)
            if prose:
                lines.append("[ASSISTANT] " + " ".join(prose))
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
