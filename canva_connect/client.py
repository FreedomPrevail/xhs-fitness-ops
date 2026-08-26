"""Canva OAuth 2.0 PKCE and Design Import client.

Credentials and OAuth tokens are stored in the operating-system credential
vault through ``keyring``.  No token is written into the project tree.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable


AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"
IMPORT_URL = "https://api.canva.com/rest/v1/imports"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/oauth/callback"
DEFAULT_SCOPE = "design:content:write"
KEYRING_CREDENTIAL_SERVICE = "xhs-fitness-ops.canva.credentials"
KEYRING_TOKEN_SERVICE = "xhs-fitness-ops.canva.tokens"
_REFRESH_LOCK = threading.Lock()


class CanvaError(RuntimeError):
    """Actionable Canva integration error."""


@dataclass(frozen=True)
class CanvaConfig:
    client_id: str
    client_secret: str
    redirect_uri: str = DEFAULT_REDIRECT_URI
    scope: str = DEFAULT_SCOPE
    profile: str = "default"


def _keyring():
    try:
        import keyring  # type: ignore
        from keyring.errors import KeyringError  # type: ignore
    except ImportError as exc:
        raise CanvaError("缺少 keyring；请重新运行 setup_environment.sh 安装项目依赖") from exc
    try:
        backend = keyring.get_keyring()
        priority = getattr(backend, "priority", 0)
        if priority is None or priority <= 0:
            raise CanvaError("系统没有可用的安全凭证库，不能保存 Canva 密钥")
    except KeyringError as exc:
        raise CanvaError(f"无法访问系统安全凭证库：{exc}") from exc
    return keyring


def save_credentials(client_id: str, client_secret: str) -> None:
    client_id = str(client_id or "").strip()
    client_secret = str(client_secret or "").strip()
    if not client_id or not client_secret:
        raise CanvaError("Client ID 和 Client Secret 都不能为空")
    keyring = _keyring()
    try:
        keyring.set_password(KEYRING_CREDENTIAL_SERVICE, "client_id", client_id)
        keyring.set_password(KEYRING_CREDENTIAL_SERVICE, "client_secret", client_secret)
    except Exception as exc:
        raise CanvaError(f"无法把 Canva 凭证写入系统安全凭证库：{exc}") from exc


def load_config(profile: str = "default") -> CanvaConfig:
    keyring = _keyring()
    client_id = os.environ.get("CANVA_CLIENT_ID", "").strip()
    client_secret = os.environ.get("CANVA_CLIENT_SECRET", "").strip()
    try:
        if not client_id:
            client_id = keyring.get_password(KEYRING_CREDENTIAL_SERVICE, "client_id") or ""
        if not client_secret:
            client_secret = keyring.get_password(KEYRING_CREDENTIAL_SERVICE, "client_secret") or ""
    except Exception as exc:
        # Some macOS keychain versions return an OSStatus error instead of None
        # for a missing generic-password item. Treat that as unconfigured.
        if not os.environ.get("CANVA_CLIENT_ID") and not os.environ.get("CANVA_CLIENT_SECRET"):
            client_id = client_secret = ""
        else:
            raise CanvaError(f"无法读取系统安全凭证库：{exc}") from exc
    if not client_id or not client_secret:
        raise CanvaError(
            "尚未配置 Canva 开发者凭证；运行 `python -m canva_connect configure --client-id <ID>`"
        )
    redirect_uri = os.environ.get("CANVA_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip()
    scope = os.environ.get("CANVA_SCOPES", DEFAULT_SCOPE).strip() or DEFAULT_SCOPE
    return CanvaConfig(client_id, client_secret, redirect_uri, scope, profile or "default")


def _token_user(config: CanvaConfig, kind: str) -> str:
    return f"{config.client_id}:{config.profile}:{kind}"


def _load_tokens(config: CanvaConfig) -> dict[str, Any]:
    keyring = _keyring()
    try:
        meta_raw = keyring.get_password(KEYRING_TOKEN_SERVICE, _token_user(config, "meta")) or "{}"
        access_token = keyring.get_password(KEYRING_TOKEN_SERVICE, _token_user(config, "access")) or ""
        refresh_token = keyring.get_password(KEYRING_TOKEN_SERVICE, _token_user(config, "refresh")) or ""
    except Exception:
        meta_raw, access_token, refresh_token = "{}", "", ""
    try:
        meta = json.loads(meta_raw)
    except json.JSONDecodeError:
        meta = {}
    meta["access_token"] = access_token
    meta["refresh_token"] = refresh_token
    return meta


def _save_tokens(config: CanvaConfig, payload: dict[str, Any]) -> None:
    access_token = str(payload.get("access_token") or "")
    refresh_token = str(payload.get("refresh_token") or "")
    if not access_token or not refresh_token:
        raise CanvaError("Canva 令牌响应不完整")
    expires_in = max(0, int(payload.get("expires_in") or 0))
    meta = {
        "expires_at": int(time.time()) + expires_in,
        "scope": str(payload.get("scope") or config.scope),
        "token_type": str(payload.get("token_type") or "Bearer"),
        "saved_at": int(time.time()),
    }
    keyring = _keyring()
    # Canva refresh tokens rotate and are single-use. Save the new refresh
    # token before replacing access-token metadata.
    try:
        keyring.set_password(KEYRING_TOKEN_SERVICE, _token_user(config, "refresh"), refresh_token)
        keyring.set_password(KEYRING_TOKEN_SERVICE, _token_user(config, "access"), access_token)
        keyring.set_password(KEYRING_TOKEN_SERVICE, _token_user(config, "meta"), json.dumps(meta))
    except Exception as exc:
        raise CanvaError(f"无法把 Canva OAuth 令牌写入系统安全凭证库：{exc}") from exc


def delete_tokens(config: CanvaConfig) -> None:
    keyring = _keyring()
    for kind in ("access", "refresh", "meta"):
        try:
            keyring.delete_password(KEYRING_TOKEN_SERVICE, _token_user(config, kind))
        except Exception:
            pass


def make_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    return verifier, challenge


def _basic_auth(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _default_transport(method: str, url: str, headers: dict[str, str], data: bytes | None, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
            message = detail.get("message") or detail.get("error_description") or detail.get("error") or raw
        except json.JSONDecodeError:
            message = raw or str(exc)
        raise CanvaError(f"Canva API {exc.code}：{message}") from exc
    except urllib.error.URLError as exc:
        raise CanvaError(f"无法连接 Canva API：{exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CanvaError("Canva API 返回了无法解析的响应") from exc


class CanvaClient:
    def __init__(self, config: CanvaConfig | None = None,
                 transport: Callable[[str, str, dict[str, str], bytes | None, float], dict[str, Any]] | None = None):
        self.config = config or load_config()
        self.transport = transport or _default_transport

    def authorization_url(self, code_challenge: str, state: str) -> str:
        query = urllib.parse.urlencode({
            "code_challenge": code_challenge,
            "code_challenge_method": "s256",
            "scope": self.config.scope,
            "response_type": "code",
            "client_id": self.config.client_id,
            "state": state,
            "redirect_uri": self.config.redirect_uri,
        })
        return f"{AUTHORIZE_URL}?{query}"

    def _token_request(self, fields: dict[str, str]) -> dict[str, Any]:
        headers = {
            "Authorization": _basic_auth(self.config.client_id, self.config.client_secret),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        return self.transport("POST", TOKEN_URL, headers, urllib.parse.urlencode(fields).encode("ascii"), 30)

    def exchange_code(self, code: str, verifier: str) -> dict[str, Any]:
        payload = self._token_request({
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": self.config.redirect_uri,
        })
        _save_tokens(self.config, payload)
        return payload

    def login(self, *, open_browser: bool = True, timeout: int = 300) -> dict[str, Any]:
        parsed = urllib.parse.urlparse(self.config.redirect_uri)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            raise CanvaError("CLI 授权回调必须是 http://127.0.0.1:<端口>/...；请检查 CANVA_REDIRECT_URI")
        verifier, challenge = make_pkce_pair()
        state = secrets.token_urlsafe(32)
        result: dict[str, str] = {}
        expected_path = parsed.path or "/oauth/callback"

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                callback = urllib.parse.urlparse(self.path)
                if callback.path != expected_path:
                    self.send_response(404); self.end_headers(); return
                query = urllib.parse.parse_qs(callback.query)
                received_state = (query.get("state") or [""])[0]
                if not secrets.compare_digest(received_state, state):
                    result["error"] = "OAuth state 校验失败"
                    status, message = 400, "Canva 授权失败：state 校验失败。"
                elif query.get("error"):
                    result["error"] = (query.get("error_description") or query.get("error") or ["授权被取消"])[0]
                    status, message = 400, f"Canva 授权失败：{result['error']}"
                else:
                    result["code"] = (query.get("code") or [""])[0]
                    status, message = 200, "Canva 授权已完成，可以关闭此页面并返回运营看板。"
                body = f"<!doctype html><meta charset='utf-8'><title>Canva 授权</title><h2>{message}</h2>".encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)

            def log_message(self, *_args):
                return

        try:
            server = HTTPServer((parsed.hostname, parsed.port), CallbackHandler)
        except OSError as exc:
            raise CanvaError(f"无法监听 OAuth 回调端口 {parsed.port}：{exc}") from exc
        url = self.authorization_url(challenge, state)
        if open_browser and not webbrowser.open(url):
            print(f"请在浏览器打开：{url}")
        else:
            print(f"Canva 授权地址：{url}")
        deadline = time.monotonic() + max(30, timeout)
        server.timeout = 1
        try:
            while time.monotonic() < deadline and not result:
                server.handle_request()
        finally:
            server.server_close()
        if result.get("error"):
            raise CanvaError(result["error"])
        if not result.get("code"):
            raise CanvaError("等待 Canva 授权超时，请重新运行登录命令")
        payload = self.exchange_code(result["code"], verifier)
        return {"ok": True, "scope": payload.get("scope", self.config.scope), "expires_in": payload.get("expires_in")}

    def status(self) -> dict[str, Any]:
        tokens = _load_tokens(self.config)
        expires_at = int(tokens.get("expires_at") or 0)
        return {
            "ok": True,
            "configured": True,
            "authenticated": bool(tokens.get("refresh_token")),
            "access_token_valid": bool(tokens.get("access_token")) and expires_at > int(time.time()) + 60,
            "expires_at": expires_at or None,
            "scope": tokens.get("scope") or self.config.scope,
            "profile": self.config.profile,
            "redirect_uri": self.config.redirect_uri,
        }

    def access_token(self) -> str:
        with _REFRESH_LOCK:
            tokens = _load_tokens(self.config)
            if tokens.get("access_token") and int(tokens.get("expires_at") or 0) > int(time.time()) + 60:
                return str(tokens["access_token"])
            refresh_token = str(tokens.get("refresh_token") or "")
            if not refresh_token:
                raise CanvaError("Canva 尚未授权；先运行 `python -m canva_connect login`")
            payload = self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
            _save_tokens(self.config, payload)
            return str(payload["access_token"])

    def import_design(self, pptx_path: str | Path, *, title: str | None = None,
                      poll_timeout: int = 240, poll_interval: float = 2.0) -> dict[str, Any]:
        path = Path(pptx_path).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".pptx":
            raise CanvaError(f"找不到可导入的 PPTX：{path}")
        clean_title = str(title or path.stem).strip()[:50] or "小红书图文"
        title_base64 = base64.b64encode(clean_title.encode("utf-8")).decode("ascii")
        token = self.access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
            "Import-Metadata": json.dumps({
                "title_base64": title_base64,
                "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            }, separators=(",", ":")),
        }
        response = self.transport("POST", IMPORT_URL, headers, path.read_bytes(), 120)
        job = response.get("job") or {}
        job_id = str(job.get("id") or "")
        if not job_id:
            raise CanvaError("Canva 没有返回导入任务编号")
        deadline = time.monotonic() + max(10, poll_timeout)
        while str(job.get("status")) == "in_progress" and time.monotonic() < deadline:
            time.sleep(max(0.2, poll_interval))
            response = self.transport("GET", f"{IMPORT_URL}/{urllib.parse.quote(job_id)}",
                                      {"Authorization": f"Bearer {token}"}, None, 30)
            job = response.get("job") or {}
        status = str(job.get("status") or "")
        if status == "failed":
            error = job.get("error") or {}
            raise CanvaError(f"Canva 导入失败：{error.get('message') or error.get('code') or '未知错误'}")
        if status != "success":
            raise CanvaError(f"Canva 导入任务等待超时（job_id={job_id}）")
        designs = ((job.get("result") or {}).get("designs") or [])
        if not designs:
            raise CanvaError("Canva 导入成功但没有返回设计")
        normalized = []
        for design in designs:
            urls = design.get("urls") or {}
            normalized.append({
                "id": design.get("id"),
                "title": design.get("title") or clean_title,
                "page_count": design.get("page_count"),
                "edit_url": urls.get("edit_url"),
                "view_url": urls.get("view_url"),
            })
        return {"ok": True, "job_id": job_id, "status": "success", "designs": normalized, "source": str(path)}
