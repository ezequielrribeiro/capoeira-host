import asyncio
import json
import threading
import time
import uuid

import pytest
import websockets
from fastapi.testclient import TestClient

from server.config import Settings
from server.main import create_app

WS_TEST_PORT = 28766
POLL_TIMEOUT = 5.0


@pytest.fixture
def models_file(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "default_provider": "gemini",
                "models": [
                    {
                        "name": "gemini-pro",
                        "display_name": "Gemini Pro (Web)",
                        "provider": "gemini",
                        "system_prompt": "Você é um assistente útil.",
                        "template": "{{ .System }}\n\n{{ .Prompt }}",
                        "streaming": False,
                        "options": {"temperature": 0.7},
                    },
                    {
                        "name": "claude-sonnet",
                        "display_name": "Claude Sonnet (Web)",
                        "provider": "claude",
                        "system_prompt": "Você é um assistente de IA.",
                        "template": "{{ .System }}\n\n{{ .Prompt }}",
                        "streaming": True,
                        "options": {"temperature": 1.0},
                    },
                    {
                        "name": "copilot-365",
                        "display_name": "Microsoft 365 Copilot (Web)",
                        "provider": "copilot365",
                        "system_prompt": "Assistente profissional da Microsoft 365.",
                        "streaming": False,
                        "options": {},
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture
def app(models_file):
    settings = Settings(
        host="127.0.0.1",
        ws_port=WS_TEST_PORT,
        models_file=models_file,
        timeout=5.0,
        queue_size=10,
    )
    return create_app(settings)


@pytest.fixture
def app_reuse_chat(models_file):
    settings = Settings(
        host="127.0.0.1",
        ws_port=WS_TEST_PORT,
        models_file=models_file,
        timeout=5.0,
        queue_size=10,
        new_chat=False,
    )
    return create_app(settings)


def start_fake_bridge(provider, response_text, *, streaming=False, partials=None, origin=None):
    """Simula a extensão do navegador: conecta no WS do bridge, envia HELLO,
    aguarda SEND_PROMPT e devolve parciais (se streaming) + RESPONSE final.

    ``origin`` permite simular a origem que o navegador envia (ex.: a origem da
    página em um content script, como ``https://gemini.google.com``)."""
    ctx = {
        "received": [],
        "error": None,
        "provider": provider,
        "streaming": streaming,
        "partials": partials or [],
        "response_text": response_text,
    }

    def worker():
        async def _run():
            kwargs = {}
            if origin is not None:
                kwargs["additional_headers"] = {"Origin": origin}
            async with websockets.connect(
                f"ws://127.0.0.1:{WS_TEST_PORT}", **kwargs
            ) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "version": "1.0",
                            "action": "HELLO",
                            "id": str(uuid.uuid4()),
                            "payload": {
                                "provider": provider,
                                "adapters": [provider],
                                "supportsStreaming": streaming,
                                "supportsNewChat": True,
                                "tabTitle": "Teste CapoeiraHost",
                            },
                        }
                    )
                )
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("action") != "SEND_PROMPT":
                        continue
                    ctx["received"].append(msg)
                    for partial in ctx["partials"]:
                        await ws.send(
                            json.dumps(
                                {
                                    "version": "1.0",
                                    "action": "STREAM_UPDATE",
                                    "status": "STREAMING",
                                    "id": msg["id"],
                                    "payload": {"partial": partial},
                                }
                            )
                        )
                        await asyncio.sleep(0.05)
                    await ws.send(
                        json.dumps(
                            {
                                "version": "1.0",
                                "action": "RESPONSE",
                                "status": "SUCCESS",
                                "id": msg["id"],
                                "payload": {
                                    "rawResponse": response_text,
                                    "executionTimeMs": 50,
                                },
                                "error": None,
                            }
                        )
                    )
                    return

        try:
            asyncio.run(_run())
        except Exception as exc:
            ctx["error"] = repr(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread, ctx


def post_until(client, path, payload, timeout=POLL_TIMEOUT):
    """Repete o POST enquanto o provider ainda não estiver registrado (503),
    garantindo que o HELLO da extensão fake foi processado pelo bridge."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.post(path, json=payload)
        if last.status_code != 503:
            return last
        time.sleep(0.05)
    return last


# ------------------------------------------------------------------ smoke


def test_version_ok(app):
    with TestClient(app) as client:
        resp = client.get("/api/version")
        assert resp.status_code == 200
        assert resp.json() == {"version": "1.0.0"}


def test_tags_lists_profiles(app):
    with TestClient(app) as client:
        resp = client.get("/api/tags")
        assert resp.status_code == 200
        names = {m["name"] for m in resp.json()["models"]}
        assert names == {"gemini-pro", "claude-sonnet", "copilot-365"}


def test_chat_without_bridge_returns_503(app):
    with TestClient(app) as client:
        resp = client.post(
            "/api/chat",
            json={"model": "gemini-pro", "messages": [{"role": "user", "content": "oi"}]},
        )
        assert resp.status_code == 503
        assert "no bridge available" in resp.json()["error"]


# ------------------------------------------------------------------ e2e bridge


def test_generate_via_fake_extension(app):
    thread, ctx = start_fake_bridge("gemini", "A capoeira é uma arte afro-brasileira.")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {"model": "gemini-pro", "messages": [{"role": "user", "content": "O que é capoeira?"}]},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["message"]["content"] == "A capoeira é uma arte afro-brasileira."
            assert data["done"] is True

            assert ctx["received"], "bridge não recebeu SEND_PROMPT"
            payload = ctx["received"][0]["payload"]
            assert "[SYSTEM]" in payload["systemPrompt"]
            assert "[0] [USER]" in payload["prompt"]
            assert payload["newChat"] is True
    finally:
        thread.join(timeout=10)


def test_streaming_ndjson_via_fake_extension(app):
    thread, ctx = start_fake_bridge(
        "claude",
        "A capoeira é uma arte.",
        streaming=True,
        partials=["A capoeira ", "é uma arte."],
    )
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "claude-sonnet",
                    "stream": True,
                    "messages": [{"role": "user", "content": "O que é capoeira?"}],
                },
            )
            assert resp.status_code == 200
            assert "application/x-ndjson" in resp.headers["content-type"]

            lines = [json.loads(line) for line in resp.text.strip().splitlines()]
            assert len(lines) >= 3
            content = "".join(chunk["message"]["content"] for chunk in lines if not chunk["done"])
            assert content == "A capoeira é uma arte."
            assert lines[-1]["done"] is True
            assert lines[-1]["done_reason"] == "stop"
            assert ctx["received"], "bridge não recebeu SEND_PROMPT"
    finally:
        thread.join(timeout=10)


# --------------------------- validação de origem (CSWSH) ---------------------------


def test_bridge_accepts_web_provider_origin(app):
    """Repro do bug: um content script conecta ao WS com a origem da página
    (https://gemini.google.com). O bridge deve aceitar e processar o chat."""
    thread, ctx = start_fake_bridge(
        "gemini",
        "A capoeira é uma arte afro-brasileira.",
        origin="https://gemini.google.com",
    )
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {"model": "gemini-pro", "messages": [{"role": "user", "content": "oi"}]},
            )
            assert resp.status_code == 200
            assert resp.json()["message"]["content"] == "A capoeira é uma arte afro-brasileira."
            assert ctx["received"], "bridge rejeitou a conexão com origem de página"
            assert ctx["error"] is None
    finally:
        thread.join(timeout=10)


def test_bridge_rejects_unknown_origin(app):
    """Origem desconhecida deve continuar sendo rejeitada (CSWSH defense)."""
    thread, ctx = start_fake_bridge(
        "gemini",
        "nunca deve chegar",
        origin="https://evil.example",
    )
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/chat",
                json={"model": "gemini-pro", "messages": [{"role": "user", "content": "oi"}]},
            )
            assert resp.status_code == 503
        thread.join(timeout=10)
        assert ctx["error"], "conexão de origem desconhecida deveria ter sido rejeitada"
        assert not ctx["received"]
    except Exception:
        thread.join(timeout=10)
        raise


