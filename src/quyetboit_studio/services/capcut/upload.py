from __future__ import annotations

import hashlib
import hmac
import json
import random
import string
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import httpx

from ...core.models import CancellationToken, PipelineError
from .client import CapCutEditClient
from .config import APPVR_ASSET, USER_AGENT

PART_SIZE = 3 * 1024 * 1024


def crc32_hex(data: bytes) -> str:
    return format(zlib.crc32(data) & 0xFFFFFFFF, "08x")


@dataclass(slots=True, frozen=True)
class StsCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str


class AwsV4Signer:
    region = "ap-singapore-1"
    service = "vod"

    @staticmethod
    def _hmac(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode(), hashlib.sha256).digest()

    def sign(
        self, method: str, url: str, credentials: StsCredentials, body: bytes = b"",
        *, now: datetime | None = None,
    ) -> dict[str, str]:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        amz_date, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        parsed = urlparse(url)
        pairs = []
        from urllib.parse import parse_qsl
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            pairs.append((quote(key, safe="-_.~"), quote(value, safe="-_.~")))
        canonical_query = "&".join(f"{key}={value}" for key, value in sorted(pairs))
        payload_hash = hashlib.sha256(body).hexdigest()
        headers = {"host": parsed.netloc, "x-amz-date": amz_date,
                   "x-amz-security-token": credentials.session_token}
        if method.upper() == "POST":
            headers["x-amz-content-sha256"] = payload_hash
        signed_names = ";".join(sorted(headers))
        canonical_headers = "".join(f"{key}:{headers[key].strip()}\n" for key in sorted(headers))
        canonical = f"{method.upper()}\n{parsed.path or '/'}\n{canonical_query}\n{canonical_headers}\n{signed_names}\n{payload_hash}"
        scope = f"{date}/{self.region}/{self.service}/aws4_request"
        string_to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
        date_key = self._hmac(("AWS4" + credentials.secret_access_key).encode(), date)
        region_key = self._hmac(date_key, self.region)
        service_key = self._hmac(region_key, self.service)
        signing_key = self._hmac(service_key, "aws4_request")
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        result = {"X-Amz-Date": amz_date, "X-Amz-Security-Token": credentials.session_token,
                  "Authorization": f"AWS4-HMAC-SHA256 Credential={credentials.access_key_id}/{scope}, SignedHeaders={signed_names}, Signature={signature}"}
        if method.upper() == "POST": result["X-Amz-Content-Sha256"] = payload_hash
        return result


