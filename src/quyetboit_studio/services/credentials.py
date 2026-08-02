from __future__ import annotations

from typing import Protocol

from keyring.errors import KeyringError

from ..core.models import PipelineError


class CredentialBackend(Protocol):
    def set_password(self, service: str, account: str, password: str) -> None: ...
    def get_password(self, service: str, account: str) -> str | None: ...
    def delete_password(self, service: str, account: str) -> None: ...


class SecureValueStore:
    """Small wrapper around the operating-system credential vault."""

    SERVICE = "QuyetBoITStudio"

    def __init__(self, backend: CredentialBackend | None = None) -> None:
        if backend is None:
            try:
                import keyring
            except ImportError as exc:
                raise PipelineError("keyring_missing", "Chưa cài keyring.") from exc
            backend = keyring
        self.backend = backend

    def save(self, name: str, value: str) -> None:
        try:
            self.backend.set_password(self.SERVICE, name, value)
        except (KeyringError, RuntimeError) as exc:
            raise PipelineError(
                "credential_store_failed", "Không thể lưu credential vào Windows Credential Manager.",
                debug_detail=type(exc).__name__,
            ) from exc

    def load(self, name: str) -> str:
        try:
            return self.backend.get_password(self.SERVICE, name) or ""
        except (KeyringError, RuntimeError):
            return ""

    def delete(self, name: str) -> None:
        try:
            self.backend.delete_password(self.SERVICE, name)
        except (KeyringError, RuntimeError):
            return
