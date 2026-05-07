from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


DEPENDENCY_PACKAGES = {
    "platformdirs": "platformdirs",
    "pyperclip": "pyperclip",
    "tomli_w": "tomli-w",
    "yt_dlp": "yt-dlp",
}
BOOTSTRAP_ENV_VAR = "CLIPDOCK_SKIP_VENV_BOOTSTRAP"


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _project_venv_python() -> Path | None:
    root = _project_root()
    candidates = [
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _missing_dependency_message(module_name: str) -> str:
    package_name = DEPENDENCY_PACKAGES.get(module_name, module_name)
    project_root = _project_root()
    return (
        f'clipdock could not start because "{package_name}" is not installed for {sys.executable}.\n'
        f"Run `{sys.executable} -m pip install -e {project_root}` or activate `{project_root / '.venv'}` and retry."
    )


def _run_with_project_venv(argv: list[str] | None = None) -> int | None:
    if os.environ.get(BOOTSTRAP_ENV_VAR) == "1":
        return None

    venv_python = _project_venv_python()
    if venv_python is None:
        return None

    try:
        current_python = Path(sys.executable).resolve()
        if venv_python.resolve() == current_python:
            return None
    except OSError:
        pass

    env = os.environ.copy()
    env[BOOTSTRAP_ENV_VAR] = "1"
    command = [str(venv_python), str(_project_root() / "main.py"), *(argv if argv is not None else sys.argv[1:])]
    completed = subprocess.run(command, check=False, env=env)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    try:
        from clipdock.cli import main as clipdock_main
    except ModuleNotFoundError as exc:
        if exc.name not in DEPENDENCY_PACKAGES:
            raise
        fallback_code = _run_with_project_venv(argv)
        if fallback_code is not None:
            return fallback_code
        print(_missing_dependency_message(exc.name), file=sys.stderr)
        return 1
    return clipdock_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
