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