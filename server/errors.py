from __future__ import annotations


class OllamaError(Exception):
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class BadRequest(OllamaError):
    status_code = 400


class ModelNotFound(OllamaError):
    status_code = 404


class BridgeOffline(OllamaError):
    status_code = 503


class QueueFullError(OllamaError):
    status_code = 503


class BridgeTimeout(OllamaError):
    status_code = 504


class BridgeError(OllamaError):
    status_code = 502


class UnsupportedError(OllamaError):
    status_code = 501