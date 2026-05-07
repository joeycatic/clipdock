from __future__ import annotations

import os

import pytest

from clipdock import cli
from clipdock.downloader import download_media


LIVE_URL = "https://www.youtube.com/watch?v=BaW_jenozKc"

pytestmark = pytest.mark.live


def require_live_tests() -> None:
    if os.environ.get("CLIPDOCK_LIVE_TESTS") != "1":
        pytest.skip("Set CLIPDOCK_LIVE_TESTS=1 to run live smoke tests.")


def test_live_extract_and_simulate(tmp_path) -> None:  # type: ignore[no-untyped-def]
    require_live_tests()
    args = cli.parse_args(
        [
            "--non-interactive",
            "--simulate",
            "--platform",
            "youtube",
            "--output-dir",
            str(tmp_path),
            LIVE_URL,
        ]
    )
    context = cli.build_download_context(args, LIVE_URL)

    assert context.info.get("title")
    assert context.plan.output_path


def test_live_download_small_public_fixture(tmp_path) -> None:  # type: ignore[no-untyped-def]
    require_live_tests()
    args = cli.parse_args(
        [
            "--non-interactive",
            "--platform",
            "youtube",
            "--quality",
            "360p",
            "--output-dir",
            str(tmp_path),
            LIVE_URL,
        ]
    )
    context = cli.build_download_context(args, LIVE_URL)

    output_path = download_media(context.settings, context.quality, quiet=True)

    assert os.path.exists(output_path)
