"""Native Chrome/CDP check-in through the official page, with one POST per context."""
from dataclasses import dataclass, field
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit

from glados_common import (BROWSER_LOCALE, CHALLENGE_DELAY, CHALLENGE_SUFFIX, GladosError, ORIGINS,
                           TURNSTILE_HOST, auth_denied, bool_setting, int_setting, turnstile_denied)
from glados_runtime import PROJECT_ROOT, RunLock, RuntimePaths, project_path

TOKEN_READY = """() => {
    try { if (window.turnstile && window.turnstile.getResponse()) return true; } catch (_) {}
    return [...document.querySelectorAll('input[name="cf-turnstile-response"]')]
        .some(node => Boolean(node.value));
}"""


@dataclass
class BrowserReply:
    payload: dict = field(repr=False)
    cookies: list = field(default_factory=list, repr=False)


def proxy_options(value):
    if not value:
        return None
    try:
        parts = urlsplit(value)
        scheme = "socks5" if parts.scheme == "socks5h" else parts.scheme
        if (scheme not in ("http", "https", "socks5") or not parts.hostname or
                re.search(r"\s", value) or parts.path not in ("", "/") or parts.query or parts.fragment):
            raise ValueError
        port = parts.port if parts.port is not None else {"http": 80, "https": 443, "socks5": 1080}[scheme]
        if not 1 <= port <= 65535:
            raise ValueError
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        options = {"server": f"{scheme}://{host}:{port}", "bypass": "<-loopback>"}
        if parts.username is not None or parts.password is not None:
            if scheme == "socks5":
                raise ValueError
            for key, raw in (("username", parts.username), ("password", parts.password)):
                decoded = unquote(raw or "")
                if any(ord(char) < 32 or ord(char) == 127 for char in decoded):
                    raise ValueError
                options[key] = decoded
        return options
    except (ValueError, TypeError):
        raise GladosError("Browser proxy is invalid or uses unsupported SOCKS authentication", kind="config") from None


