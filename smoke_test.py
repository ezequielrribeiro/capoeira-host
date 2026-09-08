#!/usr/bin/env python3
"""Smoke test do CapoeiraHost.

Envia uma requisição à API Ollama-compatível e exibe a solicitação e o
retorno — inclusive streaming NDJSON. Usa apenas a biblioteca padrão, sem
dependências extras e sem problemas de quoting de shell.

Uso:
    python smoke_test.py
    python smoke_test.py --endpoint generate --prompt "O que é capoeira?" --stream
    python smoke_test.py --model claude-sonnet --stream
    python smoke_test.py --endpoint chat --new-chat false
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

ERROR_HINTS = {
    400: "Corpo inválido ou recurso não suportado (imagens / tool calls).",
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


def build_payload(args) -> dict:
    if args.endpoint == "generate":
        payload = {"model": args.model, "prompt": args.prompt, "stream": args.stream}
    else:
        payload = {
            "model": args.model,
            "stream": args.stream,
            "messages": [{"role": "user", "content": args.prompt}],
        }
    if args.new_chat is not None:
        payload["new_chat"] = args.new_chat == "true"
    return payload


def print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def build_request(url: str, payload: dict) -> urllib.request.Request:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )


def handle_http_error(exc: urllib.error.HTTPError) -> None:
    print(f"HTTP {exc.code}", file=sys.stderr)
    raw = exc.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(raw, file=sys.stderr)
        return
    message = data.get("error") or (data.get("detail") and json.dumps(data["detail"], ensure_ascii=False))
    if message:
        print(f"Erro: {message}", file=sys.stderr)
    hint = ERROR_HINTS.get(exc.code)
    if hint:
        print(f"Dica: {hint}", file=sys.stderr)
    sys.exit(1)


def extract_text(chunk: dict, endpoint: str) -> str:
    if endpoint == "chat":
        return (chunk.get("message") or {}).get("content") or ""
    return chunk.get("response") or ""


def read_non_stream(url: str, payload: dict) -> None:
    try:
        with urllib.request.urlopen(build_request(url, payload)) as resp:
            print(f"HTTP {resp.status}")
            print_json(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        handle_http_error(exc)
    except urllib.error.URLError as exc:
        print(f"Falha de conexão: {exc.reason}", file=sys.stderr)
        print("O servidor está rodando? use 'python -m server.main'.", file=sys.stderr)
        sys.exit(1)


def read_stream(url: str, payload: dict, endpoint: str) -> None:
    try:
        with urllib.request.urlopen(build_request(url, payload)) as resp:
            print(f"HTTP {resp.status} (NDJSON)")
            for raw in resp:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = extract_text(chunk, endpoint)
                if text:
                    print(text, end="", flush=True)
                if chunk.get("done"):
                    print()
                    print("---")
                    print("Resposta completa. Linha final:")
                    print_json(chunk)
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

    payload = build_payload(args)
    url = f"{args.base_url}/api/{args.endpoint}"

    print("=" * 40)
    print(f"POST {url}")
    print("Solicitação:")
    print_json(payload)
    print("=" * 40)

    if args.stream:
        read_stream(url, payload, args.endpoint)
    else:
        read_non_stream(url, payload)


if __name__ == "__main__":
    main()