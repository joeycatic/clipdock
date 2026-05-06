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

Download MP3:

```bash
python main.py --non-interactive --audio-only --quality mp3 "<url>"
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