class VodUploader:
    def __init__(self, client: CapCutEditClient, signer: AwsV4Signer | None = None) -> None:
        self.client = client; self.signer = signer or AwsV4Signer()

    def prepare(self, path: str | Path, md5_hex: str) -> dict[str, Any]:
        self.client.ensure_session()
        source = Path(path)
        payload = self.client.request("POST", "/lv/v1/asset/prepare_upload_cloud", appvr=APPVR_ASSET,
                                      headers={"Lan": "en"}, json={"workspace_id": self.client.session.workspace_id,
                                      "space_id": "0", "md5": md5_hex, "size": source.stat().st_size,
                                      "file_type": "video", "is_web_user": True})
        data = payload.get("data") or {}
        token = data.get("security_token") or {}
        required = ("access_key_id", "secret_access_key", "session_token")
        if not all(token.get(key) for key in required) or not data.get("upload_domain") or not data.get("service_id"):
            raise PipelineError("capcut_upload_schema", "CapCut không trả đủ thông tin upload.", provider="capcut")
        return data

    def upload(self, path: str | Path, *, cancel: CancellationToken | None = None) -> dict[str, Any]:
        source = Path(path)
        digest = hashlib.md5()
        with source.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                if cancel: cancel.raise_if_cancelled()
                digest.update(chunk)
        md5_hex = digest.hexdigest(); prepared = self.prepare(source, md5_hex)
        token = prepared["security_token"]
        credentials = StsCredentials(token["access_key_id"], token["secret_access_key"], token["session_token"])
        domain = str(prepared["upload_domain"]).removeprefix("https://").rstrip("/")
        params = {"Action": "ApplyUploadInner", "Version": "2020-11-19", "SpaceName": prepared["service_id"],
                  "FileType": "video", "IsInner": "1", "FileSize": str(source.stat().st_size),
                  "s": "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(11)), "device_platform": "web"}
        apply_url = f"https://{domain}/?{urlencode(params)}"
        response = self.client.http.get(apply_url, headers=self.signer.sign("GET", apply_url, credentials))
        response.raise_for_status(); apply = response.json()
        try:
            node = apply["Result"]["InnerUploadAddress"]["UploadNodes"][0]
            store = node["StoreInfos"][0]
            store_uri, auth = store["StoreUri"], store["Auth"]
            upload_host, session_key = node["UploadHost"], node["SessionKey"]
        except (KeyError, IndexError, TypeError) as exc:
            raise PipelineError("capcut_apply_schema", "Upload server trả schema không hợp lệ.", provider="capcut") from exc
        base = f"https://{str(upload_host).removeprefix('https://').rstrip('/')}/upload/v1/{store_uri}"
        headers = {"Authorization": auth, "X-Storage-U": self.client.session.uid}
        boundary = "----WebKitFormBoundary" + "".join(random.choice(string.ascii_letters + string.digits) for _ in range(16))
        init = self.client.http.post(f"{base}?uploadmode=part&phase=init", headers={**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"}, content=f"--{boundary}--\r\n".encode())
        init.raise_for_status(); upload_id = (init.json().get("data") or {}).get("uploadid")
        if not upload_id: raise PipelineError("capcut_upload_init", "Không khởi tạo được multipart upload.", provider="capcut")
        checksums: list[str] = []; offset = 0; part_number = 1
        with source.open("rb") as stream:
            while chunk := stream.read(PART_SIZE):
                if cancel: cancel.raise_if_cancelled()
                crc = crc32_hex(chunk)
                query = urlencode({"uploadid": upload_id, "part_number": part_number, "phase": "transfer", "part_offset": offset})
                part_headers = {**headers, "Content-Type": "application/octet-stream", "Content-Disposition": 'attachment; filename="undefined"', "Content-CRC32": crc}
                last: BaseException | None = None
                for _ in range(3):
                    try:
                        transfer = self.client.http.post(f"{base}?{query}", headers=part_headers, content=chunk)
                        transfer.raise_for_status()
                        if transfer.json().get("code") != 2000: raise ValueError("unexpected transfer code")
                        last = None; break
                    except (httpx.HTTPError, ValueError) as exc: last = exc
                if last: raise PipelineError("capcut_upload_part", "Upload một phần audio thất bại.", provider="capcut", retryable=True) from last
                checksums.append(f"{part_number}:{crc}"); offset += len(chunk); part_number += 1
        finish_query = urlencode({"uploadmode": "part", "phase": "finish", "uploadid": upload_id, "size": source.stat().st_size})
        finish = self.client.http.post(f"{base}?{finish_query}", headers={**headers, "Content-Type": "text/plain;charset=UTF-8"}, content=",".join(checksums))
        finish.raise_for_status()
        if finish.json().get("code") != 2000: raise PipelineError("capcut_upload_finish", "Không hoàn tất được multipart upload.", provider="capcut")
        commit_params = {"Action": "CommitUploadInner", "Version": "2020-11-19", "SpaceName": prepared["service_id"]}
        commit_url = f"https://{domain}/?{urlencode(commit_params)}"; body = json.dumps({"SessionKey": session_key}, separators=(",", ":")).encode()
        commit = self.client.http.post(commit_url, headers={**self.signer.sign("POST", commit_url, credentials, body), "Content-Type": "text/plain;charset=UTF-8"}, content=body)
        commit.raise_for_status()
        if not (commit.json().get("Result") or {}).get("Results"): raise PipelineError("capcut_commit_failed", "CapCut không commit được upload.", provider="capcut")
        return {"prepared": prepared, "node": node, "store_uri": store_uri, "md5": md5_hex, "size": source.stat().st_size}

    def create_asset(self, path: str | Path, upload: dict[str, Any]) -> str:
        source = Path(path); prepared = upload["prepared"]
        meta = json.dumps({"uploadSource": {"owner": self.client.session.uid, "platform": "browser", "systemVersion": USER_AGENT, "appVersion": "1.0.0"}}, separators=(",", ":"))
        payload = self.client.request("POST", "/lv/v1/asset/create_cloud_asset", appvr=APPVR_ASSET,
                                      headers={"Lan": "en"}, json={"everphoto_id": str(prepared.get("space_id", "0")),
                                      "asset": {"uri": upload["store_uri"], "size": upload["size"], "workspace_id": self.client.session.workspace_id,
                                      "filename": source.name, "upload_id": str(prepared.get("upload_id", "")), "if_image_async_resize": True,
                                      "permission": 0, "space_id": "0", "file_type": "video", "folder_id": "-1", "meta": meta, "md5": upload["md5"]},
                                      "is_web_user": True})
        asset_id = str(((payload.get("data") or {}).get("cloud_asset") or {}).get("asset_id") or "")
        if not asset_id: raise PipelineError("capcut_asset_schema", "Không đọc được asset ID.", provider="capcut")
        return asset_id

    def upload_asset(self, path: str | Path, *, cancel: CancellationToken | None = None) -> str:
        return self.create_asset(path, self.upload(path, cancel=cancel))
