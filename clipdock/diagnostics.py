from __future__ import annotations

from typing import Any

from .models import FailureHelp, ResolvedPlan


def render_formats(info: dict[str, Any]) -> str:
    rows = [
        "formats",
        "-------",
        "id               ext   resolution   note",
    ]
    formats = info.get("formats") or []
    if not formats and (info.get("_type") == "playlist" or isinstance(info.get("entries"), list)):
        rows.append("-                -     -            playlist metadata is using entry summaries; open a single video URL for exact formats")
        return "\n".join(rows)
    for fmt in formats:
        resolution = "audio"
        if fmt.get("height"):
            resolution = f"{fmt.get('width') or '?'}x{fmt.get('height')}"
        note_parts = []
        if fmt.get("fps"):
            note_parts.append(f"{fmt['fps']}fps")
        if fmt.get("vcodec") == "none":
            note_parts.append("audio-only")
        elif fmt.get("acodec") == "none":
            note_parts.append("video-only")
        elif fmt.get("acodec"):
            note_parts.append("muxed")
        if fmt.get("format_note"):
            note_parts.append(str(fmt["format_note"]))
        rows.append(f"{str(fmt.get('format_id') or '-'):<16} {str(fmt.get('ext') or '-'):<5} {resolution:<12} {' | '.join(note_parts) or '-'}")
    return "\n".join(rows)


def render_simulation(plan: ResolvedPlan, info: dict[str, Any]) -> str:
    lines = [
        "simulation",
        "----------",
        f"platform   {plan.platform_label}",
        f"url        {plan.normalized_url}",
        f"mode       {plan.mode}",
        f"playlist   {'enabled' if plan.playlist else 'single item'}",
        f"quality    {plan.quality.label}",
        f"selector   {plan.quality.format_selector}",
        f"output     {plan.output_path}",
        f"ffmpeg     {'ready' if plan.has_ffmpeg else 'missing'}",
        f"auth       {plan.auth_description}",
        f"title      {info.get('title') or plan.normalized_url}",
        f"creator    {info.get('uploader') or info.get('channel') or info.get('uploader_id') or 'Unknown creator'}",
    ]
    if info.get("_type") == "playlist" or isinstance(info.get("entries"), list):
        lines.append(f"entries    {info.get('playlist_count') or len(info.get('entries') or [])}")
        lines.append("policy     unavailable playlist items will be skipped during download")
    if plan.detected_platform and plan.detected_platform != plan.platform_key:
        lines.append(f"warning    url looks like {plan.detected_platform}")
    for note in plan.policy_notes:
        lines.append(f"policy     {note}")
    return "\n".join(lines)


def render_debug_report(plan: ResolvedPlan, info: dict[str, Any]) -> str:
    lines = [
        "debug",
        "-----",
        f"platform_key      {plan.platform_key}",
        f"platform_label    {plan.platform_label}",
        f"requested_url     {plan.requested_url}",
        f"normalized_url    {plan.normalized_url}",
        f"mode              {plan.mode}",
        f"playlist          {'enabled' if plan.playlist else 'disabled'}",
        f"quality_key       {plan.quality.key}",
        f"quality_label     {plan.quality.label}",
        f"format_selector   {plan.quality.format_selector}",
        f"auth_source       {plan.auth_description}",
        f"output_path       {plan.output_path}",
        f"ffmpeg            {'ready' if plan.has_ffmpeg else 'missing'}",
        f"formats_seen      {len(info.get('formats') or [])}",
    ]
    for note in plan.policy_notes:
        lines.append(f"policy_note       {note}")
    for key in sorted(plan.option_preview):
        lines.append(f"ydl_option        {key}={plan.option_preview[key]}")
    return "\n".join(lines)


def render_failure(error: Exception | str, help_text: FailureHelp | None = None, *, debug: bool = False) -> str:
    message = str(error)
    lines = [f"error      {message}"]
    if help_text is not None:
        lines.append(f"reason     {help_text.summary}")
        for hint in help_text.hints:
            lines.append(f"hint       {hint}")
    if debug:
        lines.append(f"raw        {message}")
    return "\n".join(lines)
