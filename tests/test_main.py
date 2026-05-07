from __future__ import annotations

import importlib.util
from pathlib import Path


def load_main_module():  # type: ignore[no-untyped-def]
    module_path = Path(__file__).resolve().parents[1] / "main.py"
    spec = importlib.util.spec_from_file_location("clipdock_main_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_with_project_venv_invokes_local_python(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    module = load_main_module()
    calls: dict[str, object] = {}
    venv_python = Path(r"C:\tmp\clipdock\.venv\Scripts\python.exe")

    monkeypatch.setattr(module, "_project_venv_python", lambda: venv_python)
    monkeypatch.setattr(module.sys, "executable", r"C:\Python314\python.exe")
    monkeypatch.setattr(module.sys, "argv", ["main.py", "--help"])

    def fake_run(command, check, env):  # type: ignore[no-untyped-def]
        calls["command"] = command
        calls["check"] = check
        calls["env"] = env

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module._run_with_project_venv(["--help"]) == 0
    assert calls["command"] == [str(venv_python), str(module._project_root() / "main.py"), "--help"]
    assert calls["check"] is False
    assert calls["env"][module.BOOTSTRAP_ENV_VAR] == "1"


def test_missing_dependency_message_mentions_editable_install() -> None:
    module = load_main_module()

    message = module._missing_dependency_message("tomli_w")

    assert "tomli-w" in message
    assert "pip install -e" in message
