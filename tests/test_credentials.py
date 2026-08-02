from types import SimpleNamespace

from quyetboit_studio.services.capcut import VodUploader
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
