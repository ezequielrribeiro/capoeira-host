#!/usr/bin/env python3
"""Smoke test do CapoeiraHost.

Envia uma requisição à API textual (form-urlencoded + text/plain) e exibe o
retorno — inclusive streaming. Usa apenas a biblioteca padrão, sem dependências
extras e sem problemas de quoting de shell.

Uso:
    python smoke_test.py
    python smoke_test.py --endpoint generate --prompt "O que é capoeira?" --stream
    python smoke_test.py --model claude-sonnet --stream
    python smoke_test.py --endpoint chat --new-chat false
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

ERROR_HINTS = {
    400: "Campos obrigatórios ausentes ou role/tool inválido.",
    404: "Modelo não registrado no registry.",
    501: "Endpoint não aplicável ao CapoeiraHost.",
    502: "Falha reportada pela extensão durante a geração na Web.",
    503: "Provider offline: extensão não conectada ou aba fechada. Abra/recarregue a aba autenticada.",
    504: "Timeout de geração na Web.",
}


def default_base_url() -> str:
    host = os.environ.get("CAPOEIRA_HOST", "127.0.0.1")
    port = os.environ.get("CAPOEIRA_HTTP_PORT", "8765")
    return f"http://{host}:{port}"


def build_fields(args) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = [("model", args.model)]
    if args.endpoint == "generate":
        fields.append(("prompt", args.prompt))
    else:
        fields.append(("role", "user"))
        fields.append(("content", args.prompt))
    if args.stream:
        fields.append(("stream", "true"))
    if args.new_chat is not None:
        fields.append(("new_chat", args.new_chat))
    return fields


def build_request(url: str, fields: list[tuple[str, str]]) -> urllib.request.Request:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    return urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )


def handle_http_error(exc: urllib.error.HTTPError) -> None:
    print(f"HTTP {exc.code}", file=sys.stderr)
    raw = exc.read().decode("utf-8", errors="replace").strip()
    if raw:
        print(f"Erro: {raw}", file=sys.stderr)
    hint = ERROR_HINTS.get(exc.code)
    if hint:
        print(f"Dica: {hint}", file=sys.stderr)
    sys.exit(1)


def read_non_stream(url: str, fields: list[tuple[str, str]]) -> None:
    try:
        with urllib.request.urlopen(build_request(url, fields)) as resp:
            print(f"HTTP {resp.status} ({resp.headers.get('Content-Type', '')})")
            print(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        handle_http_error(exc)
    except urllib.error.URLError as exc:
        print(f"Falha de conexão: {exc.reason}", file=sys.stderr)
        print("O servidor está rodando? use 'python -m server.main'.", file=sys.stderr)
        sys.exit(1)


def read_stream(url: str, fields: list[tuple[str, str]]) -> None:
    try:
        with urllib.request.urlopen(build_request(url, fields)) as resp:
            print(f"HTTP {resp.status} (texto, streaming)")
            for raw in resp:
                text = raw.decode("utf-8")
                if text:
                    print(text, end="", flush=True)
            print()
    except urllib.error.HTTPError as exc:
        handle_http_error(exc)
    except urllib.error.URLError as exc:
        print(f"Falha de conexão: {exc.reason}", file=sys.stderr)
        print("O servidor está rodando? use 'python -m server.main'.", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test do CapoeiraHost")
    parser.add_argument("--base-url", default=default_base_url())
    parser.add_argument("--model", default="gemini-pro")
    parser.add_argument("--prompt", default="Olá! O que é capoeira? Responda em uma frase.")
    parser.add_argument("--endpoint", choices=["generate", "chat"], default="chat")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument(
        "--new-chat",
        choices=["true", "false"],
        default=None,
        help="Sobrescreve CAPOEIRA_NEW_CHAT por requisição (ausente = default do servidor).",
    )
    args = parser.parse_args()

    fields = build_fields(args)
    url = f"{args.base_url}/api/{args.endpoint}"

    print("=" * 40)
    print(f"POST {url}")
    print("Solicitação:")
    for key, value in fields:
        print(f"  {key} = {value}")
    print("=" * 40)

    if args.stream:
        read_stream(url, fields)
    else:
        read_non_stream(url, fields)


if __name__ == "__main__":
    main()