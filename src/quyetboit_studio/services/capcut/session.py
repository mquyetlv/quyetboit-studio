from __future__ import annotations

import json

from keyring.errors import KeyringError

from ...core.models import PipelineError
from .auth import CapCutSession
from .config import AUTH_COOKIES


class CapCutSessionStore:
    SERVICE = "QuyetBoITStudio.CapCut"

    def __init__(self, account: str = "default", backend=None) -> None:
        self.account = account
        if backend is None:
            try:
                import keyring
            except ImportError as exc:
                raise PipelineError("keyring_missing", "Chưa cài keyring.", provider="capcut") from exc
            backend = keyring
        self.backend = backend

    def save(self, session: CapCutSession) -> None:
        payload = {"cookies": {key: value for key, value in session.cookies.items() if key in AUTH_COOKIES},
                   "did": session.did, "uid": session.uid, "workspace_id": session.workspace_id}
        self.backend.set_password(self.SERVICE, self.account, json.dumps(payload, separators=(",", ":")))

    def load(self) -> CapCutSession | None:
        raw = self.backend.get_password(self.SERVICE, self.account)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            session = CapCutSession(**payload)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        return session if session.has_auth else None

    def delete(self) -> None:
        try:
            self.backend.delete_password(self.SERVICE, self.account)
        except (KeyringError, RuntimeError):
            return
