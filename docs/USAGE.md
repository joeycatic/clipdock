# Usage Guide

## Install

Preferred end-user install:

```bash
pipx install clipdock
```

Preferred local install:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

You can still run `python main.py`, but the primary interface is now the packaged `clipdock` command.

## Standard Downloads

Interactive session:

```bash
clipdock
```

Direct download:

```bash
clipdock "<url>"
```

Non-interactive run:

```bash
clipdock --non-interactive --platform youtube --quality best "<url>"
```

Examples:

```bash
clipdock --non-interactive --quality 720p "<url>"
clipdock --non-interactive --audio-only --quality mp3 "<url>"
clipdock --non-interactive --playlist "https://www.youtube.com/playlist?list=<id>"
clipdock --non-interactive --list-formats "<url>"
clipdock --non-interactive --simulate "<url>"
clipdock --non-interactive --debug "<url>"
clipdock --non-interactive --cookies ./cookies.txt "<url>"
clipdock --non-interactive --cookies-from-browser chrome "<url>"
clipdock --non-interactive --write-subs --sub-lang en "<url>"
clipdock --non-interactive --write-thumbnail --write-info-json "<url>"
clipdock --non-interactive --embed-metadata --remux-video mp4 "<url>"
```

## Presets

Create a preset:

```bash
clipdock preset create music --audio-only --quality mp3 --output-dir ~/Music/Clips
```

Inspect presets:

```bash
clipdock preset list
clipdock preset show music
```

Use a preset:

```bash
clipdock use music "<url>"
clipdock --preset music "<url>"
```

Presets live in the user config file:

- Linux example: `~/.config/clipdock/config.toml`
- Windows example: `%AppData%\clipdock\config.toml`

## Duplicate Detection

Before downloading, `clipdock` checks the local history store. When yt-dlp exposes a stable extractor/media ID, clipdock uses that first; otherwise it falls back to normalized URL matching. If a match is found, the CLI prompts before downloading again unless `--force` is set.

Duplicate tools:

```bash
clipdock history
clipdock history --platform youtube --limit 10
clipdock history --status missing --query reel
clipdock history export --format csv
clipdock history prune-missing
clipdock history open 7
clipdock history redownload 7
clipdock duplicates
clipdock duplicates --hash
clipdock clean-duplicates
clipdock clean-duplicates --hash --dry-run
```

Cleanup behavior:

- URL grouping by default
- optional file-hash grouping with `--hash`
- keeps the newest existing file
- asks before deleting older files

## Doctor

Check the local environment:

```bash
clipdock doctor
```

The doctor command verifies:

- Python runtime visibility
- `ffmpeg` availability
- clipboard access
- config/state path writability
- history database access
- default output directory writability
- curses availability for interactive mode

## Clipboard Watch Mode

Foreground watch:

```bash
clipdock watch run
```

Use a preset:

```bash
clipdock watch run --preset music
```

Auto-download:

```bash
clipdock watch run --auto --preset music
```

Background watch service:

```bash
clipdock watch start --auto --preset music
clipdock watch status
clipdock watch stop
```

Watch mode:

- polls the clipboard every `0.75` seconds by default
- detects the first supported URL in the clipboard text
- ignores repeated clipboard values
- skips URLs already processed in prior watch sessions
- in `--auto` mode, still skips duplicate URLs unless `--force` is set

## Batch Jobs

Run newline-delimited URLs from a file:

```bash
clipdock batch urls.txt --preset music
clipdock batch urls.txt --fail-fast --quality 720p
```

Batch files:

- ignore blank lines
- ignore lines starting with `#`
- continue after failures by default
- emit a final summary for downloads, duplicate skips, and failures

## Interactive Mode

The interactive UI supports:

1. selecting a platform
2. entering a URL
3. resolving metadata
4. choosing mode and quality
5. managing a playlist queue when applicable
6. editing presets, auth, and output extras
7. opening `history`, `doctor`, `formats`, `simulation`, and watch-settings panels
8. running the download

After a successful interactive download, press `N` on the completion screen to jump straight to a fresh URL prompt without restarting the app.

If a duplicate URL is detected in interactive mode, the UI now prompts before starting the transfer.

The main session screen is grouped into:

- `source` for platform, preset, URL, and auth settings
- `download` for playlist mode, queue, mode, and quality
- `output` for destination, naming, and extras
- `inspect` for formats and simulation

The initial platform picker still includes `misc` for:

- `history` to inspect recent downloads inside the UI
- `doctor` to inspect runtime health inside the UI
- `watch` settings to edit saved watch defaults

### Playlist Queue Controls

- `j` / `k` or arrows move between entries
- `Enter` or `Space` toggles the selected item in or out of the queue
- `d` or `Delete` removes the selected item from the queue
- `r` restores the selected item
- `a` restores all removed items
- `q` closes the queue view

## Output and Templates

Default output directory:

```text
~/Downloads
```

Override it with:

```bash
clipdock --output-dir ~/Media "<url>"
```

Default filename template:

```text
%(title)s.%(ext)s
```

Override it with:

```bash
clipdock --filename-template "%(uploader)s - %(title)s.%(ext)s" "<url>"
```

Extra outputs:

```bash
clipdock --write-subs --sub-lang en,en-US "<url>"
clipdock --write-auto-subs "<url>"
clipdock --write-thumbnail --embed-thumbnail "<url>"
clipdock --write-info-json --embed-metadata "<url>"
clipdock --split-chapters "<url>"
clipdock --remux-video mkv "<url>"
```

## ffmpeg Behavior

If `ffmpeg` is available:

- higher quality video+audio muxing works better
- MP3 and M4A conversion profiles are enabled

If `ffmpeg` is missing:

- some video format combinations are reduced
- audio conversion profiles are unavailable

## Known Constraints

- terminal UI requires a reasonably large terminal window
- extraction support depends on `yt-dlp`
- some sites may rate limit or block requests depending on region, login state, or upstream changes
- watch execution remains CLI-only; the curses UI edits watch defaults but does not run the watcher inline

## Manual Smoke Pass

For downloader, policy, preset, or duplicate changes, manually verify:

1. one standard non-interactive download
2. one `--list-formats` run
3. one `--simulate` run
4. one preset-backed download
5. one duplicate prompt flow
6. one `clipdock batch <file>` run with a duplicate and one failure
7. one `clipdock watch start/status/stop` lifecycle
8. one `clipdock watch run --preset <name>` session with a supported URL copied into the clipboard
