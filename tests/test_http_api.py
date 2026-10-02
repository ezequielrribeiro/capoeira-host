import json

import pytest
from fastapi.testclient import TestClient

from server.config import Settings
from server.http_api import _parse_messages, _transcript_to_messages
from server.main import create_app

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
                        "provider": "gemini",
                        "system_prompt": "Você é um assistente útil.",
                        "template": "{{ .System }}\n\n{{ .Prompt }}",
                        "streaming": False,
                        "options": {},
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
    return create_app(
        Settings(
            host="127.0.0.1",
            ws_port=WS_TEST_PORT,
            models_file=models_file,
            timeout=5.0,
            queue_size=10,
        )
    )


class _Form:
    """Stub mínimo de FormData com pares repetidos."""

    def __init__(self, pairs):
        self._pairs = pairs

    def multi_items(self):
        return list(self._pairs)

    def get(self, key):
        for k, v in self._pairs:
            if k == key:
                return v
        return None


def test_parse_messages_pairs_role_content():
    form = _Form([("model", "gemini-pro"), ("role", "user"), ("content", "Oi")])
    msgs = _parse_messages(form)
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].content == "Oi"


def test_parse_messages_multiple_turns():
    form = _Form(
        [
            ("role", "user"),
            ("content", "Quem foi Besouro?"),
            ("role", "assistant"),
            ("content", "Uma lenda."),
        ]
    )
    msgs = _parse_messages(form)
    assert [(m.role, m.content) for m in msgs] == [
        ("user", "Quem foi Besouro?"),
        ("assistant", "Uma lenda."),
    ]


def test_parse_messages_drops_unknown_role():
    form = _Form([("role", "tool"), ("content", "x"), ("role", "user"), ("content", "y")])
    msgs = _parse_messages(form)
    assert [(m.role, m.content) for m in msgs] == [("user", "y")]


def test_transcript_helpers_build_labels():
    msgs = _transcript_to_messages(
        [{"role": "user", "content": "Oi"}, {"role": "assistant", "content": "Olá"}]
    )
    labels = [f"[{m.role.upper()}] {m.content}" for m in msgs]
    assert labels == ["[USER] Oi", "[ASSISTANT] Olá"]