# --------------------------- Copilot 365 (m365.cloud.microsoft) ---------------------------


def test_bridge_accepts_m365_copilot_origin(app):
    thread, ctx = start_fake_bridge(
        "copilot365",
        "Sou o Copilot da Microsoft 365.",
        origin="https://m365.cloud.microsoft",
    )
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "copilot-365",
                    "messages": [{"role": "user", "content": "Quem é você?"}],
                },
            )
            assert resp.status_code == 200
            assert resp.json()["message"]["content"] == "Sou o Copilot da Microsoft 365."
            assert ctx["received"], "bridge rejeitou a origem de m365.cloud.microsoft"
            assert ctx["error"] is None
    finally:
        thread.join(timeout=10)


def test_chat_via_copilot365_bridge(app):
    thread, ctx = start_fake_bridge("copilot365", "Resposta do Copilot 365.")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "copilot-365",
                    "messages": [{"role": "user", "content": "Oi Copilot"}],
                },
            )
            assert resp.status_code == 200
            assert resp.json()["message"]["content"] == "Resposta do Copilot 365."
            assert ctx["received"]
            payload = ctx["received"][0]["payload"]
            assert payload["provider"] == "copilot365"
            assert payload["newChat"] is True
    finally:
        thread.join(timeout=10)


# --------------------------- reutilização de chat (new_chat) ---------------------------


