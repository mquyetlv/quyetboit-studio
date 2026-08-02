from types import SimpleNamespace

import httpx

from quyetboit_studio.services.capcut import (
    CapCutEditClient,
    CapCutSession,
    VodUploader,
)
from quyetboit_studio.services.credentials import SecureValueStore


class MemoryBackend:
    def __init__(self):
        self.values = {}

    def set_password(self, service, account, password):
        self.values[(service, account)] = password

    def get_password(self, service, account):
        return self.values.get((service, account))

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


def test_secure_value_store_roundtrip():
    backend = MemoryBackend()
    store = SecureValueStore(backend)
    store.save("gemini_api_key", "secret")
    assert store.load("gemini_api_key") == "secret"
    store.delete("gemini_api_key")
    assert store.load("gemini_api_key") == ""


def test_uploader_validates_session_before_prepare(tmp_path):
    source = tmp_path / "audio.m4a"
    source.write_bytes(b"audio")

    class Client:
        def __init__(self):
            self.session = SimpleNamespace(workspace_id="", uid="")
            self.validated = False

        def ensure_session(self):
            self.validated = True
            self.session.workspace_id = "workspace"

        def request(self, method, path, **kwargs):
            assert self.validated
            assert kwargs["json"]["workspace_id"] == "workspace"
            return {"data": {"security_token": {
                "access_key_id": "ak", "secret_access_key": "sk", "session_token": "token",
            }, "upload_domain": "upload.test", "service_id": "space"}}

    client = Client()
    data = VodUploader(client).prepare(source, "md5")
    assert data["service_id"] == "space"


def test_restored_capcut_session_is_validated_once_and_persisted():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/passport/web/account/info/":
            return httpx.Response(200, json={"data": {"user_id_str": "fresh-uid"}})
        if request.url.path == "/cc/v1/workspace/get_user_workspaces":
            return httpx.Response(200, json={"data": {"workspace_list": [
                {"workspace_id": "fresh-workspace", "role": "owner"},
            ]}})
        raise AssertionError(f"Unexpected request: {request.url}")

    class Store:
        def __init__(self):
            self.saved = []

        def save(self, session):
            self.saved.append((session.uid, session.workspace_id))

    session = CapCutSession(
        {"sessionid": "test-session"},
        "test-did",
        uid="stale-uid",
        workspace_id="stale-workspace",
    )
    store = Store()
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = CapCutEditClient(session, client=http, session_store=store)

    first = client.ensure_session()
    second = client.ensure_session()

    assert first is second is session
    assert (session.uid, session.workspace_id) == ("fresh-uid", "fresh-workspace")
    assert store.saved == [("fresh-uid", "fresh-workspace")]
    assert calls == [
        "/passport/web/account/info/",
        "/cc/v1/workspace/get_user_workspaces",
    ]
