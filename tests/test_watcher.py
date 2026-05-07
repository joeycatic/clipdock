from __future__ import annotations

import subprocess
import sys

from clipdock.watcher import extract_supported_url


def test_extract_supported_url_from_surrounding_text() -> None:
    match = extract_supported_url("check this out https://www.youtube.com/watch?v=abc123&t=1 and ignore the rest")

    assert match is not None
    assert match.platform == "youtube"
    assert match.normalized_url == "https://www.youtube.com/watch?v=abc123&t=1"


def test_python_module_entrypoint_supports_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "clipdock", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Interactive multi-platform downloader" in result.stdout