def test_chat_new_chat_false_from_global_default(app_reuse_chat):
    """Com CAPOEIRA_NEW_CHAT=false, o SEND_PROMPT não deve iniciar novo chat."""
    thread, ctx = start_fake_bridge("gemini", "Resposta sem novo chat.")
    try:
        with TestClient(app_reuse_chat) as client:
            resp = post_until(
                client,
                "/api/chat",
                {"model": "gemini-pro", "messages": [{"role": "user", "content": "oi"}]},
            )
            assert resp.status_code == 200
            assert ctx["received"]
            assert ctx["received"][0]["payload"]["newChat"] is False
    finally:
        thread.join(timeout=10)


def test_chat_new_chat_false_via_request_override(app):
    """Campo new_chat:false por requisição sobrescreve o default global (true)."""
    thread, ctx = start_fake_bridge("gemini", "Resposta sem novo chat.")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "gemini-pro",
                    "new_chat": False,
                    "messages": [{"role": "user", "content": "oi"}],
                },
            )
            assert resp.status_code == 200
            assert ctx["received"]
            assert ctx["received"][0]["payload"]["newChat"] is False
    finally:
        thread.join(timeout=10)


def test_chat_new_chat_true_overrides_global_false(app_reuse_chat):
    """Campo new_chat:true por requisição sobrescreve o default global (false)."""
    thread, ctx = start_fake_bridge("gemini", "Resposta com novo chat.")
    try:
        with TestClient(app_reuse_chat) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "gemini-pro",
                    "new_chat": True,
                    "messages": [{"role": "user", "content": "oi"}],
                },
            )
            assert resp.status_code == 200
            assert ctx["received"]
            assert ctx["received"][0]["payload"]["newChat"] is True
    finally:
        thread.join(timeout=10)


def test_generate_new_chat_false_via_request_override(app):
    """Campo new_chat:false também vale para /api/generate."""
    thread, ctx = start_fake_bridge("gemini", "Resposta sem novo chat.")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/generate",
                {"model": "gemini-pro", "prompt": "oi", "new_chat": False},
            )
            assert resp.status_code == 200
            assert resp.json()["response"] == "Resposta sem novo chat."
            assert ctx["received"]
            assert ctx["received"][0]["payload"]["newChat"] is False
    finally:
        thread.join(timeout=10)


# --------------------------- tool calling simulado ---------------------------


TOOLS_SAMPLE = [
    {
        "type": "function",
        "function": {
            "name": "shopping",
            "description": "Consulta/prepara uma compra.",
            "parameters": {
                "type": "object",
                "properties": {"item": {"type": "string"}, "quantidade": {"type": "integer"}},
                "required": ["item"],
            },
        },
    }
]


