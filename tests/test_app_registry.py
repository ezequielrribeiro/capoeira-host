import json

import pytest
from fastapi.testclient import TestClient

from server.config import Settings
from server.main import create_app

from test_integration import FakeApp, assert_ack, post_until, register_app, start_fake_bridge

WS_TEST_PORT = 28766


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
                    }
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
        app_port=28767,
    )
    return create_app(settings)


# ------------------------------------------------------------------ registro


def test_app_status_default_unregistered(app):
    with TestClient(app) as client:
        resp = client.get("/api/app")
        assert resp.status_code == 200
        assert resp.text == "no app registered"


def test_app_register_and_status(app):
    with TestClient(app) as client:
        resp = client.post(
            "/api/app/register",
            data={"port": "8123", "host": "127.0.0.1", "name": "minha-app"},
        )
        assert resp.status_code == 200
        assert "8123" in resp.text


def test_app_register_requires_port(app):
    with TestClient(app) as client:
        resp = client.post("/api/app/register", data={})
        assert resp.status_code == 400
        assert "port" in resp.text


def test_app_register_invalid_port(app):
    with TestClient(app) as client:
        for bad in ("abc", "0", "66000"):
            resp = client.post("/api/app/register", data={"port": bad})
            assert resp.status_code == 400
            assert "port" in resp.text


def test_app_register_single_slot_replaces(app):
    with TestClient(app) as client:
        client.post("/api/app/register", data={"port": "8123", "name": "primeiro"})
        client.post("/api/app/register", data={"port": "8124", "name": "segundo"})
        resp = client.get("/api/app")
        assert resp.status_code == 200
        assert "segundo" in resp.text
        assert "8124" in resp.text
        assert "primeiro" not in resp.text


def test_app_unregister(app):
    with TestClient(app) as client:
        client.post("/api/app/register", data={"port": "8123"})
        resp = client.post("/api/app/unregister")
        assert resp.status_code == 200
        assert resp.text == "ok"
        resp = client.get("/api/app")
        assert resp.text == "no app registered"


# ------------------------------------------------------------------ push e2e


def test_push_delivered_to_registered_app(app):
    thread, ctx = start_fake_bridge("gemini", "Resposta da app.")
    try:
        with TestClient(app) as client, FakeApp() as fake_app:
            register_app(client, fake_app, name="consumidora")
            resp = post_until(
                client,
                "/api/chat",
                {"model": "gemini-pro", "role": "user", "content": "Oi"},
            )
            request_id = assert_ack(resp)
            payload = fake_app.wait_payload()
            assert payload is not None, "push não chegou à aplicação registrada"
            assert payload["request_id"] == request_id
            assert payload["model"] == "gemini-pro"
            assert payload["provider"] == "gemini"
            assert payload["endpoint"] == "chat"
            assert payload["text"] == "Resposta da app."
            assert "timestamp" in payload
            assert "error" not in payload
    finally:
        thread.join(timeout=10)


def test_push_error_still_delivered(app):
    thread, ctx = start_fake_bridge("gemini", "irrelevante", error="falha na injeção")
    try:
        with TestClient(app) as client, FakeApp() as fake_app:
            register_app(client, fake_app)
            resp = post_until(
                client,
                "/api/chat",
                {"model": "gemini-pro", "role": "user", "content": "Oi"},
            )
            assert_ack(resp)
            payload = fake_app.wait_payload()
            assert payload is not None, "erro de geração não foi reportado à app"
            assert payload["text"] == ""
            assert payload["error"] == "falha na injeção"
    finally:
        thread.join(timeout=10)


def test_no_app_registered_best_effort(app):
    """Sem app registrada, o host tenta a porta padrão (fechada) e aceita a
    requisição normalmente — best-effort nunca afeta o chamador."""
    thread, ctx = start_fake_bridge("gemini", "Resposta.")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {"model": "gemini-pro", "role": "user", "content": "Oi"},
            )
            request_id = assert_ack(resp)
            assert request_id
    finally:
        thread.join(timeout=10)