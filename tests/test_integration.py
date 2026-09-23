import asyncio
import json
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
        app_port=28767,
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
        app_port=28767,
    )
    return create_app(settings)


class FakeApp:
    """Servidor REST fake da aplicação consumidora.

    Captura os POSTs JSON do contrato de resposta e os expõe em ``payloads``
    (o primeiro a chegar em ``wait_payload``). Registrar ``self.port`` em
    ``POST /api/app/register`` faz o CapoeiraHost entregar a resposta aqui.
    """

    def _make_handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                with fake.lock:
                    fake.payloads.append(json.loads(body.decode("utf-8")))
                self.send_response(200)
                self.end_headers()
                try:
                    self.wfile.write(b"ok")
                except Exception:
                    pass

            def log_message(self, *args):
                pass

        return Handler

    def __enter__(self):
        self.payloads = []
        self.lock = threading.Lock()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=10)

    def wait_payload(self, timeout=POLL_TIMEOUT):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if self.payloads:
                    return self.payloads[0]
            time.sleep(0.05)
        return None


def register_app(client, fake_app, name="test-app"):
    resp = client.post(
        "/api/app/register",
        data={"port": str(fake_app.port), "name": name},
    )
    assert resp.status_code == 200
    return resp


def unregister_app(client):
    resp = client.post("/api/app/unregister")
    assert resp.status_code == 200
    return resp


def start_fake_bridge(
    provider,
    response_text,
    *,
    streaming=False,
    partials=None,
    origin=None,
    transcript=None,
    supports_transcript=True,
    chat_updates=None,
    error=None,
):
    """Simula a extensão do navegador: conecta no WS do bridge, envia HELLO,
    aguarda SEND_PROMPT e devolve parciais (se streaming) + RESPONSE final.
    Encaminha READ_CHAT devolvendo RESPONSE com o ``transcript``, e pode emitar
    ``chat_updates`` (payloads de CHAT_UPDATE) logo após o HELLO.

    ``origin`` permite simular a origem que o navegador envia (ex.: a origem da
    página em um content script, como ``https://gemini.google.com``)."""
    ctx = {
        "received": [],
        "error": None,
        "provider": provider,
        "streaming": streaming,
        "partials": partials or [],
        "response_text": response_text,
        "transcript": transcript or [],
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
                                "supportsTranscript": supports_transcript,
                                "tabTitle": "Teste CapoeiraHost",
                            },
                        }
                    )
                )
                for update in chat_updates or []:
                    await ws.send(
                        json.dumps(
                            {
                                "version": "1.0",
                                "action": "CHAT_UPDATE",
                                "id": str(uuid.uuid4()),
                                "payload": update,
                            }
                        )
                    )
                    await asyncio.sleep(0.05)
                async for raw in ws:
                    msg = json.loads(raw)
                    action = msg.get("action")
                    if action == "READ_CHAT":
                        await ws.send(
                            json.dumps(
                                {
                                    "version": "1.0",
                                    "action": "RESPONSE",
                                    "status": "SUCCESS",
                                    "id": msg["id"],
                                    "payload": {"transcript": ctx["transcript"]},
                                    "error": None,
                                }
                            )
                        )
                        continue
                    if action != "SEND_PROMPT":
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
                    if error is not None:
                        await ws.send(
                            json.dumps(
                                {
                                    "version": "1.0",
                                    "action": "ERROR",
                                    "status": "ERROR",
                                    "id": msg["id"],
                                    "payload": None,
                                    "error": error,
                                }
                            )
                        )
                    else:
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


