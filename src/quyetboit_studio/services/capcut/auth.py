from __future__ import annotations

import hashlib
import secrets
import string
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from ...core.models import PipelineError
from .config import (
    AID,
    AUTH_COOKIES,
    CAPTCHA_CODES,
    EMAIL_HASH_SALT,
    LOGIN_FALLBACK,
    LOGIN_PAGE,
    LOGIN_ROW,
    USER_AGENT,
)


def xor5_hex(value: str) -> str:
    return "".join(f"{byte ^ 5:02x}" for byte in value.encode("utf-8"))


def _base36(value: int) -> str:
    alphabet = string.digits + string.ascii_lowercase
    output = ""
    while value:
        value, remainder = divmod(value, 36)
        output = alphabet[remainder] + output
    return output or "0"


def make_verify_fp() -> str:
    now = _base36(int(time.time() * 1000))[-8:]
    return f"verify_{now}_{secrets.token_hex(4)}_{secrets.token_hex(2)}_{secrets.token_hex(2)}_{secrets.token_hex(2)}_{secrets.token_hex(6)}"


def make_did() -> str:
    suffix = "".join(secrets.choice(string.digits) for _ in range(8))
    return f"{int(time.time() * 1000)}{suffix}"[:19]


@dataclass(slots=True)
class CapCutSession:
    cookies: dict[str, str]
    did: str
    uid: str = ""
    workspace_id: str = ""

    @property
    def has_auth(self) -> bool:
        return any(self.cookies.get(name) for name in AUTH_COOKIES)


class CapCutAuth:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.http = client or httpx.Client(
            timeout=httpx.Timeout(30), follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )

    def login(self, email: str, password: str) -> CapCutSession:
        if not email.strip() or not password:
            raise PipelineError("capcut_credentials_missing", "Thiếu email hoặc password CapCut.", provider="capcut")
        did, verify_fp = make_did(), make_verify_fp()
        csrf = secrets.token_hex(16)
        self.http.cookies.set("passport_csrf_token", csrf, domain=".capcut.com")
        self.http.cookies.set("passport_csrf_token_default", csrf, domain=".capcut.com")
        try:
            try:
                self.http.get(LOGIN_PAGE)
            except httpx.HTTPError:
                pass
            host = self._resolve(email, did, verify_fp)
            response = self._submit(host, email, password, did, verify_fp, csrf)
            if response.status_code in {404, 405}:
                response = self._submit(host, email, password, did, verify_fp, csrf, fallback=True)
            response.raise_for_status()
            payload = response.json()
            code = int(payload.get("error_code") or payload.get("code") or 0)
            if code in CAPTCHA_CODES:
                raise PipelineError(
                    "capcut_captcha_required",
                    "CapCut yêu cầu xác minh. Hãy đăng nhập/xác minh trong trình duyệt rồi thử lại.",
                    provider="capcut",
                )
            cookies = {name: value for name, value in self.http.cookies.items() if name in AUTH_COOKIES or name.startswith("passport_") or name in {"_tea_web_id", "s_v_web_id", "ttwid"}}
            session = CapCutSession(cookies, did)
            if not session.has_auth:
                raise PipelineError(
                    "capcut_login_failed", "Đăng nhập CapCut thất bại. Hãy kiểm tra email/password.",
                    provider="capcut", debug_detail=f"business_code={code}",
                )
            return session
        except PipelineError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise PipelineError(
                "capcut_login_network", "Không thể kết nối dịch vụ đăng nhập CapCut.",
                provider="capcut", retryable=True, debug_detail=str(exc),
            ) from exc

    def _resolve(self, email: str, did: str, verify_fp: str) -> str:
        hashed = hashlib.sha256((email.strip().lower() + EMAIL_HASH_SALT).encode()).hexdigest()
        try:
            response = self.http.post(
                f"{LOGIN_ROW}/passport/web/region/", params=self._params(verify_fp, mix=True),
                headers=self._headers(did, ""), data={"type": "2", "hashed_id": hashed},
            )
            response.raise_for_status()
            data = response.json().get("data") or {}
            raw = next((data.get(key) for key in ("login_domain", "domain", "redirect_url", "redirect_domain", "region_domain") if data.get(key)), "")
            if raw:
                parsed = urlparse(raw if "://" in raw else f"https://{raw}")
                if parsed.hostname and parsed.hostname.endswith("capcut.com"):
                    return f"https://{parsed.hostname}"
        except (httpx.HTTPError, ValueError):
            pass
        return LOGIN_FALLBACK

    @staticmethod
    def _params(verify_fp: str, *, mix: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {"aid": AID, "account_sdk_source": "web", "sdk_version": "2.1.10-tiktok", "language": "en-US", "verifyFp": verify_fp}
        if mix:
            result["mix_mode"] = 1
        return result

    @staticmethod
    def _headers(did: str, csrf: str) -> dict[str, str]:
        return {"Content-Type": "application/x-www-form-urlencoded", "appid": AID, "did": did,
                "Origin": "https://www.capcut.com", "Referer": LOGIN_PAGE,
                "store-country-code": "vn", "store-country-code-src": "uid",
                "x-tt-passport-csrf-token": csrf}

    def _submit(self, host: str, email: str, password: str, did: str, verify_fp: str, csrf: str, *, fallback: bool = False) -> httpx.Response:
        endpoint = "user/login" if fallback else "email/login"
        return self.http.post(
            f"{host}/passport/web/{endpoint}/", params=self._params(verify_fp),
            headers=self._headers(did, csrf),
            data={"email": xor5_hex(email), "password": xor5_hex(password), "mix_mode": "1", "fixed_mix_mode": "1"},
        )
