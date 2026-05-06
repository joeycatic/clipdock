# clipdock

Terminal-first media downloader built on `yt-dlp`, with an interactive curses UI and a diagnostics-friendly CLI fallback.

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)
![yt-dlp](https://img.shields.io/badge/yt--dlp-backed-111827?style=flat-square)
![Terminal UI](https://img.shields.io/badge/UI-curses-18181B?style=flat-square)
![Platforms](https://img.shields.io/badge/Platforms-YouTube%20%7C%20TikTok%20%7C%20Reddit%20%7C%20Instagram%20%7C%20X%20%7C%20Pinterest-27272A?style=flat-square)

## Preview

<p align="center">
  <img src="./docs/assets/terminal-preview.png" alt="clipdock terminal preview" width="88%" />
</p>

<p align="center">
  <img src="./docs/assets/cli-help.png" alt="clipdock CLI help output" width="88%" />
</p>

## Overview

`clipdock` is for people who want a fast local downloader without a browser extension or desktop wrapper.

It supports a guided terminal UI for selecting:

- platform
- URL
- optional playlist mode for YouTube playlist URLs
- playlist queue review with per-item removal before download
- video vs audio mode
- quality profile
- output directory
- filename template

It also supports a non-interactive CLI mode for scripting.

## Highlights

| Capability | Details |
| --- | --- |
| Interactive terminal app | Full-screen curses interface with keyboard navigation |
| Plain CLI mode | `--non-interactive` mode for shell usage, diagnostics, and scripts |
| Platform-aware prompts | YouTube, TikTok, Reddit, Instagram, X, Pinterest |
| Quality selection | Best available, capped resolutions, audio export profiles |
| Local-first | Downloads directly to your machine |
| `ffmpeg` aware | Detects whether muxing and audio conversion are available |
| Diagnostics | Inspect formats, simulate downloads, and enable debug reports |
| Session support | Cookie-file and browser-cookie auth for restricted media |

## Supported Platforms

| Platform | Notes |
| --- | --- |
| YouTube | Videos, shorts, music, public posts |
| TikTok | Videos and share links |
| Reddit | Posts, hosted videos, `v.redd.it` |
| Instagram | Posts, reels, stories, highlights |
| X / Twitter | Post URLs from `x.com` and `twitter.com` |
| Pinterest | Pins and video pins |

Support depends on what `yt-dlp` can extract successfully from the source.

## Requirements

- Python 3.12 or newer recommended
- `yt-dlp`
- on Windows interactive mode: `windows-curses` is installed automatically from `requirements.txt`
- optional but strongly recommended: `ffmpeg`

Without `ffmpeg`:

- video downloads still work
- audio conversion profiles such as MP3 and M4A are unavailable
- some best-quality muxing paths are reduced

## Installation

### Option 1: virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Option 2: existing Python environment

```bash
pip install -r requirements.txt
```

### Optional: install ffmpeg

On macOS with Homebrew:

```bash
brew install ffmpeg
```

On Windows, install from `requirements.txt` in a normal Python environment. That includes the Windows curses runtime needed for the full-screen UI.

## Quick Start

### Interactive mode

```bash
python main.py
```

In the interactive UI, select `playlist` and enable it before running a YouTube playlist URL. When playlist metadata loads, open `queue` to review the entry list and remove items before starting the download. After a successful download, press `N` to start another URL with the current settings still loaded.

### Open directly with a URL

```bash
python main.py "https://www.youtube.com/watch?v=example"
```

### Download a YouTube playlist

```bash
python main.py --non-interactive --playlist "https://www.youtube.com/playlist?list=example"
```

### Non-interactive mode

```bash
python main.py --non-interactive --platform youtube --quality 1080p "https://www.youtube.com/watch?v=example"
```

### Audio-only export

```bash
python main.py --non-interactive --audio-only --quality mp3 "https://www.youtube.com/watch?v=example"
```

### Inspect available formats

```bash
python main.py --non-interactive --list-formats "https://www.reddit.com/video/example"
```

### Simulate without downloading

```bash
python main.py --non-interactive --simulate --quality 720p "https://www.youtube.com/watch?v=example"
```

### Use browser cookies for restricted media

```bash
python main.py --non-interactive --cookies-from-browser chrome "https://www.instagram.com/reel/example/"
```

## CLI Options

| Flag | Purpose |
| --- | --- |
| `--platform` | Choose the expected source platform or `auto` |
| `--audio-only` | Download only audio |
| `--playlist` | Allow playlist downloads instead of forcing a single item |
| `--quality` | Set target quality such as `best`, `1080p`, `mp3`, `m4a` |
| `--output-dir` | Choose the destination folder |
| `--filename-template` | Pass a `yt-dlp` output template |
| `--non-interactive` | Disable curses UI |
| `--interactive` | Force curses UI |
| `--list-formats` | Print the extractor-visible format table and exit |
| `--simulate` | Resolve metadata, quality, and output path without downloading |
| `--debug` | Print detailed policy and `yt-dlp` diagnostics |
| `--cookies` | Use a Netscape-format cookie file |
| `--cookies-from-browser` | Load cookies from a local browser profile |

## Interactive Controls

### Platform picker

- `j` / `k` or arrow keys to move
- number keys to jump to a platform
- `Enter` to confirm
- `q` to cancel

### Main screen

- `j` / `k` or arrows to move between settings
- `h` / `l` or arrows to cycle mode / quality where applicable
- `Enter` to edit a field or start the download
- number keys `1-9` and `0` to jump between command rows
- `q` to exit

### Playlist queue

- open `queue` after loading a playlist
- `j` / `k` or arrows move between entries
- `Enter` or `Space` toggles the selected item in or out of the queue
- `d` or `Delete` removes the selected item from the queue
- `r` restores the selected item
- `a` restores the full playlist queue
- `q` closes the queue view

After a successful interactive download, press `N` to jump straight to the next URL prompt and keep your current platform/output settings.

## How It Works

1. infer the platform from the URL when possible
2. fetch remote metadata with `yt-dlp`
3. normalize the URL and apply platform-specific extractor policy
4. build quality options based on available formats and `ffmpeg`
5. optionally inspect or simulate the plan
6. run the transfer with live progress hooks
7. resolve the final output path and write to disk

## Project Layout

```text
main.py            thin entrypoint
clipdock/          CLI, UI, downloader, diagnostics, platform policy
tests/             pytest regression suite
requirements.txt   runtime dependency list
docs/              additional usage and contributor notes
```

## Notes

- this project is a thin local app around `yt-dlp`, not a hosted service
- extraction behavior can change as upstream sites change
- success depends on the current extractor support in `yt-dlp`

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md).
