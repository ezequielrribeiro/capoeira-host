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
            assert "[SYSTEM]" in payload["systemPrompt"]
            assert "[USER]" in payload["prompt"]
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
            assert "[SYSTEM]" in payload["systemPrompt"]
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


# --------------------------- tool calling simulado (contrato textual) ---------------------------


TOOLS_TEXT = (
    "name=shopping | desc=Consulta/prepara uma compra. | item:string | quantidade:int"
)


def tool_call_line(name, args_text):
    return f"[TOOL_CALL] {name} | {args_text}"


def test_tool_calling_returns_call_lines(app):
    """Com 'tools' presente, se a Web devolver a linha de contrato, o host
    devolve as linhas [TOOL_CALL] como text/plain (prosa removida)."""
    line_response = "Claro! Vou buscar isso pra você.\n" + tool_call_line(
        "shopping", "item=leite | quantidade=2"
    )
    thread, ctx = start_fake_bridge("gemini", line_response)
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "gemini-pro",
                    "tools": TOOLS_TEXT,
                    "role": "user",
                    "content": "Preciso comprar leite.",
                },
            )
            assert resp.status_code == 200
            assert resp.text == tool_call_line("shopping", "item=leite | quantidade=2")
            assert ctx["received"], "bridge não recebeu SEND_PROMPT"
            system = ctx["received"][0]["payload"]["systemPrompt"]
            assert "[TOOLS]" in system
            assert "[TOOL] name=shopping" in system
            assert "[MODE TOOL_CALLING]" in system
    finally:
        thread.join(timeout=10)


def test_tool_calling_parallel_calls(app):
    """Várias linhas [TOOL_CALL] viram chamadas paralelas (uma por linha)."""
    line_response = (
        tool_call_line("shopping", "item=leite")
        + "\n"
        + tool_call_line("shopping", "item=pão | quantidade=3")
    )
    thread, ctx = start_fake_bridge("gemini", line_response)
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "gemini-pro",
                    "tools": TOOLS_TEXT,
                    "role": "user",
                    "content": "Compre leite e pão.",
                },
            )
            assert resp.status_code == 200
            lines = [ln for ln in resp.text.strip().splitlines() if ln]
            assert len(lines) == 2
            assert lines[0].endswith("item=leite")
            assert lines[1].endswith("item=pão | quantidade=3")
    finally:
        thread.join(timeout=10)


def test_tool_calling_line_inside_code_fence(app):
    """Linha [TOOL_CALL] envelopada em code fence ainda é parseada — o render
    markdown arranca os backticks no innerText."""
    line_response = "```text\n" + tool_call_line("shopping", "item=leite") + "\n```"
    thread, ctx = start_fake_bridge("gemini", line_response)
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "gemini-pro",
                    "tools": TOOLS_TEXT,
                    "role": "user",
                    "content": "Compre leite.",
                },
            )
            assert resp.status_code == 200
            assert resp.text == tool_call_line("shopping", "item=leite")
    finally:
        thread.join(timeout=10)


def test_tool_calling_falls_back_to_text(app):
    """Se a Web responder texto (sem linha [TOOL_CALL]), faz fallback em texto."""
    thread, ctx = start_fake_bridge("gemini", "Vou verificar para você.")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "gemini-pro",
                    "tools": TOOLS_TEXT,
                    "role": "user",
                    "content": "Compre leite.",
                },
            )
            assert resp.status_code == 200
            assert resp.text == "Vou verificar para você."
    finally:
        thread.join(timeout=10)


def test_tool_calling_fallback_strips_invalid_lines(app):
    """Linha [TOOL_CALL] inválida (sem nome parseável) é removida do fallback."""
    line_response = (
        "Nunca vou chamar a ferramenta.\n"
        + "[TOOL_CALL]  {bad json}\n"
        + "[TOOL_CALL] shopping {bad json}"
    )
    thread, ctx = start_fake_bridge("gemini", line_response)
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "gemini-pro",
                    "tools": TOOLS_TEXT,
                    "role": "user",
                    "content": "Compre leite.",
                },
            )
            assert resp.status_code == 200
            assert resp.text == "Nunca vou chamar a ferramenta."
    finally:
        thread.join(timeout=10)


def test_tool_result_roundtrip_accepted(app):
    """Role 'tool' + tools mantêm o resultado no transcript (TOOL_RESULT)."""
    thread, ctx = start_fake_bridge("gemini", "Você tem 2 leites.")
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                [
                    ("model", "gemini-pro"),
                    ("tools", TOOLS_TEXT),
                    ("role", "assistant"),
                    ("content", tool_call_line("shopping", "item=leite")),
                    ("role", "tool"),
                    ("content", "Leite comprado"),
                    ("tool_call_id", "call-1"),
                ],
            )
            assert resp.status_code == 200
            assert resp.text == "Você tem 2 leites."
            assert ctx["received"]
            prompt = ctx["received"][0]["payload"]["prompt"]
            assert "[TOOL_RESULT] (call-1)" in prompt
            assert "[TOOL_CALL] shopping | item=leite" in prompt
    finally:
        thread.join(timeout=10)


def test_role_tool_rejected_without_tools(app):
    """Sem 'tools' no request, role 'tool' continua rejeitado (400)."""
    with TestClient(app) as client:
        resp = client.post(
            "/api/chat",
            data={
                "model": "gemini-pro",
                "role": "tool",
                "content": "ok",
                "tool_call_id": "call-1",
            },
        )
        assert resp.status_code == 400
        assert "tools" in resp.text


def test_unknown_role_rejected(app):
    """Role fora do permitido é rejeitado (400)."""
    with TestClient(app) as client:
        resp = client.post(
            "/api/chat",
            data={"model": "gemini-pro", "role": "banana", "content": "oi"},
        )
        assert resp.status_code == 400
        assert "messages" in resp.text


def test_tool_calling_streaming_emits_calls(app):
    """Em streaming com tools, as chamadas aparecem no texto final."""
    line_response = tool_call_line("shopping", "item=leite")
    thread, ctx = start_fake_bridge("gemini", line_response)
    try:
        with TestClient(app) as client:
            resp = post_until(
                client,
                "/api/chat",
                {
                    "model": "gemini-pro",
                    "stream": "true",
                    "tools": TOOLS_TEXT,
                    "role": "user",
                    "content": "Compre leite.",
                },
            )
            assert resp.status_code == 200
            assert resp.text == tool_call_line("shopping", "item=leite")
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