def _chrome_executable(runtime):
    explicit = os.getenv("GLADOS_BROWSER_EXECUTABLE", "").strip()
    channel = os.getenv("GLADOS_BROWSER_CHANNEL", "chrome").strip().lower()
    if channel not in ("chrome", "chromium", "msedge"):
        raise GladosError("GLADOS_BROWSER_CHANNEL must be chrome, chromium or msedge", kind="config")
    candidates = [explicit] if explicit else []
    if not explicit and channel == "chromium":
        candidates.append(runtime.chromium.executable_path)
    elif not explicit and sys.platform == "win32":
        relative = "Microsoft/Edge/Application/msedge.exe" if channel == "msedge" else "Google/Chrome/Application/chrome.exe"
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if os.getenv(variable):
                candidates.append(str(Path(os.environ[variable]) / relative))
    elif not explicit and sys.platform == "darwin":
        candidates.append("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" if channel == "msedge"
                          else "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    elif not explicit:
        names = ("microsoft-edge", "microsoft-edge-stable") if channel == "msedge" else ("google-chrome", "google-chrome-stable")
        candidates.extend(shutil.which(name) for name in names)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise GladosError("Native Chrome/Edge not found; install it or set GLADOS_BROWSER_EXECUTABLE", kind="config")


def _cdp_endpoint(port):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", "/json/version")
        response = connection.getresponse()
        if response.status != 200:
            return None
        raw = response.read(16385)
        if len(raw) > 16384:
            return None
        data = json.loads(raw)
        endpoint = data.get("webSocketDebuggerUrl") if isinstance(data, dict) else None
        if not isinstance(endpoint, str):
            return None
        address = urlsplit(endpoint)
        if (address.scheme != "ws" or address.netloc != f"127.0.0.1:{port}" or address.query or address.fragment or
                not re.fullmatch(r"/devtools/browser/[a-zA-Z0-9-]+", address.path)):
            return None
        return endpoint
    except (OSError, ValueError, TypeError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def _verify_browser_process(browser, pid):
    client = browser.new_browser_cdp_session()
    try:
        data = client.send("SystemInfo.getProcessInfo")
        processes = data.get("processInfo", []) if isinstance(data, dict) else []
        if not isinstance(processes, list) or not any(
                isinstance(item, dict) and item.get("type") == "browser" and type(item.get("id")) is int and
                item["id"] == pid for item in processes):
            raise GladosError("Local CDP endpoint does not belong to the launched Chrome process", kind="browser")
    finally:
        client.detach()


def _checkbox(page):
    for frame in page.frames:
        if urlsplit(frame.url).hostname != TURNSTILE_HOST:
            continue
        client = None
        try:
            client = page.context.new_cdp_session(frame)
            tree = client.send("Page.getFrameTree")["frameTree"]
            frame_id = tree["frame"]["id"]
            nodes = client.send("Accessibility.getFullAXTree", {"frameId": frame_id}).get("nodes", [])
            for node in nodes:
                name = str(node.get("name", {}).get("value", "")).lower()
                properties = {item.get("name"): item.get("value", {}).get("value") for item in node.get("properties", [])}
                target = node.get("backendDOMNodeId")
                if (node.get("role", {}).get("value") != "checkbox" or
                        not any(word in name for word in ("human", "verify")) or
                        properties.get("checked") in (True, "true") or properties.get("disabled") is True or
                        type(target) is not int):
                    continue
                client.send("DOM.focus", {"backendNodeId": target})
                page.keyboard.press("Space")
                print("[GLaDOS] Official verification checkbox activated; waiting for its result")
                return True
        except Exception:
            pass
        finally:
            if client is not None:
                try:
                    client.detach()
                except Exception:
                    pass
    return False


class NativeBrowser:
    def __init__(self, *, proxy=None, runtime=None, headless=None, origins=ORIGINS):
        self.paths = runtime or RuntimePaths()
        self.proxy = proxy_options(proxy)
        self.headless = bool_setting("GLADOS_BROWSER_HEADLESS", False) if headless is None else headless
        self.timeout = int_setting("GLADOS_BROWSER_TIMEOUT_SECONDS", 120, 10, 300) * 1000
        self.origins = tuple(origins)
        self.driver = self.browser = self.process = self.profile = self.lock = None
        self.user_agent = ""
        self.webdriver = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise GladosError("Browser mode requires the pinned Playwright dependency", kind="config") from None
        try:
            self.lock = RunLock(self.paths).__enter__()
            with self.paths.driver_environment():
                self.driver = sync_playwright().start()
            executable = _chrome_executable(self.driver)
            if not self.headless and sys.platform.startswith("linux") and not os.getenv("DISPLAY"):
                raise GladosError("Headed Chrome on Linux requires a display such as Xvfb", kind="config")
            self.profile = self.paths.new_profile()
            # Port 0 sets Chrome's automation flag; allocate a nonzero loopback port instead.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            args = [executable, f"--user-data-dir={self.profile}", f"--remote-debugging-port={port}",
                    "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check",
                    "--disable-background-networking", "--disable-component-update", "--disable-sync",
                    f"--crash-dumps-dir={self.paths.crashes}"]
            args.append("--proxy-server=" + self.proxy["server"] if self.proxy else "--no-proxy-server")
            if self.proxy:
                args.append("--proxy-bypass-list=<-loopback>")
            if self.headless:
                args.append("--headless=new")
            if os.getenv("CI", "").lower() == "true":
                args.append("--no-sandbox")
            args.append("about:blank")
            self.process = subprocess.Popen(args, cwd=str(PROJECT_ROOT), env=self.paths.child_environment(),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            deadline = time.monotonic() + 30
            endpoint = None
            while time.monotonic() < deadline and self.process.poll() is None:
                endpoint = _cdp_endpoint(port)
                if endpoint:
                    break
                time.sleep(0.1)
            if endpoint is None:
                raise GladosError("Native Chrome failed to expose its local CDP endpoint", kind="browser")
            candidate = self.driver.chromium.connect_over_cdp(endpoint, timeout=15000)
            # A port race must not connect to or close somebody else's browser.
            _verify_browser_process(candidate, self.process.pid)
            self.browser = candidate
            context = self.browser.new_context()
            try:
                page = context.new_page()
                metadata = page.evaluate("({ua: navigator.userAgent, webdriver: navigator.webdriver})")
                self.user_agent = metadata.get("ua") if isinstance(metadata, dict) else None
                self.webdriver = metadata.get("webdriver") if isinstance(metadata, dict) else None
            finally:
                context.close()
            if not isinstance(self.user_agent, str) or not self.user_agent or any(ord(c) < 32 for c in self.user_agent):
                raise GladosError("Native Chrome returned an invalid user agent", kind="browser")
            if type(self.webdriver) is not bool or (not self.headless and self.webdriver):
                raise GladosError("Headed native Chrome must not report automation mode; no check-in submitted", kind="config")
            print(f"[GLaDOS] Native Chrome connected; headless={self.headless}, webdriver={self.webdriver}")
            return self
        except Exception as error:
            try:
                self.close()
            except GladosError:
                print("[GLaDOS] Browser startup cleanup failed; inspect the project runtime before retrying")
            if isinstance(error, GladosError):
                raise
            raise GladosError("Native Chrome/CDP startup failed", kind="browser") from None

    def close(self):
        for resource in (self.browser, self.driver):
            if resource is not None:
                try:
                    resource.close() if resource is self.browser else resource.stop()
                except Exception:
                    pass
        self.browser = self.driver = None
        if self.process is not None:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                raise GladosError("Cannot stop Chrome; its project profile and run lock were retained", kind="runtime") from None
        self.process = None
        try:
            if self.profile is not None:
                self.paths.remove_profile(self.profile)
                self.profile = None
        finally:
            if self.lock is not None:
                self.lock.__exit__(None, None, None)
                self.lock = None

    def __exit__(self, *args):
        try:
            self.close()
        except GladosError:
            if not args or args[0] is None:
                raise
            print("[GLaDOS] Browser cleanup failed; the original task failure is preserved")

    def login_with_code(self, origin, email, code_provider):
        if origin not in self.origins:
            raise GladosError("Login origin is not approved", kind="config")
        options = {"locale": BROWSER_LOCALE, "service_workers": "block"}
        if self.proxy:
            options["proxy"] = self.proxy
        context = self.browser.new_context(**options)
        allowed = {"path": None, "payload": None}
        submitted = set()
        core = urlsplit(origin)
        page = context.new_page()

        def guard(route):
            request = route.request
            url = urlsplit(request.url)
            same_origin = (url.scheme, url.netloc) == (core.scheme, core.netloc)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                if same_origin and url.path.startswith("/cdn-cgi/challenge-platform/"):
                    route.continue_()
                    return
                if same_origin:
                    if (request.method != "POST" or url.path != allowed["path"] or url.path in submitted or
                            url.path not in ("/api/authorization", "/api/login")):
                        route.abort()
                        return
                    try:
                        if request.post_data_json != allowed["payload"]:
                            raise ValueError
                    except Exception:
                        route.abort()
                        return
                    submitted.add(url.path)
                elif url.hostname != TURNSTILE_HOST:
                    route.abort()
                    return
            if request.is_navigation_request() and request.frame == page.main_frame and not same_origin:
                route.abort()
                return
            route.continue_()

        def request(path, payload=None):
            allowed["path"] = path if payload is not None else None
            allowed["payload"] = payload
            try:
                result = page.evaluate("""async ({path, payload}) => {
                    const options = {method: payload === null ? 'GET' : 'POST',
                        credentials: 'include', cache: 'no-store', redirect: 'error',
                        signal: AbortSignal.timeout(15000)};
                    if (payload !== null) {
                        options.headers = {'Content-Type': 'application/json'};
                        options.body = JSON.stringify(payload);
                    }
                    const response = await fetch(path, options);
                    const text = await response.text();
                    if (text.length > 1048576) throw new Error('Response too large');
                    return {status: response.status, data: JSON.parse(text)};
                }""", {"path": path, "payload": payload})
                if (not isinstance(result, dict) or result.get("status") != 200 or
                        not isinstance(result.get("data"), dict)):
                    raise ValueError
                return result["data"]
            except Exception:
                raise GladosError("Native browser authentication request failed; not retried", kind="auth-request") from None
            finally:
                allowed["path"] = None
                allowed["payload"] = None

        try:
            context.set_default_timeout(self.timeout)
            context.route("**/*", guard)
            page.goto(origin + "/login", wait_until="domcontentloaded")
            sent = request("/api/authorization", {"address": email, "site": "glados.network"})
            safe_code = sent.get("code") if type(sent.get("code")) is int else "invalid"
            print(f"[GLaDOS] Email authorization result: code={safe_code}, captcha_required={bool(sent.get('captcha_required'))}", flush=True)
            if sent.get("captcha_required") or type(sent.get("code")) is not int or sent["code"] != 0:
                raise GladosError("Email authorization refused or requires additional manual verification", kind="auth")
            code = code_provider()
            if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
                raise GladosError("Manual verification code must contain six digits", kind="auth")
            logged_in = request("/api/login", {"method": "email", "site": "glados.network",
                                              "email": email, "mailcode": code})
            code = None
            safe_code = logged_in.get("code") if type(logged_in.get("code")) is int else "invalid"
            print(f"[GLaDOS] Manual email login result: code={safe_code}", flush=True)
            if type(logged_in.get("code")) is not int or logged_in["code"] != 0:
                raise GladosError("Manual email login was rejected; no extra code requested", kind="auth")
            status = request("/api/user/status")
            data = status.get("data")
            if (type(status.get("code")) is not int or status["code"] != 0 or not isinstance(data, dict) or
                    not isinstance(data.get("email"), str) or data["email"].strip().lower() != email):
                raise GladosError("Manual login did not establish the expected account identity", kind="identity")
            print("[GLaDOS] Manual email login established a verified native Chrome session", flush=True)
            return BrowserReply(status, context.cookies(origin))
        except GladosError:
            raise
        except Exception:
            raise GladosError("Manual native browser login failed; not retried", kind="auth-request") from None
        finally:
            context.close()

    def run(self, origin, cookies, *, expected_email=None, verify_only=False):
        if origin not in self.origins:
            raise GladosError("Browser origin is not an approved GLaDOS origin", kind="config")
        options = {"locale": BROWSER_LOCALE, "service_workers": "block"}
        if self.proxy:
            options["proxy"] = self.proxy
        context = self.browser.new_context(**options)
        attempt = {"request": None, "finished": None, "failure": None, "armed": False}
        nonce = proof = None
        required = False
        core = urlsplit(origin)

        def api(request, path):
            url = urlsplit(request.url)
            return (url.scheme, url.netloc, url.path) == (core.scheme, core.netloc, path)

        def guard(route):
            request = route.request
            url = urlsplit(request.url)
            same_origin = (url.scheme, url.netloc) == (core.scheme, core.netloc)
            if same_origin and request.method not in ("GET", "HEAD", "OPTIONS"):
                if url.path.startswith("/cdn-cgi/challenge-platform/"):
                    route.continue_()
                    return
                if (verify_only or not attempt["armed"] or attempt["request"] is not None or
                        request.method != "POST" or url.path != "/api/user/checkin"):
                    route.abort()
                    return
                try:
                    body = request.post_data_json
                    valid = (isinstance(body, dict) and body.get("nonce") == nonce and body.get("proof") == proof and
                             body.get("token") == core.hostname and (not required or (
                                 body.get("tsState") == "ok" and isinstance(body.get("turnstile"), str) and body["turnstile"])))
                except Exception:
                    valid = False
                if not valid:
                    attempt["failure"] = GladosError("Official page produced invalid verification parameters; POST blocked", kind="challenge")
                    route.abort()
                    return
                attempt["request"] = request
                print(f"[GLaDOS] Browser verification: nonce=True, proof=True, Turnstile={required}, state={'ok' if required else 'off'}")
            elif request.method not in ("GET", "HEAD", "OPTIONS") and url.hostname != TURNSTILE_HOST:
                route.abort()
                return
            if request.is_navigation_request() and request.frame == page.main_frame and not same_origin:
                route.abort()
                return
            route.continue_()

        try:
            context.set_default_timeout(self.timeout)
            context.add_cookies(cookies)
            page = context.new_page()
            context.route("**/*", guard)
            page.on("requestfinished", lambda request: attempt.update(finished=request)
                    if request == attempt["request"] else None)
            page.on("requestfailed", lambda request: attempt.update(failure=GladosError(
                "Check-in transport failed after submission; result unknown, not replayed", submitted=True))
                if request == attempt["request"] else None)
            response = context.request.get(origin + "/api/user/status", max_redirects=0, timeout=self.timeout,
                                           headers={"User-Agent": self.user_agent, "Accept-Language": BROWSER_LOCALE})
            if response.status == 401:
                return BrowserReply({"code": -2}, context.cookies(origin))
            try:
                status = response.json()
            except Exception:
                raise GladosError("Browser status response is not JSON; no email recovery triggered") from None
            if auth_denied(status):
                return BrowserReply(status, context.cookies(origin))
            data = status.get("data") if isinstance(status, dict) else None
            if (response.status != 200 or not isinstance(status, dict) or
                    type(status.get("code")) is not int or status["code"] != 0 or not isinstance(data, dict)):
                raise GladosError("Browser status query was rejected; no email recovery triggered")
            actual_email = data.get("email")
            if expected_email and (not isinstance(actual_email, str) or actual_email.strip().lower() != expected_email):
                raise GladosError("Browser account identity does not match the configured email", kind="identity")
            started = time.monotonic()
            with page.expect_event("requestfinished", predicate=lambda request: api(request, "/api/user/checkin/challenge")
                                   and request.method == "GET", timeout=self.timeout) as loaded:
                page.goto(origin + "/console/checkin", wait_until="domcontentloaded")
                button = page.locator("button.ckb")
                button.wait_for(state="visible")
            issued = time.monotonic()
            challenge_response = loaded.value.response()
            try:
                challenge = challenge_response.json()
            except Exception:
                raise GladosError("Official page challenge is not JSON; check-in not submitted", kind="challenge") from None
            if challenge_response.status in (200, 401, 403) and auth_denied(challenge):
                return BrowserReply(challenge, context.cookies(origin))
            if challenge_response.status != 200:
                raise GladosError("Official page challenge request was rejected; check-in not submitted", kind="challenge")
            nonce = challenge.get("nonce") if isinstance(challenge, dict) else None
            ttl = challenge.get("ttl", 300000) if isinstance(challenge, dict) else None
            if (not isinstance(challenge, dict) or type(challenge.get("code")) not in (int, str) or
                    challenge["code"] not in (0, "0") or not isinstance(nonce, str) or not nonce.strip() or len(nonce) > 4096 or
                    isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not math.isfinite(ttl) or ttl <= CHALLENGE_DELAY * 1000):
                raise GladosError("Official page challenge is invalid; check-in not submitted", kind="challenge")
            try:
                proof = hashlib.sha256((nonce + CHALLENGE_SUFFIX).encode("utf-8")).hexdigest()
            except UnicodeError:
                raise GladosError("Official page nonce encoding is invalid; check-in not submitted", kind="challenge") from None
            required = bool(challenge.get("turnstile"))
            deadline = min(started + ttl / 1000 - 1, issued + self.timeout / 1000)
            interacted = False
            ready = not required
            while not ready and time.monotonic() < deadline:
                ready = bool(page.evaluate(TOKEN_READY))
                if not ready and not interacted:
                    interacted = _checkbox(page)
                if not ready:
                    page.wait_for_timeout(250)
            if verify_only:
                return BrowserReply({"challenge": True, "turnstile_required": required, "ready": ready}, [])
            if not ready:
                raise GladosError("Turnstile was not ready; check-in not submitted", kind="turnstile")
            delay = CHALLENGE_DELAY - (time.monotonic() - issued)
            if delay > 0:
                page.wait_for_timeout(delay * 1000)
            if time.monotonic() >= started + ttl / 1000:
                raise GladosError("Challenge expired before submission", kind="challenge")
            attempt["armed"] = True
            button.click(timeout=self.timeout)
            deadline = time.monotonic() + self.timeout / 1000
            while attempt["finished"] is None and attempt["failure"] is None and time.monotonic() < deadline:
                page.wait_for_timeout(100)
            if attempt["failure"] is not None:
                raise attempt["failure"]
            if attempt["finished"] is None:
                raise GladosError("Check-in response timed out; result unknown, not replayed", submitted=attempt["request"] is not None)
            response = attempt["finished"].response()
            if response.status == 401:
                payload = {"code": -2}
            else:
                payload = response.json()
                known_denial = response.status == 403 and (auth_denied(payload) or turnstile_denied(payload))
                if (response.status != 200 and not known_denial) or not isinstance(payload, dict) or "code" not in payload:
                    raise GladosError("Check-in response was invalid; result unknown, not replayed", submitted=True)
            return BrowserReply(payload, context.cookies(origin + "/api/user/checkin"))
        except GladosError:
            raise
        except Exception:
            raise GladosError("Browser request failed; no automatic POST replay", submitted=attempt["request"] is not None,
                              fallback=attempt["request"] is None) from None
        finally:
            pending_failure = sys.exc_info()[0] is not None
            try:
                context.close()
            except Exception:
                if not pending_failure:
                    raise GladosError("Browser context cleanup failed; check-in not replayed",
                                      submitted=attempt["request"] is not None) from None
                print("[GLaDOS] Browser context cleanup failed; the original result is not replayed")
