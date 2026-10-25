"""调用火山引擎 OpenAPI：HMAC-SHA256 签名，POST JSON。只用标准库。

签名规则见 https://www.volcengine.com/docs/6369/67269
注意：本模块不打印、不记录 AK/SK 和 Authorization 头。
"""
import datetime
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request

HOST = "open.volcengineapi.com"
SERVICE = "ml_platform"
VERSION = "2024-07-01"


class VolcError(Exception):
    """接口返回错误，或响应无法解析。"""


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def call(action: str, body: dict, *, ak: str, sk: str, region: str, timeout: int = 30) -> dict:
    """调用一个 ml_platform 接口，返回响应里的 Result。"""
    query = urllib.parse.urlencode(sorted({"Action": action, "Version": VERSION}.items()))
    payload = json.dumps(body).encode()
    payload_hash = hashlib.sha256(payload).hexdigest()
    x_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short_date = x_date[:8]

    headers = {
        "content-type": "application/json",
        "host": HOST,
        "x-content-sha256": payload_hash,
        "x-date": x_date,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical_request = "\n".join(["POST", "/", query, canonical_headers, signed_headers, payload_hash])

    scope = f"{short_date}/{region}/{SERVICE}/request"
    string_to_sign = "\n".join([
        "HMAC-SHA256", x_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    key = sk.encode()
    for part in (short_date, region, SERVICE, "request"):
        key = _hmac(key, part)
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = (
        f"HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    )

    req = urllib.request.Request(f"https://{HOST}/?{query}", data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        if not raw:
            raise VolcError(f"HTTP {e.code}，响应为空") from None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise VolcError(f"响应不是 JSON：{raw[:200]!r}") from None

    err = data.get("ResponseMetadata", {}).get("Error")
    if err:
        raise VolcError(f"{err.get('Code')}: {err.get('Message')}")
    return data.get("Result", {})
