"""Optional, bounded Gmail recovery and encrypted per-email GLaDOS sessions."""
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
import base64
import imaplib
import json
import os
import re
import ssl
import tempfile
import time

from glados_common import (GladosError, ORIGINS, auth_denied, bool_setting, complete_session,
                           email_address, int_setting)
from glados_runtime import RuntimePaths, project_path


@dataclass(frozen=True)
class MailSettings:
    address: str = field(repr=False)
    password: str = field(repr=False)
    key: str = field(repr=False)
    senders: tuple = ("glados.network", "glados.cloud", "glados.one")
    wait_seconds: int = 120

    @classmethod
    def from_environment(cls):
        names = ("GLADOS_GMAIL_ADDRESS", "GLADOS_GMAIL_APP_PASSWORD", "GLADOS_COOKIE_STORE_KEY")
        address, password, key = (os.getenv(name, "").strip() for name in names)
        if not any((address, password)):
            return None
        if not all((address, password, key)):
            raise GladosError("Email recovery requires all three GLADOS Gmail/cache Secrets", kind="config")
        address = email_address(address)
        password = password.replace(" ", "")
        if not password or any(ord(char) < 33 or ord(char) > 126 for char in password):
            raise GladosError("Gmail application password is invalid", kind="config")
        senders = tuple(item.lower() for item in re.split(r"[\s,;]+", os.getenv(
            "GLADOS_GMAIL_SENDERS", "").strip() or "glados.network,glados.cloud,glados.one"))
        if not senders or any(not re.fullmatch(r"[A-Za-z0-9@._+-]+", item) for item in senders):
            raise GladosError("GLADOS_GMAIL_SENDERS must contain sender addresses or domains", kind="config")
        return cls(address, password, key, senders, int_setting("GLADOS_GMAIL_WAIT_SECONDS", 120, 10, 600))


def session_cache_key(raw):
    explicit = os.getenv("GLADOS_COOKIE_STORE_KEY", "").strip()
    if explicit:
        return explicit
    if not bool_setting("GLADOS_MANUAL_SESSION_CACHE", False):
        return ""
    try:
        accounts = json.loads(raw)
        if not isinstance(accounts, list) or not accounts:
            raise ValueError
        signatures = []
        for account in accounts:
            cookie = account.get("cookie", "")
            if not isinstance(cookie, str) or any(char in cookie for char in "\r\n"):
                raise ValueError
            for part in cookie.split(";"):
                name, separator, value = part.strip().partition("=")
                if separator and name.endswith(":sess.sig"):
                    if (len(value) < 20 or not re.fullmatch(r"[A-Za-z0-9_+/=-]+", value) or
                            len(set(value)) < 10):
                        raise ValueError
                    signatures.append(value)
        if not signatures:
            raise ValueError
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        # Existing signed Cookie Secrets supply the entropy, never public account labels.
        material = json.dumps(sorted(set(signatures)), separators=(",", ":")).encode("ascii")
        derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                       info=b"glados-manual-session-cache-v1").derive(material)
        return base64.urlsafe_b64encode(derived).decode("ascii")
    except (ValueError, TypeError, AttributeError, ImportError, UnicodeError):
        raise GladosError("Manual session cache needs existing signed Cookie Secrets or a Fernet key", kind="config") from None


