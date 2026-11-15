"""Project-bound runtime paths, temporary profiles and process coordination."""
import contextlib
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile

from glados_common import GladosError

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def project_path(value):
    try:
        path = Path(value)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        resolved = path.resolve(strict=False)
        resolved.relative_to(PROJECT_ROOT)
        return resolved
    except (OSError, RuntimeError, ValueError):
        raise GladosError("Runtime output must resolve inside the project", kind="config") from None


class RuntimePaths:
    def __init__(self, base=None):
        self.base = project_path(base or os.getenv("GLADOS_RUNTIME_DIR", "") or ".cache/panels")
        self.tmp = project_path(self.base / "tmp")
        self.home = project_path(self.base / "home")
        self.cache = project_path(self.base / "cache")
        self.config = project_path(self.base / "config")
        self.state = project_path(self.base / "state")
        # Windows known-folder APIs expand USERPROFILE independently of APPDATA.
        self.local = project_path(self.home / "AppData" / "Local")
        self.roaming = project_path(self.home / "AppData" / "Roaming")
        self.crashes = project_path(self.base / "crashes")
        self.browsers = project_path(os.getenv("PLAYWRIGHT_BROWSERS_PATH", "") or self.base / "browsers")

    def ensure(self):
        paths = (self.base, self.tmp, self.home, self.cache, self.config, self.state,
                 self.local, self.roaming, self.crashes, self.browsers)
        # Resolve every prospective output before creating the first directory.
        checked = [project_path(path) for path in paths]
        try:
            for path in checked:
                path.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise GladosError("Cannot create project runtime directories", kind="runtime") from None

    def environment(self):
        return {"TEMP": str(self.tmp), "TMP": str(self.tmp), "TMPDIR": str(self.tmp),
                "HOME": str(self.home), "USERPROFILE": str(self.home),
                "LOCALAPPDATA": str(self.local), "APPDATA": str(self.roaming),
                "XDG_CACHE_HOME": str(self.cache), "XDG_CONFIG_HOME": str(self.config),
                "XDG_STATE_HOME": str(self.state), "PLAYWRIGHT_BROWSERS_PATH": str(self.browsers),
                "CHROME_LOG_FILE": str(project_path(self.base / "chrome.log")),
                "NODE_COMPILE_CACHE": str(project_path(self.cache / "node")),
                "NODE_OPTIONS": "", "DEBUG": "", "PWDEBUG": "0"}

    @contextlib.contextmanager
    def driver_environment(self):
        values = self.environment()
        previous = {name: os.environ.get(name) for name in (*values, "SSLKEYLOGFILE")}
        os.environ.update(values)
        os.environ.pop("SSLKEYLOGFILE", None)
        try:
            yield
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def child_environment(self):
        values = dict(os.environ)
        values.update(self.environment())
        values.pop("SSLKEYLOGFILE", None)
        return values

    def new_profile(self):
        self.ensure()
        return project_path(tempfile.mkdtemp(prefix="chrome-", dir=str(project_path(self.tmp))))

    def remove_profile(self, path):
        target = project_path(path)
        if target.parent != project_path(self.tmp) or not target.name.startswith("chrome-"):
            raise GladosError("Refusing to remove an unowned browser profile", kind="runtime")
        if not target.exists():
            return
        for parent, dirs, files in os.walk(target, followlinks=False):
            for name in dirs + files:
                project_path(Path(parent) / name)
        try:
            shutil.rmtree(target)
        except OSError:
            raise GladosError("Browser profile cleanup failed inside project runtime", kind="runtime") from None


class RunLock:
    def __init__(self, runtime):
        self.runtime = runtime
        self.path = project_path(runtime.base / "browser.lock")
        self.owner = secrets.token_hex(16)
        self.acquired = False

    def __enter__(self):
        self.runtime.ensure()
        project_path(self.path)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise GladosError("Another browser run or a crash lock exists; inspect before removing it", kind="runtime") from None
        except OSError:
            raise GladosError("Cannot acquire project browser lock", kind="runtime") from None
        self.acquired = True
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                json.dump({"pid": os.getpid(), "owner": self.owner}, handle)
        except OSError:
            self.__exit__(None, None, None)
            raise GladosError("Cannot write project browser lock", kind="runtime") from None
        return self

    def __exit__(self, *args):
        if not self.acquired:
            return
        try:
            path = project_path(self.path)
            if path.exists() and json.loads(path.read_text(encoding="ascii")).get("owner") == self.owner:
                path.unlink()
        except (OSError, ValueError, GladosError):
            # Never remove a changed lock that may belong to a different run.
            pass
        self.acquired = False
