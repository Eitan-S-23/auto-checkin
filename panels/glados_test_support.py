"""Shared project-bound runtime for native-browser test fixtures."""
import os

from glados_runtime import RuntimePaths, project_path


def native_fixture_runtime(directory):
    runtime = RuntimePaths(directory)
    if os.name == "posix":
        # Chrome's temporary SingletonSocket must fit in the Unix socket path limit.
        runtime.tmp = project_path(".cache/panels/tmp")
    return runtime
