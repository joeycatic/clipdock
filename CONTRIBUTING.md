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

Windows interactive mode support comes from the `windows-curses` dependency in `requirements.txt`.

## Project Rules

- keep the app local-first
- keep the dependency surface small
- prefer standard library functionality where it is already sufficient
- use `yt-dlp` as the extraction backend instead of re-implementing site logic

## Testing Guidance

Run the automated suite before opening a PR:

```bash
python -m pytest
```

Then manually verify:

1. interactive mode still starts
2. non-interactive mode still parses arguments
3. at least one known supported URL still resolves metadata
4. audio-only and video modes still select sane quality profiles
5. `--list-formats` and `--simulate` still produce sensible reports

## Smoke Checklist

Use one known good URL per advertised platform when making downloader changes:

1. YouTube
2. TikTok
3. Reddit
4. Instagram
5. X
6. Pinterest

When a site requires authentication or region-specific access, verify the remediation path with `--cookies` or `--cookies-from-browser`.

## Documentation Standard

If behavior changes for users, update:

- `README.md` for setup or core usage
- `docs/USAGE.md` for workflow and operational detail