def post_until(client, path, fields, timeout=POLL_TIMEOUT):
    """Repete o POST enquanto o provider ainda não estiver registrado (503),
    garantindo que o HELLO da extensão fake foi processado pelo bridge."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        if isinstance(fields, list):
            body = urllib.parse.urlencode(fields).encode("utf-8")
            last = client.post(
                path,
                content=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        else:
            last = client.post(path, data=fields)
        if last.status_code != 503:
            return last
        time.sleep(0.05)
    return last


def wait_received(ctx, timeout=POLL_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ctx["received"]:
            return ctx["received"]
        time.sleep(0.05)
    return ctx["received"]


def assert_ack(resp):
    assert resp.status_code == 200
    assert resp.text.startswith("accepted: ")
    assert "text/plain" in resp.headers["content-type"]
    return resp.text.split("accepted: ", 1)[1]


# ------------------------------------------------------------------ smoke


def test_version_ok(app):
    with TestClient(app) as client:
        resp = client.get("/api/version")
        assert resp.status_code == 200
        assert resp.text == "1.0.0"
        assert "text/plain" in resp.headers["content-type"]


def test_tags_lists_profiles(app):
    with TestClient(app) as client:
        resp = client.get("/api/tags")
        assert resp.status_code == 200
        names = {line.split(" | ")[0] for line in resp.text.strip().splitlines()}
        assert names == {"gemini-pro", "claude-sonnet", "copilot-365"}
        assert "provider=gemini" in resp.text


def test_chat_without_bridge_returns_503(app):
    with TestClient(app) as client:
        resp = client.post(
            "/api/chat",
            data={"model": "gemini-pro", "role": "user", "content": "oi"},
        )
        assert resp.status_code == 503
        assert "no bridge available" in resp.text
        assert "text/plain" in resp.headers["content-type"]


# ------------------------------------------------------------------ e2e push (bridge → app)


def test_chat_via_fake_extension(app):
    thread, ctx = start_fake_bridge("gemini", "A capoeira é uma arte afro-brasileira.")
    try:
        with TestClient(app) as client, FakeApp() as fake_app:
            register_app(client, fake_app)
            resp = post_until(
                client,
                "/api/chat",
                {"model": "gemini-pro", "role": "user", "content": "O que é capoeira?"},
            )
            request_id = assert_ack(resp)

            payload = fake_app.wait_payload()
            assert payload is not None, "resposta não foi entregue à aplicação"
            assert payload["request_id"] == request_id
            assert payload["model"] == "gemini-pro"
            assert payload["provider"] == "gemini"
            assert payload["endpoint"] == "chat"
            assert payload["text"] == "A capoeira é uma arte afro-brasileira."
            assert "error" not in payload

            received = wait_received(ctx)
            assert received, "bridge não recebeu SEND_PROMPT"
            spayload = received[0]["payload"]
            assert spayload["systemPrompt"] == "Você é um assistente útil."
            assert "[USER] O que é capoeira?" in spayload["prompt"]
            assert spayload["newChat"] is True
    finally:
        thread.join(timeout=10)


def test_generate_via_fake_extension(app):
    thread, ctx = start_fake_bridge("gemini", "Expliquei a capoeira.")
    try:
        with TestClient(app) as client, FakeApp() as fake_app:
            register_app(client, fake_app)
            resp = post_until(
                client,
                "/api/generate",
                {"model": "gemini-pro", "prompt": "O que é capoeira?"},
            )
            request_id = assert_ack(resp)

            payload = fake_app.wait_payload()
            assert payload is not None, "resposta não foi entregue à aplicação"
            assert payload["request_id"] == request_id
            assert payload["endpoint"] == "generate"
            assert payload["text"] == "Expliquei a capoeira."

            received = wait_received(ctx)
            assert received, "bridge não recebeu SEND_PROMPT"
            spayload = received[0]["payload"]
            assert spayload["systemPrompt"] == "Você é um assistente útil."
            assert "[SYSTEM]" not in spayload["systemPrompt"]
            assert spayload["newChat"] is True
    finally:
        thread.join(timeout=10)


def test_streaming_plain_text_pushed_complete(app):
    thread, ctx = start_fake_bridge(
        "claude",
        "A capoeira é uma arte.",
        streaming=True,
        partials=["A capoeira ", "é uma arte."],
    )
    try:
        with TestClient(app) as client, FakeApp() as fake_app:
            register_app(client, fake_app)
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "claude-sonnet",
                    "stream": "true",
                    "role": "user",
                    "content": "O que é capoeira?",
                },
            )
            assert_ack(resp)
            payload = fake_app.wait_payload()
            assert payload is not None
            assert payload["text"] == "A capoeira é uma arte."
            assert payload["stream"] is True
            assert wait_received(ctx), "bridge não recebeu SEND_PROMPT"
    finally:
        thread.join(timeout=10)


# --------------------------------------------- validação de origem (CSWSH) --


def test_bridge_accepts_web_provider_origin(app):
    thread, ctx = start_fake_bridge(
        "gemini",
        "A capoeira é uma arte afro-brasileira.",
        origin="https://gemini.google.com",
    )
    try:
        with TestClient(app) as client, FakeApp() as fake_app:
            register_app(client, fake_app)
            resp = post_until(
                client,
                "/api/chat",
                {"model": "gemini-pro", "role": "user", "content": "oi"},
            )
            assert_ack(resp)
            assert wait_received(ctx), "bridge rejeitou a conexão com origem de página"
            assert ctx["error"] is None
    finally:
        thread.join(timeout=10)


def test_bridge_rejects_unknown_origin(app):
    thread, ctx = start_fake_bridge(
        "gemini",
        "nunca deve chegar",
        origin="https://evil.example",
    )
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/chat",
                data={"model": "gemini-pro", "role": "user", "content": "oi"},
            )
            assert resp.status_code == 503
        thread.join(timeout=10)
        assert ctx["error"], "conexão de origem desconhecida deveria ter sido rejeitada"
        assert not ctx["received"]
    except Exception:
        thread.join(timeout=10)
        raise


# --------------------------- Copilot 365 (m365.cloud.microsoft) ----- ------


def test_bridge_accepts_m365_copilot_origin(app):
    thread, ctx = start_fake_bridge(
        "copilot365",
        "Sou o Copilot da Microsoft 365.",
        origin="https://m365.cloud.microsoft",
    )
    try:
        with TestClient(app) as client, FakeApp() as fake_app:
            register_app(client, fake_app)
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "copilot-365",
                    "role": "user",
                    "content": "Quem é você?",
                },
            )
            assert_ack(resp)
            assert wait_received(ctx), "bridge rejeitou a origem de m365.cloud.microsoft"
            assert ctx["error"] is None
    finally:
        thread.join(timeout=10)


def test_chat_via_copilot365_bridge(app):
    thread, ctx = start_fake_bridge("copilot365", "Resposta do Copilot 365.")
    try:
        with TestClient(app) as client, FakeApp() as fake_app:
            register_app(client, fake_app)
            resp = post_until(
                client,
                "/api/chat",
                {"model": "copilot-365", "role": "user", "content": "Oi Copilot"},
            )
            assert_ack(resp)
            payload = fake_app.wait_payload()
            assert payload is not None
            assert payload["text"] == "Resposta do Copilot 365."
            received = wait_received(ctx)
            assert received
            spayload = received[0]["payload"]
            assert spayload["provider"] == "copilot365"
            assert spayload["newChat"] is True
    finally:
        thread.join(timeout=10)


# --------------------------- reutilização de chat (new_chat) ----- ----------


def test_chat_new_chat_false_from_global_default(app_reuse_chat):
    thread, ctx = start_fake_bridge("gemini", "Resposta sem novo chat.")
    try:
        with TestClient(app_reuse_chat) as client:
            resp = post_until(
                client,
                "/api/chat",
                {"model": "gemini-pro", "role": "user", "content": "oi"},
            )
            assert_ack(resp)
            received = wait_received(ctx)
            assert received
            assert received[0]["payload"]["newChat"] is False
    finally:
        thread.join(timeout=10)


def test_chat_new_chat_false_via_request_override(app):
    thread, ctx = start_fake_bridge("gemini", "Resposta sem novo chat.")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "gemini-pro",
                    "new_chat": "false",
                    "role": "user",
                    "content": "oi",
                },
            )
            assert_ack(resp)
            received = wait_received(ctx)
            assert received
            assert received[0]["payload"]["newChat"] is False
    finally:
        thread.join(timeout=10)


def test_chat_new_chat_true_overrides_global_false(app_reuse_chat):
    thread, ctx = start_fake_bridge("gemini", "Resposta com novo chat.")
    try:
        with TestClient(app_reuse_chat) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "gemini-pro",
                    "new_chat": "true",
                    "role": "user",
                    "content": "oi",
                },
            )
            assert_ack(resp)
            received = wait_received(ctx)
            assert received
            assert received[0]["payload"]["newChat"] is True
    finally:
        thread.join(timeout=10)


def test_generate_new_chat_false_via_request_override(app):
    thread, ctx = start_fake_bridge("gemini", "Resposta sem novo chat.")
    try:
        with TestClient(app) as client, FakeApp() as fake_app:
            register_app(client, fake_app)
            resp = post_until(
                client,
                "/api/generate",
                {
                    "model": "gemini-pro",
                    "prompt": "oi",
                    "new_chat": "false",
                },
            )
            assert_ack(resp)
            received = wait_received(ctx)
            assert received
            assert received[0]["payload"]["newChat"] is False
    finally:
        thread.join(timeout=10)


# --------------------------- pass-through verbatim (sem tags do host) ------


def test_chat_system_verbatim_without_tags(app):
    """O system prompt vai verbatim, sem [SYSTEM]/[OPTIONS]/[TOOLS]."""
    thread, ctx = start_fake_bridge("gemini", "Resposta.")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {"model": "gemini-pro", "role": "user", "content": "X"},
            )
            assert_ack(resp)
            received = wait_received(ctx)
            assert received, "bridge não recebeu SEND_PROMPT"
            system = received[0]["payload"]["systemPrompt"]
            assert system == "Você é um assistente útil."
            assert "[SYSTEM]" not in system
            assert "[OPTIONS]" not in system
            assert "[TOOLS]" not in system
            assert "[MODE TOOL_CALLING]" not in system
    finally:
        thread.join(timeout=10)


def test_chat_transcript_keeps_user_assistant_labels(app):
    """O transcript mantém [USER]/[ASSISTANT] no prompt enviado à Web."""
    thread, ctx = start_fake_bridge("gemini", "Resposta.")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                [
                    ("model", "gemini-pro"),
                    ("role", "user"),
                    ("content", "Quem foi Besouro?"),
                    ("role", "assistant"),
                    ("content", "Uma lenda."),
                ],
            )
            assert_ack(resp)
            received = wait_received(ctx)
            assert received
            prompt = received[0]["payload"]["prompt"]
            assert "[USER] Quem foi Besouro?" in prompt
            assert "[ASSISTANT] Uma lenda." in prompt
    finally:
        thread.join(timeout=10)


def test_response_verbatim_no_tool_parsing(app):
    """A resposta é entregue à aplicação verbatim, sem extração de [TOOL_CALL]."""
    line_response = "Vou buscar isso.\n[TOOL_CALL] shopping | item=leite"
    thread, ctx = start_fake_bridge("gemini", line_response)
    try:
        with TestClient(app) as client, FakeApp() as fake_app:
            register_app(client, fake_app)
            resp = post_until(
                client,
                "/api/chat",
                {"model": "gemini-pro", "role": "user", "content": "Compre leite."},
            )
            assert_ack(resp)
            payload = fake_app.wait_payload()
            assert payload is not None
            assert payload["text"] == line_response
    finally:
        thread.join(timeout=10)


def test_role_tool_rejected(app):
    """role 'tool' deixou de ser suportado (tool calling removido do host)."""
    with TestClient(app) as client:
        resp = client.post(
            "/api/chat",
            data={
                "model": "gemini-pro",
                "role": "tool",
                "content": "ok",
            },
        )
        assert resp.status_code == 400
        assert "messages" in resp.text


def test_unknown_role_rejected(app):
    """Role fora do permitido é rejeitado (400)."""
    with TestClient(app) as client:
        resp = client.post(
            "/api/chat",
            data={"model": "gemini-pro", "role": "banana", "content": "oi"},
        )
        assert resp.status_code == 400
        assert "messages" in resp.text


def test_chat_streaming_verbatim(app):
    """Em streaming sem tools, o texto chega verbatim via push."""
    thread, ctx = start_fake_bridge(
        "gemini",
        "A capoeira é uma arte.",
        streaming=True,
        partials=["A capoeira ", "é uma arte."],
    )
    try:
        with TestClient(app) as client, FakeApp() as fake_app:
            register_app(client, fake_app)
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "gemini-pro",
                    "stream": "true",
                    "role": "user",
                    "content": "O que é capoeira?",
                },
            )
            assert_ack(resp)
            payload = fake_app.wait_payload()
            assert payload is not None
            assert payload["text"] == "A capoeira é uma arte."
            assert wait_received(ctx), "bridge não recebeu SEND_PROMPT"
    finally:
        thread.join(timeout=10)


# --------------------------- read do chat -----------------------------------


TRANSCRIPT = [
    {"role": "user", "content": "Quem foi Besouro Mangangá?"},
    {"role": "assistant", "content": "Uma lenda da capoeira do recôncavo baiano."},
]


def test_chat_read_returns_transcript(app):
    thread, ctx = start_fake_bridge("gemini", "irrelevante", transcript=TRANSCRIPT)
    try:
        with TestClient(app) as client:
            resp = post_until(client, "/api/chat/read", {"model": "gemini-pro"})
            assert resp.status_code == 200
            assert "text/plain" in resp.headers["content-type"]
            assert "[USER] Quem foi Besouro Mangangá?" in resp.text
            assert "[ASSISTANT] Uma lenda da capoeira do recôncavo baiano." in resp.text
    finally:
        thread.join(timeout=10)


def test_chat_read_requires_model(app):
    thread, ctx = start_fake_bridge("gemini", "irrelevante", transcript=TRANSCRIPT)
    try:
        with TestClient(app) as client:
            resp = post_until(client, "/api/chat/read", {})
            assert resp.status_code == 400
            assert "model" in resp.text
    finally:
        thread.join(timeout=10)


def test_chat_read_offline_503(app):
    with TestClient(app) as client:
        resp = client.post("/api/chat/read", data={"model": "gemini-pro"})
        assert resp.status_code == 503
        assert "no bridge available" in resp.text


def test_chat_read_no_transcript_support_501(app):
    thread, ctx = start_fake_bridge("gemini", "x", supports_transcript=False)
    try:
        with TestClient(app) as client:
            resp = post_until(client, "/api/chat/read", {"model": "gemini-pro"})
            assert resp.status_code == 501
            assert "transcript" in resp.text
    finally:
        thread.join(timeout=10)


# --------------------------- validação de form -------------------------------


def test_generate_requires_prompt(app):
    with TestClient(app) as client:
        resp = client.post("/api/generate", data={"model": "gemini-pro"})
        assert resp.status_code == 400
        assert "prompt" in resp.text


def test_create_show_delete(app):
    with TestClient(app) as client:
        resp = client.post(
            "/api/create",
            data={"model": "meu-perfil", "from": "gemini", "parameter.temperature": "0.5"},
        )
        assert resp.status_code == 200
        assert resp.text == "ok"

        resp = client.post("/api/show", data={"model": "meu-perfil"})
        assert resp.status_code == 200
        assert "model: meu-perfil" in resp.text
        assert "provider: gemini" in resp.text

        resp = client.request("DELETE", "/api/delete", data={"model": "meu-perfil"})
        assert resp.status_code == 200
        assert resp.text == "ok"

        resp = client.post("/api/show", data={"model": "meu-perfil"})
        assert resp.status_code == 404