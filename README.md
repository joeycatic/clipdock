# clipdock

Terminal-first media downloader built on `yt-dlp`, with an interactive curses UI, a packaged CLI, named presets, duplicate detection, and clipboard watch mode.

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

It supports:

- a guided terminal UI for selecting platform, URL, mode, quality, output path, and playlist queue
- in-app `history` and `doctor` panels inside the interactive UI
- a plain CLI for scripting and diagnostics
- named presets for repeatable download workflows
- duplicate detection before download
- clipboard watch mode for daily-use automation

## Highlights

| Capability | Details |
| --- | --- |
| Interactive terminal app | Full-screen curses interface with keyboard navigation |
| Packaged CLI | `clipdock "<url>"`, `python -m clipdock`, and editable installs |
| Presets | Save reusable download profiles such as `music` or `youtube-1080` |
| Duplicate handling | Detect existing downloads by extractor/media ID when available, then fall back to normalized URLs |
| Clipboard watch | Poll the clipboard, detect supported URLs, and prompt or auto-download |
| History and doctor | Inspect prior downloads and verify local runtime health |
| Diagnostics | Inspect formats, simulate downloads, and enable debug reports |
| Session support | Cookie-file and browser-cookie auth for restricted media |

## Supported Platforms

| Platform | Notes |
| --- | --- |
| YouTube | Videos, shorts, music, public posts, playlists |
| TikTok | Videos and share links |
| Reddit | Posts, hosted videos, `v.redd.it` |
| Instagram | Posts, reels, stories, highlights |
| X / Twitter | Post URLs from `x.com` and `twitter.com` |
| Pinterest | Pins and video pins |

Support depends on what `yt-dlp` can extract successfully from the source.

## Requirements

- Python 3.12 or newer
- optional but strongly recommended: `ffmpeg`
- Windows interactive mode uses `windows-curses`, installed automatically with the package

Without `ffmpeg`:

- video downloads still work
- audio conversion profiles such as MP3 and M4A are unavailable
- some best-quality muxing paths are reduced

## Installation

### Recommended

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Compatibility entrypoint

`python main.py` still works, but the primary interface is now the packaged `clipdock` command.

### Optional: install ffmpeg

On macOS with Homebrew:

```bash
brew install ffmpeg
```

## Quick Start

### Interactive mode

```bash
clipdock
```

After a successful interactive download, press `N` on the completion screen to jump straight to another URL while keeping the current platform and output settings.

### Direct download

```bash
clipdock "https://www.youtube.com/watch?v=example"
```

### Non-interactive mode

```bash
clipdock --non-interactive --platform youtube --quality 1080p "https://www.youtube.com/watch?v=example"
```

### Audio-only export

```bash
clipdock --non-interactive --audio-only --quality mp3 "https://www.youtube.com/watch?v=example"
```

### Download a YouTube playlist

```bash
clipdock --non-interactive --playlist "https://www.youtube.com/playlist?list=example"
```

### Inspect available formats

```bash
clipdock --non-interactive --list-formats "https://www.reddit.com/video/example"
```

### Simulate without downloading

```bash
clipdock --non-interactive --simulate --quality 720p "https://www.youtube.com/watch?v=example"
```

### Use browser cookies for restricted media

```bash
clipdock --non-interactive --cookies-from-browser chrome "https://www.instagram.com/reel/example/"
```

## Presets

Create a preset:

```bash
clipdock preset create music --audio-only --quality mp3 --output-dir ~/Music/Clips
```

Show or list presets:

```bash
clipdock preset show music
clipdock preset list
```

Use a preset directly:

```bash
clipdock use music "https://www.youtube.com/watch?v=example"
clipdock --preset music "https://www.youtube.com/watch?v=example"
```

Presets are stored in the user config file:

- Linux example: `~/.config/clipdock/config.toml`
- Windows example: `%AppData%\clipdock\config.toml`

Example:

```toml
[presets.music]
audio_only = true
quality = "mp3"
output_dir = "~/Music/Clips"
filename_template = "%(uploader)s - %(title)s.%(ext)s"
```

## Duplicate Detection

Before downloading, `clipdock` checks the local history database. When yt-dlp exposes a stable extractor/media ID, clipdock uses that identity first. If not, it falls back to normalized URL matching.

Example flow:

```text
duplicate  This URL was already downloaded on 2026-05-07T00:11:00+00:00:
path       C:\Users\Joey\Downloads\clipdock\video-title.mp4
Download again? [y/N]
```

Duplicate utilities:

```bash
clipdock history
clipdock history --platform youtube --limit 10
clipdock duplicates
clipdock duplicates --hash
clipdock clean-duplicates
clipdock clean-duplicates --hash --dry-run
```

History output includes the recorded platform, preset, quality, file path, and the duplicate identity key used for matching.

Default cleanup behavior:

- group duplicates by normalized URL unless `--hash` is used
- keep the newest existing file
- ask before deleting the older files

## Clipboard Watch Mode

Watch the clipboard and prompt when a supported URL appears:

```bash
clipdock watch
```

Use a preset:

```bash
clipdock watch --preset music
```

Auto-download with duplicate safety:

```bash
clipdock watch --auto --preset music
```

Watch mode behavior:

- polls the clipboard every `0.75` seconds by default
- detects the first supported URL in the clipboard text
- ignores repeated clipboard content
- skips URLs already seen in the current watch session
- prompts with platform, title, preset, and duplicate warning unless `--auto` is enabled

## CLI Commands

| Command | Purpose |
| --- | --- |
| `clipdock "<url>"` | Standard download flow |
| `clipdock use <preset> "<url>"` | Download with a named preset |
| `clipdock watch` | Watch the clipboard for supported URLs |
| `clipdock history` | Show recent download history |
| `clipdock preset create/list/show/delete` | Manage named presets |
| `clipdock duplicates` | Show duplicate downloads |
| `clipdock clean-duplicates` | Remove duplicate files after confirmation |
| `clipdock doctor` | Check local dependencies and writable paths |

Core flags still apply to the download flow:

- `--platform`
- `--audio-only`
- `--playlist`
- `--quality`
- `--output-dir`
- `--filename-template`
- `--preset`
- `--force`
- `--non-interactive`
- `--interactive`
- `--list-formats`
- `--simulate`
- `--debug`
- `--cookies`
- `--cookies-from-browser`

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

Interactive command list also includes:

- `history` to inspect recent downloads without leaving the UI
- `doctor` to inspect runtime health without leaving the UI

### Playlist queue

- open `queue` after loading a playlist
- `j` / `k` or arrows move between entries
- `Enter` or `Space` toggles the selected item in or out of the queue
- `d` or `Delete` removes the selected item from the queue
- `r` restores the selected item
- `a` restores the full playlist queue
- `q` closes the queue view

Interactive duplicate detection now prompts before starting a duplicate transfer.

## Doctor

Run:

```bash
clipdock doctor
```

Current checks:

- Python runtime
- `ffmpeg` availability
- clipboard access
- config and state path writability
- history database accessibility
- default output directory writability
- curses availability for interactive mode

## Project Layout

```text
main.py            compatibility entrypoint
pyproject.toml     package metadata and console script
clipdock/          CLI, UI, downloader, config, history, watcher, diagnostics
tests/             pytest regression suite
docs/              additional usage notes
```

## Notes

- this project is a thin local app around `yt-dlp`, not a hosted service
- extraction behavior can change as upstream sites change
- success depends on the current extractor support in `yt-dlp`

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md).
