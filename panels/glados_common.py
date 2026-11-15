"""Shared, credential-safe contracts for the optional GLaDOS browser flow."""
import os
import re

ORIGINS = ("https://glados.cloud", "https://glados.rocks", "https://glados.network")
TURNSTILE_HOST = "challenges.cloudflare.com"
CHALLENGE_SUFFIX = ".glados-observe-v1"
CHALLENGE_DELAY = 2.7
BROWSER_LOCALE = "en-US"
EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


class GladosError(RuntimeError):
    """Messages must be fixed descriptions, never upstream errors or credentials."""

    def __init__(self, message, *, kind="browser", submitted=False, fallback=False):
        super().__init__(message)
        self.kind = kind
        self.submitted = submitted
        self.fallback = fallback and not submitted


def bool_setting(name, default=False):
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    if value not in ("1", "0", "true", "false"):
        raise GladosError(f"{name} must be 1/0 or true/false", kind="config")
    return value in ("1", "true")


def int_setting(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, "").strip() or str(default))
        if not minimum <= value <= maximum:
            raise ValueError
        return value
    except ValueError:
        raise GladosError(f"{name} is outside its supported integer range", kind="config") from None


def email_address(value):
    if not isinstance(value, str) or len(value) > 254 or not EMAIL.fullmatch(value.strip()):
        raise GladosError("Email recovery requires a real account email, not a display label", kind="config")
    return value.strip().lower()


def turnstile_denied(payload):
    if not isinstance(payload, dict):
        return False
    message = payload.get("message", "")
    return payload.get("reason") == "turnstile" or (
        isinstance(message, str) and "cloudflare verification failed" in message.lower())


def auth_denied(payload):
    if not isinstance(payload, dict) or turnstile_denied(payload):
        return False
    code = payload.get("code")
    return (type(code) in (int, str) and code in (-2, "-2", 4, "4")) or (
        payload.get("reason") == "device-mismatch")


def complete_session(values):
    return isinstance(values, dict) and any(
        isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]+:sess", name) and
        isinstance(value, str) and value and isinstance(values.get(name + ".sig"), str) and
        values[name + ".sig"] for name, value in values.items())
