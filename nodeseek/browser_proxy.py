"""Credential-safe proxy validation shared by the browser check-in clients."""

import ipaddress
import re
from urllib.parse import urlsplit


def normalize_browser_proxy(raw, setting="NS_PROXY"):
    """Return a Chrome proxy URL, rejecting credentials and route bypass syntax."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        scheme = "socks5" if parts.scheme == "socks5h" else parts.scheme
        host = parts.hostname
        port = parts.port
        if (scheme not in ("http", "https", "socks5") or not host or not port
                or parts.username is not None or parts.password is not None
                or parts.path not in ("", "/") or parts.query or parts.fragment
                or any(char.isspace() for char in raw)):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host):
                raise ValueError
        if ":" in host:
            host = f"[{host}]"
        return f"{scheme}://{host}:{port}"
    except ValueError:
        raise ValueError(
            f"{setting} must be an HTTP(S)/SOCKS5 host:port URL without credentials or paths"
        ) from None