class CookieStore:
    def __init__(self, key, runtime=None):
        try:
            from cryptography.fernet import Fernet
            self.cipher = Fernet(key.encode("ascii"))
        except (ImportError, ValueError, UnicodeError, AttributeError):
            raise GladosError("GLADOS_COOKIE_STORE_KEY must be a valid Fernet key", kind="config") from None
        self.runtime = runtime or RuntimePaths()
        self.path = project_path(self.runtime.base / "sessions.enc")
        self.entries = {}
        if self.path.exists():
            try:
                if self.path.stat().st_size > 1024 * 1024:
                    raise ValueError
                decoded = json.loads(self.cipher.decrypt(self.path.read_bytes()))
                if decoded.get("version") != 1 or not isinstance(decoded.get("accounts"), dict):
                    raise ValueError
                self.entries = decoded["accounts"]
            except Exception:
                print("[GLaDOS] Encrypted session cache unavailable; only validated sessions will be reused")

    def get(self, email, origin):
        entry = self.entries.get(email, {})
        values = entry.get("cookies") if isinstance(entry, dict) and entry.get("origin") == origin else None
        if not complete_session(values):
            return {}
        if any(not isinstance(name, str) or not isinstance(value, str) or
               re.search(r"[\s;,]", name) or any(char in value for char in "\r\n;")
               for name, value in values.items()):
            return {}
        return dict(values)

    def put(self, email, origin, values):
        email = email_address(email)
        if origin not in ORIGINS or not complete_session(values):
            raise GladosError("Refusing to cache an incomplete or unbound session", kind="auth")
        self.entries[email] = {"origin": origin, "cookies": dict(values)}
        self.save()

    def remove(self, email):
        if email in self.entries:
            del self.entries[email]
            self.save()

    def save(self):
        plaintext = json.dumps({"version": 1, "accounts": self.entries}, separators=(",", ":")).encode("utf-8")
        if len(plaintext) > 512 * 1024:
            raise GladosError("Encrypted session cache exceeded its size limit", kind="runtime")
        ciphertext = self.cipher.encrypt(plaintext)
        self.runtime.ensure()
        target = project_path(self.path)
        temporary = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=".sessions-", suffix=".tmp", dir=str(target.parent))
            temporary = project_path(name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(ciphertext)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except OSError:
            raise GladosError("Cannot atomically save the encrypted session cache", kind="runtime") from None
        finally:
            if temporary is not None and temporary.exists():
                project_path(temporary).unlink()


class _MailText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag in ("br", "p", "div", "td"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def verification_code(raw, target, requested_at, senders):
    if not isinstance(raw, bytes) or len(raw) > 1024 * 1024:
        return None
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        recipients = getaddresses([str(value) for name in ("To", "X-Original-To", "X-Gm-Original-To")
                                   for value in message.get_all(name, [])])
        if target not in {address.lower() for _, address in recipients}:
            return None
        sources = getaddresses([str(value) for value in message.get_all("From", [])])
        if len(sources) != 1:
            return None
        sender = sources[0][1].lower()
        domain = sender.rpartition("@")[2]
        if not any(sender == allowed if "@" in allowed else
                   domain == allowed or domain.endswith("." + allowed) for allowed in senders):
            return None
        date = parsedate_to_datetime(str(message.get("Date", "")))
        if date.tzinfo is None or not requested_at - 30 <= date.timestamp() <= requested_at + 660:
            return None
        text = [str(message.get("Subject", ""))]
        for part in message.walk():
            if part.is_multipart() or part.get_content_disposition() == "attachment":
                continue
            if part.get_content_type() == "text/plain":
                text.append(part.get_content())
            elif part.get_content_type() == "text/html":
                parser = _MailText()
                parser.feed(part.get_content())
                text.append("".join(parser.parts))
        content = "\n".join(text)
        if not re.search(r"code|\u9a8c\u8bc1\u7801|\u8bbf\u95ee\u7801", content, re.I):
            return None
        codes = set(re.findall(r"(?<![A-Za-z0-9])([0-9]{6})(?![A-Za-z0-9])", content))
        return codes.pop() if len(codes) == 1 else None
    except (ValueError, TypeError, LookupError, UnicodeError, AttributeError):
        return None


class GmailInbox:
    def __init__(self, settings):
        self.settings = settings
        self.client = None
        self.seen = set()

    def __enter__(self):
        try:
            # create_default_context() honors SSLKEYLOGFILE and can open an external file.
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            tls.load_default_certs()
            self.client = imaplib.IMAP4_SSL("imap.gmail.com", 993,
                ssl_context=tls, timeout=15)
            self.client.login(self.settings.address, self.settings.password)
            code, _ = self.client.select("INBOX", readonly=True)
            if code != "OK":
                raise ValueError
            self.first_uid, self.validity = self._uids()
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise GladosError("Gmail IMAP connection/authentication failed", kind="mail") from None

    def __exit__(self, *args):
        if self.client is not None:
            try:
                self.client.logout()
            except Exception:
                pass

    def _uids(self):
        code, data = self.client.status("INBOX", "(UIDNEXT UIDVALIDITY)")
        if code != "OK":
            raise GladosError("Gmail UID boundary unavailable", kind="mail")
        content = b" ".join(value for value in data if isinstance(value, bytes))
        next_uid = re.search(rb"UIDNEXT\s+(\d+)", content)
        validity = re.search(rb"UIDVALIDITY\s+(\d+)", content)
        if not next_uid or not validity or int(next_uid[1]) <= 0:
            raise GladosError("Gmail UID boundary invalid", kind="mail")
        return int(next_uid[1]), int(validity[1])

    def _budget(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GladosError("Verification email timed out; check forwarding and sender rules", kind="mail")
        if getattr(self.client, "sock", None) is not None:
            self.client.sock.settimeout(min(15, remaining))

    def wait_for_code(self, target, requested_at):
        deadline = time.monotonic() + self.settings.wait_seconds
        try:
            while True:
                self._budget(deadline)
                if self._uids()[1] != self.validity:
                    raise GladosError("Gmail UID validity changed; refusing stale email", kind="mail")
                self._budget(deadline)
                code, data = self.client.uid("search", None, f"UID {self.first_uid}:*")
                if code != "OK":
                    raise GladosError("Gmail search failed", kind="mail")
                uids = sorted({int(value) for line in data if isinstance(line, bytes) for value in line.split()
                               if value.isdigit() and int(value) >= self.first_uid}, reverse=True)
                candidates = set()
                for uid in uids[:50]:
                    if uid in self.seen:
                        continue
                    self._budget(deadline)
                    code, parts = self.client.uid("fetch", str(uid), "(BODY.PEEK[])")
                    self._budget(deadline)
                    if code != "OK":
                        continue
                    self.seen.add(uid)
                    for item in parts:
                        if isinstance(item, tuple) and len(item) > 1:
                            value = verification_code(item[1], target, requested_at, self.settings.senders)
                            if value:
                                candidates.add(value)
                self._budget(deadline)
                if len(candidates) > 1:
                    raise GladosError("Multiple verification codes matched; refusing to guess", kind="mail")
                if candidates:
                    return candidates.pop()
                time.sleep(min(5, deadline - time.monotonic()))
        except GladosError:
            raise
        except Exception:
            raise GladosError("Gmail verification read failed", kind="mail") from None


class EmailRecovery:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store

    def _request(self, client, method, path, payload=None):
        try:
            kwargs = {"headers": client.headers(path), "timeout": 15, "allow_redirects": False,
                      "discard_cookies": True, "default_headers": False}
            if client.proxy_url:
                kwargs["proxy"] = client.proxy_url
            if payload is not None:
                kwargs["json"] = payload
            response = getattr(client.session, method.lower())(client.panel["origin"] + path, **kwargs)
            if response.status_code == 401:
                return {"code": -2}
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError
            if response.status_code == 403 and auth_denied(data):
                return data
            if response.status_code != 200:
                raise ValueError
            if auth_denied(data):
                return data
            client.merge_cookies(response, path)
            return data
        except Exception:
            raise GladosError("GLaDOS authentication request failed; not retried", kind="auth-request") from None

    def _matches(self, client, email):
        payload = self._request(client, "GET", "/api/user/status")
        if auth_denied(payload):
            return False
        data = payload.get("data")
        if type(payload.get("code")) is not int or payload["code"] != 0 or not isinstance(data, dict):
            raise GladosError("Account status unavailable; no email login triggered", kind="auth-request")
        if not isinstance(data.get("email"), str) or data["email"].strip().lower() != email:
            raise GladosError("Session account identity mismatch; no email login triggered", kind="identity")
        return True

    def prepare(self, client, email):
        cached = self.store.get(email, client.panel["origin"])
        if cached:
            client.replace_cookie_values(cached)
        if complete_session(client.cookie_values()) and self._matches(client, email):
            self.store.put(email, client.panel["origin"], client.cookie_values())
            print("[GLaDOS] Validated session reused; no verification email sent")
            return False
        self.login(client, email)
        return True

    def login(self, client, email):
        self.store.remove(email)
        client.replace_cookie_values({})
        with GmailInbox(self.settings) as inbox:
            requested_at = time.time()
            sent = self._request(client, "POST", "/api/authorization",
                                 {"address": email, "site": "glados.network"})
            if sent.get("captcha_required") or type(sent.get("code")) is not int or sent["code"] != 0:
                raise GladosError("Email authorization refused or requires manual login verification", kind="mail")
            code = inbox.wait_for_code(email, requested_at)
            logged_in = self._request(client, "POST", "/api/login", {
                "method": "email", "site": "glados.network", "email": email, "mailcode": code})
        if (type(logged_in.get("code")) is not int or logged_in["code"] != 0 or
                not complete_session(client.cookie_values()) or not self._matches(client, email)):
            client.replace_cookie_values({})
            raise GladosError("Email login did not establish a verified session", kind="auth")
        client.replace_cookie_values(client.cookie_values())
        self.store.put(email, client.panel["origin"], client.cookie_values())
        print("[GLaDOS] Account session recovered through one email login")