def test_tool_calling_returns_tool_calls(app):
    """Com 'tools' presente, se a Web devolver o JSON de contrato, o host
    converte em message.tool_calls (formato Ollama) com content vazio."""
    tool_json = '{"name": "shopping", "arguments": {"item": "leite", "quantidade": 2}}'
    thread, ctx = start_fake_bridge("gemini", tool_json)
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "gemini-pro",
                    "tools": TOOLS_SAMPLE,
                    "messages": [{"role": "user", "content": "Preciso comprar leite."}],
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["message"]["content"] == ""
            calls = data["message"]["tool_calls"]
            assert calls and calls[0]["function"]["name"] == "shopping"
            assert calls[0]["function"]["arguments"] == {"item": "leite", "quantidade": 2}
            assert ctx["received"], "bridge não recebeu SEND_PROMPT"
            prompt = ctx["received"][0]["payload"]["prompt"]
            assert "[AVAILABLE TOOLS]" in prompt
            assert "[MODE TOOL_CALLING]" in prompt
    finally:
        thread.join(timeout=10)


def test_tool_calling_falls_back_to_text(app):
    """Se a Web responder texto (não JSON de contrato), faz fallback em content."""
    thread, ctx = start_fake_bridge("gemini", "Vou verificar para você.")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "gemini-pro",
                    "tools": TOOLS_SAMPLE,
                    "messages": [{"role": "user", "content": "Compre leite."}],
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["message"]["content"] == "Vou verificar para você."
            assert not data.get("message", {}).get("tool_calls")
    finally:
        thread.join(timeout=10)


def test_tool_result_roundtrip_accepted(app):
    """Assistant com tool_calls + role 'tool' são aceitos quando 'tools' presente
    e o resultado fica no transcript (TOOL_RESULT)."""
    thread, ctx = start_fake_bridge("gemini", '{"text": "Você tem 2 leites."}')
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "gemini-pro",
                    "tools": TOOLS_SAMPLE,
                    "messages": [
                        {"role": "user", "content": "Preciso de leite."},
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {"function": {"name": "shopping", "arguments": {"item": "leite"}}}
                            ],
                        },
                        {"role": "tool", "content": "Leite comprado", "tool_call_id": "call-1"},
                    ],
                },
            )
            assert resp.status_code == 200
            assert resp.json()["message"]["content"] == "Você tem 2 leites."
            assert ctx["received"]
            prompt = ctx["received"][0]["payload"]["prompt"]
            assert "[TOOL_RESULT]" in prompt
            assert "[assistant chamou ferramenta] name=shopping" in prompt
    finally:
        thread.join(timeout=10)


def test_tool_call_and_role_tool_rejected_without_tools(app):
    """Sem 'tools' no request, tool_calls / role 'tool' continuam rejeitados (400)."""
    with TestClient(app) as client:
        resp = client.post(
            "/api/chat",
            json={
                "model": "gemini-pro",
                "messages": [
                    {"role": "tool", "content": "ok", "tool_call_id": "call-1"},
                ],
            },
        )
        assert resp.status_code == 400
        assert "tools" in resp.json()["error"]


def test_tool_calling_streaming_emits_calls_on_final_chunk(app):
    """Em streaming com tools, os tool_calls aparecem apenas no chunk final (done)."""
    tool_json = '{"name": "shopping", "arguments": {"item": "leite"}}'
    thread, ctx = start_fake_bridge("gemini", tool_json)
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "gemini-pro",
                    "stream": True,
                    "tools": TOOLS_SAMPLE,
                    "messages": [{"role": "user", "content": "Compre leite."}],
                },
            )
            assert resp.status_code == 200
            lines = [json.loads(line) for line in resp.text.strip().splitlines()]
            assert lines[-1]["done"] is True
            msg = lines[-1]["message"]
            assert msg["content"] == ""
            assert msg["tool_calls"][0]["function"]["name"] == "shopping"
    finally:
        thread.join(timeout=10)