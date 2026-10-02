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


def start_fake_bridge(
    provider,
    response_text,
    *,
    streaming=False,
    partials=None,
    origin=None,
    transcript=None,
    supports_transcript=True,
    error=None,
):
    """Simula a extensão do navegador: conecta no WS do bridge, envia HELLO,
    aguarda SEND_PROMPT e devolve parciais (se streaming) + RESPONSE final.
    Encaminha READ_CHAT devolvendo RESPONSE com o ``transcript``.

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
        "ready": False,
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
                ctx["ready"] = True
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
        if isinstance(fields, str):
            last = client.post(
                path,
                content=fields,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        else:
            last = client.post(path, data=fields)
        if last.status_code not in (503, 504):
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


def wait_ready(ctx, timeout=POLL_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ctx.get("ready"):
            return True
        time.sleep(0.02)
    return ctx.get("ready", False)


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


def test_app_endpoints_removed(app):
    """O host não registra mais a aplicação consumidora (unidirecional)."""
    with TestClient(app) as client:
        assert client.get("/api/app").status_code == 404
        assert client.post("/api/app/register", data={"port": "8123"}).status_code == 404
        assert client.post("/api/app/unregister").status_code == 404


# ------------------------------------------------------------------ e2e (bridge → chamador)


def test_chat_via_fake_extension_returns_text(app):
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

            received = wait_received(ctx)
            assert received, "bridge não recebeu SEND_PROMPT"
            spayload = received[0]["payload"]
            assert spayload["systemPrompt"] == "Você é um assistente útil."
            assert "[USER] O que é capoeira?" in spayload["prompt"]
            assert spayload["newChat"] is True
    finally:
        thread.join(timeout=10)


def test_generate_via_fake_extension_returns_text(app):
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

            received = wait_received(ctx)
            assert received, "bridge não recebeu SEND_PROMPT"
            spayload = received[0]["payload"]
            assert spayload["systemPrompt"] == "Você é um assistente útil."
            assert "[SYSTEM]" not in spayload["systemPrompt"]
            assert spayload["newChat"] is True
    finally:
        thread.join(timeout=10)


def test_generate_error_returns_502(app):
    thread, ctx = start_fake_bridge("gemini", "irrelevante", error="falha na injeção")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/generate",
                {"model": "gemini-pro", "prompt": "oi"},
            )
            assert resp.status_code == 502
            assert "falha na injeção" in resp.text
    finally:
        thread.join(timeout=10)


def test_generate_timeout_returns_504(models_file):
    """Bridge conectado mas que nunca responde → 504 (timeout síncrono)."""
    settings = Settings(
        host="127.0.0.1",
        ws_port=WS_TEST_PORT,
        models_file=models_file,
        timeout=1.0,
        queue_size=10,
    )
    app = create_app(settings)
    thread = threading.Thread(target=_silent_bridge, name="silent-bridge", daemon=True)
    thread.start()
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/generate",
                {"model": "gemini-pro", "prompt": "oi"},
            )
            assert resp.status_code == 504
    finally:
        thread.join(timeout=10)


def _silent_bridge():
    async def _run():
        try:
            async with websockets.connect(f"ws://127.0.0.1:{WS_TEST_PORT}") as ws:
                await ws.send(
                    json.dumps(
                        {
                            "version": "1.0",
                            "action": "HELLO",
                            "id": str(uuid.uuid4()),
                            "payload": {
                                "provider": "gemini",
                                "adapters": ["gemini"],
                                "supportsNewChat": True,
                                "supportsTranscript": True,
                            },
                        }
                    )
                )
                async for _ in ws:
                    await asyncio.sleep(60)
        except Exception:
            pass

    asyncio.run(_run())


def test_streaming_partials_not_duplicated(app):
    """Com supportsStreaming, os parciais e o RESPONSE final não duplicam texto."""
    thread, ctx = start_fake_bridge(
        "claude",
        "Olá mundo!",
        streaming=True,
        partials=["Olá ", "mundo!"],
    )
    try:
        with TestClient(app) as client:
            assert wait_ready(ctx), "bridge fake não conectou"
            time.sleep(0.2)
            resp = post_until(
                client,
                "/api/chat",
                "model=claude-sonnet&role=user&content=oi",
            )
            assert resp.status_code == 200, f"ctx.error={ctx['error']!r} ready={ctx['ready']}"
            assert resp.text == "Olá mundo!"
    finally:
        thread.join(timeout=10)


# --------------------------------------------- read chat


def test_chat_read_returns_transcript(app):
    thread, ctx = start_fake_bridge(
        "gemini",
        "x",
        transcript=[
            {"role": "user", "content": "Quem foi Besouro?"},
            {"role": "assistant", "content": "Uma lenda."},
        ],
    )
    try:
        with TestClient(app) as client:
            deadline = time.monotonic() + POLL_TIMEOUT
            last = None
            while time.monotonic() < deadline:
                last = client.post("/api/chat/read", data={"model": "gemini-pro"})
                if last.status_code == 200:
                    break
                time.sleep(0.05)
            assert last.status_code == 200
            assert "[USER] Quem foi Besouro?" in last.text
            assert "[ASSISTANT] Uma lenda." in last.text
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
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {"model": "gemini-pro", "role": "user", "content": "oi"},
            )
            assert resp.status_code == 200
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
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {"model": "copilot-365", "role": "user", "content": "Quem é você?"},
            )
            assert resp.status_code == 200
            assert wait_received(ctx), "bridge rejeitou a origem de m365.cloud.microsoft"
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
            assert resp.status_code == 200
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
            assert resp.status_code == 200
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
            assert resp.status_code == 200
            received = wait_received(ctx)
            assert received
            assert received[0]["payload"]["newChat"] is True
    finally:
        thread.join(timeout=10)


def test_generate_new_chat_false_via_request_override(app):
    thread, ctx = start_fake_bridge("gemini", "Resposta sem novo chat.")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/generate",
                {"model": "gemini-pro", "prompt": "oi", "new_chat": "false"},
            )
            assert resp.status_code == 200
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
            assert resp.status_code == 200
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


def test_chat_transcript_keeps_user_assistant_labels():
    """O transcript mantém [USER]/[ASSISTANT] no prompt enviado à Web
    (a fiação form→messages é coberta em tests/test_http_api.py)."""
    from server.gateway import Gateway
    from server.prompt_builder import build_prompt_payload, build_chat_transcript
    from server.registry import Profile

    class _Req:
        messages = [
            type("M", (), {"role": "user", "content": "Quem foi Besouro?"})(),
            type("M", (), {"role": "assistant", "content": "Uma lenda."})(),
        ]

    prompt = build_chat_transcript(_Req.messages)
    assert "[USER] Quem foi Besouro?" in prompt
    assert "[ASSISTANT] Uma lenda." in prompt


def test_response_verbatim_no_tool_parsing(app):
    """A resposta é devolvida verbatim ao chamador, sem extração de [TOOL_CALL]."""
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
