"""Shared Zotero REST API helpers."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any


class ApiError:
    """Sentinel returned by _request on HTTP/network errors (distinct from None for empty responses)."""

    def __init__(self, message: str):
        self.message = message

    def __bool__(self):
        return False

    def __repr__(self):
        return f"ApiError({self.message!r})"


# Return type for API functions
ApiResult = dict | list | None | ApiError


def is_error(result: Any) -> bool:
    """Check if an API result is an error."""
    return isinstance(result, ApiError)


def _get_credentials() -> tuple[str, str]:
    """Read Zotero credentials from environment, raising if missing."""
    user_id = os.environ.get("ZOTERO_USER_ID", "")
    api_key = os.environ.get("ZOTERO_API_KEY", "")
    if not user_id:
        raise RuntimeError("ZOTERO_USER_ID environment variable is not set")
    if not api_key:
        raise RuntimeError("ZOTERO_API_KEY environment variable is not set")
    return user_id, api_key


def _request(method: str, url: str, api_key: str, data: bytes | None = None, headers: dict | None = None) -> ApiResult:
    """Make an HTTP request. Returns parsed JSON, None for empty body, or ApiError on failure."""
    hdrs = {"Zotero-API-Key": api_key}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            return json.loads(body) if body.strip() else None
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        msg = f"HTTP {e.code}: {body[:200]}"
        print(f"Zotero API error ({msg})", file=sys.stderr)
        return ApiError(msg)
    except urllib.error.URLError as e:
        msg = str(e)
        print(f"Request failed: {msg}", file=sys.stderr)
        return ApiError(msg)
    except json.JSONDecodeError as e:
        msg = f"Invalid JSON: {e}"
        print(f"Request failed: {msg}", file=sys.stderr)
        return ApiError(msg)


def api_get(path: str) -> ApiResult:
    """GET from Zotero REST API. Returns parsed JSON, None, or ApiError."""
    user_id, api_key = _get_credentials()
    url = f"https://api.zotero.org/users/{user_id}/{path.lstrip('/')}"
    return _request("GET", url, api_key)


def api_post(path: str, data: list | dict) -> ApiResult:
    """POST JSON to Zotero REST API. Returns parsed JSON, None, or ApiError."""
    user_id, api_key = _get_credentials()
    url = f"https://api.zotero.org/users/{user_id}/{path.lstrip('/')}"
    token = uuid.uuid4().hex[:32]
    return _request(
        "POST", url, api_key,
        data=json.dumps(data).encode(),
        headers={
            "Content-Type": "application/json",
            "Zotero-Write-Token": token,
        },
    )


def api_post_items(items: list[dict]) -> bool:
    """POST items to Zotero. Returns True if all succeeded."""
    for i in range(0, len(items), 50):
        batch = items[i : i + 50]
        result = api_post("/items", batch)
        if is_error(result):
            return False
        if not isinstance(result, dict):
            print(f"Unexpected response type: {type(result)}", file=sys.stderr)
            return False
        failed = result.get("failed", {})
        if failed:
            for key, err in failed.items():
                print(f"  Failed item {key}: {err.get('message', err)}", file=sys.stderr)
            return False
    return True


def api_post_raw(path: str, data: bytes, headers: dict) -> ApiResult:
    """POST raw bytes to Zotero REST API (for file upload auth, etc.)."""
    user_id, api_key = _get_credentials()
    url = f"https://api.zotero.org/users/{user_id}/{path.lstrip('/')}"
    return _request("POST", url, api_key, data=data, headers=headers)


def upload_file_to_item(item_key: str, filepath: str, filename: str, content_type: str) -> bool:
    """Upload a file to a Zotero attachment item. Returns True on success.

    Steps: 1) get upload auth  2) upload to S3  3) register upload.
    """
    import hashlib
    user_id, api_key = _get_credentials()

    with open(filepath, "rb") as f:
        file_data = f.read()

    md5 = hashlib.md5(file_data).hexdigest()
    filesize = len(file_data)
    mtime = int(os.path.getmtime(filepath) * 1000)

    # Step 1: Get upload authorization
    auth_body = urllib.parse.urlencode({
        "md5": md5, "filename": filename,
        "filesize": filesize, "mtime": mtime,
    }).encode()
    base_url = f"https://api.zotero.org/users/{user_id}/items/{item_key}/file"
    auth_result = _request(
        "POST", base_url, api_key,
        data=auth_body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "If-None-Match": "*",
        },
    )
    if is_error(auth_result) or auth_result is None:
        print(f"File upload auth failed: {auth_result}", file=sys.stderr)
        return False

    if isinstance(auth_result, dict) and auth_result.get("exists") == 1:
        print("  File already exists in Zotero", file=sys.stderr)
        return True

    # Step 2: Upload to the provided URL
    upload_url = auth_result["url"]
    prefix = auth_result["prefix"].encode()
    suffix = auth_result["suffix"].encode()
    upload_content_type = auth_result["contentType"]
    upload_key = auth_result["uploadKey"]

    upload_body = prefix + file_data + suffix
    upload_req = urllib.request.Request(
        upload_url, data=upload_body,
        headers={"Content-Type": upload_content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(upload_req, timeout=120) as resp:
            if resp.status not in (200, 201):
                print(f"File upload failed: HTTP {resp.status}", file=sys.stderr)
                return False
    except urllib.error.URLError as e:
        print(f"File upload failed: {e}", file=sys.stderr)
        return False

    # Step 3: Register the upload
    register_body = urllib.parse.urlencode({"upload": upload_key}).encode()
    register_result = _request(
        "POST", base_url, api_key,
        data=register_body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "If-None-Match": "*",
        },
    )
    if is_error(register_result):
        print(f"File upload registration failed: {register_result}", file=sys.stderr)
        return False

    return True


_HTTP_USER_AGENT = "claude-zotero/0.1 (https://github.com/ekunish/claude-zotero)"


def http_get_json(url: str, timeout: int = 15) -> dict | list | None:
    """Simple GET returning parsed JSON. For non-Zotero APIs (CrossRef, etc.)."""
    req = urllib.request.Request(url, headers={"User-Agent": _HTTP_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"http_get_json failed for {url}: {e}", file=sys.stderr)
        return None
