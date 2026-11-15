"""One bounded native-browser login using an encrypted, human-supplied code."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import signal
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import (HTTPRedirectHandler, HTTPSHandler, ProxyHandler,
                            Request, build_opener)

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from glados_auth import CookieStore, session_cache_key
from glados_common import GladosError, email_address
from glados_runtime import PROJECT_ROOT, RuntimePaths, project_path

REPLY_PATH = ".github/glados-manual.reply.json"
CODE_WAIT_SECONDS = 600
LABEL = b"glados-manual-code-v1"


def atomic_json(path, value):
    target = project_path(path)
    parent = project_path(target.parent)
    parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".manual-", suffix=".tmp", dir=str(parent))
        temporary = project_path(name)
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            json.dump(value, handle, ensure_ascii=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            project_path(temporary).unlink()


def read_json(path):
    target = project_path(path)
    if not target.exists():
        return {}
    if target.stat().st_size > 16384:
        raise GladosError("Manual login state is oversized", kind="runtime")
    value = json.loads(target.read_text(encoding="ascii"))
    if not isinstance(value, dict):
        raise GladosError("Manual login state is invalid", kind="runtime")
    return value


def identity(document):
    if (not isinstance(document, dict) or type(document.get("version")) is not int or document["version"] != 1 or
            any(not isinstance(document.get(field), str) or not re.fullmatch(r"[0-9]{1,20}", document[field])
                for field in ("run_id", "attempt")) or
            type(document.get("account")) is not int or not 1 <= document["account"] <= 50 or
            not isinstance(document.get("challenge"), str) or not re.fullmatch(r"[a-f0-9]{32}", document["challenge"])):
        raise GladosError("Manual code challenge identity is invalid", kind="config")
    return {key: document[key] for key in ("version", "run_id", "attempt", "account", "challenge")}


def encrypt_reply(document, code):
    expected = identity(document)
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        raise GladosError("Verification code must contain six digits", kind="config")
    if (type(document.get("expires_at")) not in (int, float) or
            not time.time() < document["expires_at"] <= time.time() + CODE_WAIT_SECONDS + 30):
        raise GladosError("Manual code challenge has expired or has an invalid deadline", kind="auth")
    try:
        key = serialization.load_der_public_key(base64.b64decode(document["public_key"], validate=True))
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size != 3072:
            raise ValueError
        plaintext = json.dumps({**expected, "code": code}, separators=(",", ":")).encode("ascii")
        ciphertext = key.encrypt(plaintext, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                                       algorithm=hashes.SHA256(), label=LABEL))
        return {**expected, "ciphertext": base64.b64encode(ciphertext).decode("ascii")}
    except (ValueError, TypeError, KeyError):
        raise GladosError("Cannot encrypt the reply for this manual login challenge", kind="config") from None


class CodeChallenge:
    def __init__(self, run_id, attempt, account):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        self.document = {"version": 1, "run_id": run_id, "attempt": attempt, "account": account,
                         "challenge": secrets.token_hex(16), "expires_at": time.time() + CODE_WAIT_SECONDS,
                         "public_key": base64.b64encode(self.key.public_key().public_bytes(
                             serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode("ascii")}
        identity(self.document)

    def decrypt(self, envelope):
        try:
            actual = identity(envelope)
        except GladosError:
            return None
        if actual != identity(self.document):
            return None
        if time.time() >= self.document["expires_at"]:
            raise GladosError("Manual code challenge expired", kind="auth")
        try:
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            if len(ciphertext) != self.key.key_size // 8:
                raise ValueError
            value = json.loads(self.key.decrypt(ciphertext, padding.OAEP(
                mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=LABEL)))
            if (not isinstance(value, dict) or identity(value) != identity(self.document) or
                    not isinstance(value.get("code"), str) or not re.fullmatch(r"[0-9]{6}", value["code"])):
                raise ValueError
            return value["code"]
        except (KeyError, ValueError, TypeError, UnicodeError):
            raise GladosError("Encrypted manual code reply is invalid; refusing to guess", kind="auth") from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class GitHubReply:
    def __init__(self, repository, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not token:
            raise GladosError("Manual code relay requires the repository runtime read token", kind="config")
        self.url = f"https://api.github.com/repos/{repository}/contents/{REPLY_PATH}?ref=main"
        self.token = token
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls.load_default_certs()
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=tls), NoRedirect())

    def get(self, timeout):
        request = Request(self.url, headers={"Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "glados-manual-code-relay", "Cache-Control": "no-cache"})
        try:
            with self.opener.open(request, timeout=min(10, timeout)) as response:
                raw = response.read(32769)
            if len(raw) > 32768:
                raise ValueError
            data = json.loads(raw)
            if (data.get("type") != "file" or data.get("path") != REPLY_PATH or
                    data.get("encoding") != "base64" or type(data.get("size")) is not int or data["size"] > 8192):
                raise ValueError
            return json.loads(base64.b64decode("".join(data["content"].split()), validate=True))
        except HTTPError as error:
            if error.code in (404, 429, 502, 503, 504):
                return None
            raise GladosError("Manual code relay HTTP request was rejected", kind="auth-request") from None
        except (TimeoutError, OSError, URLError):
            return None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise GladosError("Manual code relay returned invalid public data", kind="auth-request") from None


class ManualPaths:
    def __init__(self, run_id, attempt, runtime=None):
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,20}", value)
               for value in (run_id, attempt)):
            raise GladosError("Manual login requires valid Actions run identifiers", kind="config")
        self.run_id, self.attempt = run_id, attempt
        self.runtime = runtime or RuntimePaths()
        self.directory = project_path(self.runtime.base / f"manual-{run_id}-{attempt}")
        self.state = project_path(self.directory / "state.json")
        self.challenge = project_path(self.directory / "challenge.json")
        self.process = project_path(self.directory / "process.json")
        self.log = project_path(self.directory / "worker.log")

    def update(self, state, **fields):
        atomic_json(self.state, {"run_id": self.run_id, "attempt": self.attempt, "state": state, **fields})

    def await_code(self, account, relay):
        challenge = CodeChallenge(self.run_id, self.attempt, account)
        atomic_json(self.challenge, challenge.document)
        self.update("awaiting_code", account=account)
        print(f"[GLaDOS] Verification email requested for account {account}; waiting for one encrypted reply", flush=True)
        deadline = time.monotonic() + CODE_WAIT_SECONDS
        while time.monotonic() < deadline:
            envelope = relay.get(deadline - time.monotonic())
            if time.monotonic() >= deadline:
                break
            value = challenge.decrypt(envelope)
            if value is not None:
                self.update("logging_in", account=account)
                return value
            time.sleep(min(5, max(0, deadline - time.monotonic())))
        raise GladosError("Manual verification code timed out; no replacement code requested", kind="auth")


def action_paths():
    if os.getenv("GITHUB_ACTIONS") != "true":
        raise GladosError("Manual login orchestration is only enabled in GitHub Actions", kind="config")
    return ManualPaths(os.getenv("GITHUB_RUN_ID", ""), os.getenv("GITHUB_RUN_ATTEMPT", ""))


def selected_account():
    value = os.getenv("GLADOS_MANUAL_ACCOUNT", "").strip()
    if not value:
        value = read_json(PROJECT_ROOT / ".github/glados-manual-login.trigger").get("account")
    try:
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise ValueError
        number = int(value)
    except (TypeError, ValueError):
        raise GladosError("Manual login account index is invalid", kind="config") from None
    if not 1 <= number <= 50:
        raise GladosError("Manual login account index is out of range", kind="config")
    return number


def worker(paths, account):
    import checkin
    from glados_browser import NativeBrowser

    paths.update("starting", account=account)
    try:
        raw = os.getenv("GLADOS_ACCOUNTS_JSON", "")
        accounts = checkin.parse_accounts(raw)
        if account > len(accounts) or not isinstance(accounts[account - 1], dict):
            raise GladosError("Selected GLaDOS account does not exist", kind="config")
        configured = accounts[account - 1]
        email = email_address(configured.get("email", ""))
        panel = next(value for value in checkin.PANELS if value["name"] == "GLaDOS")
        store = CookieStore(session_cache_key(raw), paths.runtime)
        relay = GitHubReply(os.getenv("GITHUB_REPOSITORY", ""), os.getenv("GLADOS_RELAY_TOKEN", ""))
        with NativeBrowser(proxy=checkin.configured_proxy(panel), runtime=paths.runtime) as browser:
            reply = browser.login_with_code(panel["origin"], email, lambda: paths.await_code(account, relay))
            with checkin.AccountClient(panel, "", browser_user_agent=browser.user_agent,
                                       allow_empty_cookie=True) as client:
                client.accept_browser_cookies(reply.cookies)
                store.put(email, panel["origin"], client.cookie_values())
                atomic_json(paths.directory / "cache-ready.json", {"cached": True})
                cookie = "; ".join(f"{name}={value}" for name, value in client.cookie_values().items())
            paths.update("signing", account=account, cached=True)
            result = checkin.process_browser_account(panel, {**configured, "cookie": cookie}, browser, store=store)
            outcome = result.checkin_outcome
            fields = {"account": account, "success": result.success, "cached": True,
                      "category": outcome.category if outcome else None,
                      "reward": outcome.reward if outcome else None, "code": outcome.code if outcome else None}
            if not result.success:
                fields["message"] = result.message
            paths.update("complete", **fields)
            print("[GLaDOS] Manual login/check-in result: " + json.dumps(fields, ensure_ascii=True), flush=True)
            return 0 if result.success else 1
    except GladosError as error:
        paths.update("complete", account=account, success=False, message=str(error))
        print("[GLaDOS] Manual login stopped: " + str(error), flush=True)
    except Exception as error:
        paths.update("complete", account=account, success=False, message="Manual login failed: " + type(error).__name__)
        print("[GLaDOS] Manual login failed: " + type(error).__name__, flush=True)
    return 1


def start(paths):
    if os.name != "posix" or read_json(paths.process):
        raise GladosError("Manual worker must start once on the Linux runner", kind="runtime")
    paths.runtime.ensure()
    project_path(paths.directory).mkdir(parents=True, exist_ok=True)
    account = selected_account()
    arguments = ["xvfb-run", "-a", "--error-file=/dev/stderr", sys.executable, "-B",
                 str(Path(__file__).resolve()), "worker", "--run-id", paths.run_id,
                 "--attempt", paths.attempt, "--account", str(account)]
    environment = dict(os.environ)
    environment.update({"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    with project_path(paths.log).open("wb") as output:
        process = subprocess.Popen(arguments, cwd=str(PROJECT_ROOT), env=environment,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    atomic_json(paths.process, {"pid": process.pid, "run_id": paths.run_id, "attempt": paths.attempt})
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        state = read_json(paths.state)
        if state.get("state") == "awaiting_code":
            print(f"[GLaDOS] Account {account} is awaiting its code; publish only the public challenge artifact")
            return 0
        if state.get("state") == "complete" or process.poll() is not None:
            print("[GLaDOS] Worker stopped before accepting a code: " + state.get("message", "startup failure"))
            return 1
        time.sleep(1)
    raise GladosError("Manual browser startup timed out", kind="runtime")


def wait(paths):
    deadline = time.monotonic() + CODE_WAIT_SECONDS + 400
    last = None
    while time.monotonic() < deadline:
        state = read_json(paths.state)
        if state.get("state") != last:
            last = state.get("state")
            print("[GLaDOS] Manual worker state: " + str(last), flush=True)
        if last == "complete":
            print("[GLaDOS] Final manual-login result: " + json.dumps(state, ensure_ascii=True), flush=True)
            return 0 if state.get("success") is True else 1
        time.sleep(2)
    raise GladosError("Manual login worker exceeded the bounded wait", kind="runtime")


def cleanup(paths):
    process = read_json(paths.process)
    pid = process.get("pid")
    if os.name != "posix" or type(pid) is not int or pid <= 1:
        return
    try:
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        script = str(Path(__file__).resolve()).encode()
        if (script not in arguments or b"worker" not in arguments or b"--run-id" not in arguments or
                arguments[arguments.index(b"--run-id") + 1] != paths.run_id.encode() or os.getpgid(pid) != pid):
            return
        os.killpg(pid, signal.SIGTERM)
    except (OSError, ValueError, IndexError):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "wait", "cleanup"):
        commands.add_parser(name)
    child = commands.add_parser("worker")
    child.add_argument("--run-id", required=True)
    child.add_argument("--attempt", required=True)
    child.add_argument("--account", type=int, required=True)
    encode = commands.add_parser("encrypt")
    encode.add_argument("--challenge-base64", required=True)
    args = parser.parse_args()
    try:
        if args.command == "encrypt":
            document = json.loads(base64.b64decode(args.challenge_base64, validate=True))
            code = sys.stdin.readline().strip()
            print(json.dumps(encrypt_reply(document, code), separators=(",", ":")))
            return 0
        paths = action_paths()
        if args.command == "worker":
            if (args.run_id, args.attempt) != (paths.run_id, paths.attempt):
                raise GladosError("Worker run identity mismatch", kind="config")
            def cancel(*_):
                raise GladosError("Manual worker cancelled", kind="runtime")
            signal.signal(signal.SIGTERM, cancel)
            return worker(paths, args.account)
        if args.command == "cleanup":
            cleanup(paths)
            return 0
        return start(paths) if args.command == "start" else wait(paths)
    except (GladosError, ValueError, TypeError, KeyError) as error:
        message = str(error) if isinstance(error, GladosError) else "Invalid manual-login control data"
        print("[GLaDOS] " + message)
        return 1


if __name__ == "__main__":
    sys.exit(main())
