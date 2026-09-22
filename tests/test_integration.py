import asyncio
import json
import threading
import time
import urllib.parse
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


def watch_until(client, path, fields, timeout=POLL_TIMEOUT):
    """Repete o POST até a resposta vir 200 com texto não vazio, ou o provider
    responder erro definitivo (400/501); 503 (ainda conectando) e 200 com corpo
    vazio (sem update) fazem manter o loop até o timeout."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.post(path, data=fields)
        if last.status_code not in (200, 503):
            return last
        if last.status_code == 200 and last.text.strip():
            return last
        time.sleep(0.1)
    return last


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


# ------------------------------------------------------------------ e2e bridge


def test_chat_via_fake_extension(app):
    thread, ctx = start_fake_bridge("gemini", "A capoeira é uma arte afro-brasileira.")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {"model": "gemini-pro", "role": "user", "content": "O que é capoeira?"},
            )
            assert resp.status_code == 200
            assert resp.text == "A capoeira é uma arte afro-brasileira."
            assert "text/plain" in resp.headers["content-type"]

            assert ctx["received"], "bridge não recebeu SEND_PROMPT"
            payload = ctx["received"][0]["payload"]
            assert payload["systemPrompt"] == "Você é um assistente útil."
            assert "[USER] O que é capoeira?" in payload["prompt"]
            assert payload["newChat"] is True
    finally:
        thread.join(timeout=10)


def test_generate_via_fake_extension(app):
    thread, ctx = start_fake_bridge("gemini", "Expliquei a capoeira.")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/generate",
                {"model": "gemini-pro", "prompt": "O que é capoeira?"},
            )
            assert resp.status_code == 200
            assert resp.text == "Expliquei a capoeira."
            assert ctx["received"], "bridge não recebeu SEND_PROMPT"
            payload = ctx["received"][0]["payload"]
            assert payload["systemPrompt"] == "Você é um assistente útil."
            assert "[SYSTEM]" not in payload["systemPrompt"]
            assert payload["newChat"] is True
    finally:
        thread.join(timeout=10)


def test_streaming_plain_text_via_fake_extension(app):
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
                    "stream": "true",
                    "role": "user",
                    "content": "O que é capoeira?",
                },
            )
            assert resp.status_code == 200
            assert "text/plain" in resp.headers["content-type"]
            assert resp.text == "A capoeira é uma arte."
            assert ctx["received"], "bridge não recebeu SEND_PROMPT"
    finally:
        thread.join(timeout=10)


# --------------------------- validação de origem (CSWSH) ---------------------------


def test_bridge_accepts_web_provider_origin(app):
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
                {"model": "gemini-pro", "role": "user", "content": "oi"},
            )
            assert resp.status_code == 200
            assert resp.text == "A capoeira é uma arte afro-brasileira."
            assert ctx["received"], "bridge rejeitou a conexão com origem de página"
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
                    "role": "user",
                    "content": "Quem é você?",
                },
            )
            assert resp.status_code == 200
            assert resp.text == "Sou o Copilot da Microsoft 365."
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
                {"model": "copilot-365", "role": "user", "content": "Oi Copilot"},
            )
            assert resp.status_code == 200
            assert resp.text == "Resposta do Copilot 365."
            assert ctx["received"]
            payload = ctx["received"][0]["payload"]
            assert payload["provider"] == "copilot365"
            assert payload["newChat"] is True
    finally:
        thread.join(timeout=10)


# --------------------------- reutilização de chat (new_chat) ---------------------------


def test_chat_new_chat_false_from_global_default(app_reuse_chat):
    thread, ctx = start_fake_bridge("gemini", "Resposta sem novo chat.")
    try:
        with TestClient(app_reuse_chat) as client:
            resp = post_until(
                client,
                "/api/chat",
                {"model": "gemini-pro", "role": "user", "content": "oi"},
            )
            assert resp.status_code == 200
            assert ctx["received"]
            assert ctx["received"][0]["payload"]["newChat"] is False
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
            assert resp.status_code == 200
            assert ctx["received"]
            assert ctx["received"][0]["payload"]["newChat"] is False
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
            assert resp.status_code == 200
            assert ctx["received"]
            assert ctx["received"][0]["payload"]["newChat"] is True
    finally:
        thread.join(timeout=10)


def test_generate_new_chat_false_via_request_override(app):
    thread, ctx = start_fake_bridge("gemini", "Resposta sem novo chat.")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/generate",
                {
                    "model": "gemini-pro",
                    "prompt": "oi",
                    "new_chat": "false",
                },
            )
            assert resp.status_code == 200
            assert resp.text == "Resposta sem novo chat."
            assert ctx["received"]
            assert ctx["received"][0]["payload"]["newChat"] is False
    finally:
        thread.join(timeout=10)


# --------------------------- pass-through verbatim (sem tags do host) ---------------------------


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
            assert resp.status_code == 200
            assert ctx["received"], "bridge não recebeu SEND_PROMPT"
            system = ctx["received"][0]["payload"]["systemPrompt"]
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
            assert resp.status_code == 200
            assert ctx["received"]
            prompt = ctx["received"][0]["payload"]["prompt"]
            assert "[USER] Quem foi Besouro?" in prompt
            assert "[ASSISTANT] Uma lenda." in prompt
    finally:
        thread.join(timeout=10)


def test_response_verbatim_no_tool_parsing(app):
    """A resposta é devolvida verbatim, sem extração/strip de [TOOL_CALL]."""
    line_response = "Vou buscar isso.\n[TOOL_CALL] shopping | item=leite"
    thread, ctx = start_fake_bridge("gemini", line_response)
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {"model": "gemini-pro", "role": "user", "content": "Compre leite."},
            )
            assert resp.status_code == 200
            assert resp.text == line_response
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
    """Em streaming sem tools, o texto chega verbatim pelos chunks."""
    thread, ctx = start_fake_bridge(
        "gemini",
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
                    "model": "gemini-pro",
                    "stream": "true",
                    "role": "user",
                    "content": "O que é capoeira?",
                },
            )
            assert resp.status_code == 200
            assert resp.text == "A capoeira é uma arte."
            assert ctx["received"], "bridge não recebeu SEND_PROMPT"
    finally:
        thread.join(timeout=10)


# --------------------------- read / watch do chat ---------------------------


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


def test_chat_watch_returns_delta(app):
    update = {
        "revision": 1,
        "transcript": TRANSCRIPT,
        "messages": TRANSCRIPT,
    }
    thread, ctx = start_fake_bridge("gemini", "x", chat_updates=[update])
    try:
        with TestClient(app) as client:
            resp = watch_until(
                client, "/api/chat/watch", {"model": "gemini-pro", "revision": "0"}
            )
            assert resp.status_code == 200
            assert resp.text.strip() == (
                "[USER] Quem foi Besouro Mangangá?\n"
                "[ASSISTANT] Uma lenda da capoeira do recôncavo baiano."
            )
            assert resp.headers.get("X-Capoeira-Revision") == "1"
    finally:
        thread.join(timeout=10)


def test_chat_watch_revision_filters_old_events(app):
    update = {
        "revision": 3,
        "transcript": TRANSCRIPT,
        "messages": TRANSCRIPT,
    }
    thread, ctx = start_fake_bridge("gemini", "x", chat_updates=[update])
    try:
        with TestClient(app) as client:
            resp = watch_until(
                client,
                "/api/chat/watch",
                {"model": "gemini-pro", "revision": "3"},
                timeout=2.0,
            )
            assert resp.status_code == 200
            assert resp.text.strip() == ""
    finally:
        thread.join(timeout=10)


def test_chat_watch_timeout_returns_empty(app):
    thread, ctx = start_fake_bridge("gemini", "x")
    try:
        with TestClient(app) as client:
            resp = watch_until(
                client,
                "/api/chat/watch",
                {"model": "gemini-pro", "revision": "0", "timeout": "0.5"},
                timeout=2.0,
            )
            assert resp.status_code == 200
            assert resp.text.strip() == ""
    finally:
        thread.join(timeout=10)


def test_chat_watch_rejects_bad_revision(app):
    thread, ctx = start_fake_bridge("gemini", "x")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client, "/api/chat/watch", {"model": "gemini-pro", "revision": "abc"}
            )
            assert resp.status_code == 400
    finally:
        thread.join(timeout=10)


def test_chat_watch_offline_503(app):
    with TestClient(app) as client:
        resp = client.post("/api/chat/watch", data={"model": "gemini-pro"})
        assert resp.status_code == 503


def test_chat_watch_no_transcript_support_501(app):
    thread, ctx = start_fake_bridge("gemini", "x", supports_transcript=False)
    try:
        with TestClient(app) as client:
            resp = post_until(
                client, "/api/chat/watch", {"model": "gemini-pro", "revision": "0"}
            )
            assert resp.status_code == 501
            assert "transcript" in resp.text
    finally:
        thread.join(timeout=10)


# --------------------------- validação de form ---------------------------


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