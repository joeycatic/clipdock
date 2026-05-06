# Usage Guide

## Interactive Mode

Run:

```bash
python main.py
```

The app will guide you through:

1. selecting a platform
2. entering a URL
3. resolving metadata
4. choosing mode and quality
5. running the download

After a successful interactive download, press `N` on the completion screen to jump straight to a fresh URL prompt and download another video without restarting the app.

For YouTube playlists, enable the `playlist` setting in the interactive command list before you run the URL. After the playlist metadata loads, open `queue` to inspect the entries and remove anything you do not want downloaded.

### Playlist Queue Controls

- `j` / `k` or arrows move between entries
- `Enter` or `Space` toggles the selected item in or out of the queue
- `d` or `Delete` removes the selected item from the queue
- `r` restores the selected item
- `a` restores all removed items
- `q` closes the queue view

## Non-Interactive Mode

For scripts or direct shell usage:

```bash
python main.py --non-interactive --platform youtube --quality best "<url>"
```

### Examples

Download best video:

```bash
python main.py --non-interactive --platform youtube --quality best "<url>"
```

Download capped video quality:

```bash
python main.py --non-interactive --platform youtube --quality 720p "<url>"
```

Download a YouTube playlist:

```bash
python main.py --non-interactive --playlist "https://www.youtube.com/playlist?list=<id>"
```

Download MP3:

```bash
python main.py --non-interactive --audio-only --quality mp3 "<url>"
```

Inspect extractor-visible formats:

```bash
python main.py --non-interactive --list-formats "<url>"
```

Simulate the full plan without downloading:

```bash
python main.py --non-interactive --simulate --quality best "<url>"
```

Enable debug diagnostics:

```bash
python main.py --non-interactive --debug "<url>"
```

Use a cookie file:

```bash
python main.py --non-interactive --cookies ./cookies.txt "<url>"
```

Use browser cookies:

```bash
python main.py --non-interactive --cookies-from-browser chrome "<url>"
```

## Output Directory

By default files go to:

```text
~/Downloads
```

Override it with:

```bash
python main.py --output-dir ~/Media "<url>"
```

## Filename Template

Default template:

```text
%(title)s.%(ext)s
```

Override it with:

```bash
python main.py --filename-template "%(uploader)s - %(title)s.%(ext)s" "<url>"
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
- the interactive UI does not expose auth or diagnostics controls yet; use the CLI flags for those flows

## Manual Smoke Pass

For downloader or policy changes, manually verify:

1. one known Reddit `v.redd.it` URL with `--quality best`
2. one capped-quality Reddit run such as `--quality 480p`
3. one `--list-formats` run on a supported platform
4. one `--simulate` run on a supported platform
5. one auth-gated failure path produces remediation hints instead of only a raw extractor error
