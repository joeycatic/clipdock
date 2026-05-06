# Contributing

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optional:

```bash
brew install ffmpeg
```

## Project Rules

- keep the app local-first
- keep the dependency surface small
- prefer standard library functionality where it is already sufficient
- use `yt-dlp` as the extraction backend instead of re-implementing site logic

## Testing Guidance

This project does not currently have an automated test suite.

Before opening a PR, manually verify:

1. interactive mode still starts
2. non-interactive mode still parses arguments
3. at least one known supported URL still resolves metadata
4. audio-only and video modes still select sane quality profiles

## Documentation Standard

If behavior changes for users, update:

- `README.md` for setup or core usage
- `docs/USAGE.md` for workflow and operational detail
