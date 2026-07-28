"""Common helpers for CLI commands."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import click
from rich.console import Console

from ..auth import Credential, get_credential
from ..client import WeiboClient
from ..exceptions import AuthRequiredError, WeiboApiError, SessionExpiredError, error_code_for_exception

console = Console()


# ── Shared formatters ───────────────────────────────────────────────


def strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text or "")


# Weibo topic tags look like #话题#: a pair of hashes wrapping a run of text
# that contains no '#' and no newline. Markdown headings (`# foo`) use a single
# leading hash with no closing hash, so they never match this pattern.
_TOPIC_TAG_RE = re.compile(r"#[^#\n]+#")

# Fenced code blocks (```...```) and inline code (`...`). Matched together so a
# stray backtick inside a fenced block can't desync the inline pass.
_CODE_SPAN_RE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)

# Zero-width / BOM characters Weibo sprinkles at the end of posts.
_ZERO_WIDTH_RE = re.compile(r"[​‌‍⁠﻿]")

# Weibo's t.cn short links. Media/links are surfaced separately, so these are
# redundant noise in the body text.
_TCN_RE = re.compile(r"https?://t\.cn/[A-Za-z0-9]+")


def strip_topic_tags(text: str, link_map: dict[str, str] | None = None) -> str:
    """Remove Weibo #topic# tags and zero-width chars from *text*, and either
    resolve or drop t.cn short links, while preserving Markdown.

    Weibo topics are paired-hash spans (``#话题#``). We delete those; we never
    touch text inside fenced code blocks (```` ``` ````) or inline code
    (`` ` ``), so `#include`, `# comment`, `color: #fff` etc. survive. Markdown
    headings are safe regardless, since a leading `# ` has no closing hash.

    t.cn short links carry no meaning on their own. When *link_map* (a
    short_url → display mapping, from the weibo's url_struct) resolves a link,
    we replace it inline with that display — a Markdown link ``[title](url)``
    when the link has a title, else the bare URL — so its surrounding context
    (e.g. "网址在此：<url>") is preserved. Unresolved short links are dropped.
    """
    if not text:
        return text

    # Zero-width chars are always noise; strip them everywhere first.
    text = _ZERO_WIDTH_RE.sub("", text)

    # Protect code spans by swapping them for placeholders that contain no '#'.
    placeholders: list[str] = []

    def _stash(match: re.Match) -> str:
        placeholders.append(match.group(0))
        return f"\x00CODE{len(placeholders) - 1}\x00"

    protected = _CODE_SPAN_RE.sub(_stash, text)

    # Drop topic tags from the non-code text.
    protected = _TOPIC_TAG_RE.sub("", protected)

    # Resolve t.cn links to their long URL when known; drop them otherwise.
    link_map = link_map or {}

    def _resolve_tcn(match: re.Match) -> str:
        short = match.group(0)
        # url_struct may key on either http or https form; try both.
        return (
            link_map.get(short)
            or link_map.get(short.replace("https://", "http://"))
            or link_map.get(short.replace("http://", "https://"))
            or ""
        )

    protected = _TCN_RE.sub(_resolve_tcn, protected)

    # Restore code spans.
    def _restore(match: re.Match) -> str:
        return placeholders[int(match.group(1))]

    protected = re.sub(r"\x00CODE(\d+)\x00", _restore, protected)

    # Collapse whitespace runs left behind by removed tags, but keep newlines.
    protected = re.sub(r"[^\S\n]{2,}", " ", protected)
    # Trim trailing spaces on each line and leading/trailing blank space.
    protected = "\n".join(line.rstrip() for line in protected.split("\n"))
    return protected.strip()


def _tcn_link_map(status: dict) -> dict[str, str]:
    """Map t.cn short_url → display string from a weibo's url_struct.

    The display is a Markdown link ``[title](url)`` when titled, else the bare
    URL. Lets the body keep inline links (resolved) instead of dropping them.
    """
    out: dict[str, str] = {}
    for rec in _link_records(status):
        if rec.get("short_url"):
            out[rec["short_url"]] = rec["display"]
    return out


def full_text(status: dict) -> str:
    """Return a weibo's full body, preferring long-text content over the
    truncated text_raw/text.

    Long weibos (isLongText) carry the complete body in
    longText.longTextContent when fetched with isGetLongText=1; the top-level
    text_raw is only a ~180-char preview.
    """
    long_text = status.get("longText")
    long_content = ""
    if isinstance(long_text, dict):
        long_content = long_text.get("longTextContent") or long_text.get("content") or ""
    short = status.get("text_raw") or status.get("text") or ""
    body = strip_html(long_content if len(long_content) > len(short) else short)
    return strip_topic_tags(body, _tcn_link_map(status))


# ── Rich media / repost extraction ──────────────────────────────────


def _pic_urls(status: dict) -> list[str]:
    """Highest-quality URL for each attached image, in order."""
    infos = status.get("pic_infos")
    urls: list[str] = []
    if isinstance(infos, dict):
        # pic_ids preserves order; pic_infos is keyed by id.
        ids = status.get("pic_ids") or list(infos.keys())
        for pid in ids:
            info = infos.get(pid)
            if not isinstance(info, dict):
                continue
            for quality in ("largest", "original", "large", "bmiddle"):
                node = info.get(quality)
                if isinstance(node, dict) and node.get("url"):
                    urls.append(node["url"])
                    break
    return urls


def _video_url(status: dict) -> str | None:
    """The video's web page URL (https://video.weibo.com/show?fid=...).

    We deliberately do NOT return the raw mp4 stream: those URLs are
    signed + time-limited (Expires/ssig) and stop working after a while.
    The video page is stable and shareable.
    """
    page = status.get("page_info")
    if not isinstance(page, dict):
        return None
    if page.get("object_type") != "video" and page.get("type") not in ("video", "11"):
        return None

    # object_id is the fid used by the video page, e.g. "1034:5324190534795325".
    fid = page.get("object_id") or page.get("oid")
    if fid and ":" in str(fid):
        return f"https://video.weibo.com/show?fid={fid}"

    # Fallback: a video.weibo.com link already present in url_struct.
    for u in status.get("url_struct") or []:
        long_url = (u or {}).get("long_url") or ""
        if "video.weibo.com" in long_url:
            return long_url
    return None


def _link_records(status: dict) -> list[dict]:
    """Raw link records from url_struct: short_url, long_url, display string.

    Skips video.weibo.com links, since those are already surfaced as the
    weibo's video URL and would otherwise be listed twice.
    """
    out: list[dict] = []
    for u in status.get("url_struct") or []:
        if isinstance(u, dict) and u.get("long_url"):
            long_url = u["long_url"]
            if "video.weibo.com" in long_url:
                continue
            title = u.get("url_title") or ""
            # Prefer a Markdown link ([title](url)) when a title is available;
            # fall back to the bare URL otherwise.
            display = f"[{title}]({long_url})" if title else long_url
            out.append(
                {
                    "short_url": u.get("short_url") or "",
                    "long_url": long_url,
                    "title": title,
                    "display": display,
                }
            )
    return out


def _expanded_links(status: dict) -> list[str]:
    """Resolve t.cn short links to their long URLs (display strings)."""
    return [rec["display"] for rec in _link_records(status)]


_MD_LINK_RE = re.compile(r"^\[.*\]\((.*)\)$", re.DOTALL)


def _link_url(display: str) -> str:
    """Extract the raw URL from a link display string.

    Handles both the Markdown form ``[title](url)`` and a bare ``url``.
    """
    m = _MD_LINK_RE.match(display)
    return m.group(1) if m else display


def extract_media(status: dict, *, _depth: int = 0) -> dict:
    """Structured media + repost context for a weibo.

    Returns a dict with keys: images (list[str]), video (str|None),
    links (list[str]), retweet (dict|None). The retweet, when present, carries
    the original author, its body text, and its own media (one level deep).

    For reposts, Weibo copies the original weibo's page_info/pics onto the outer
    status too. Those media belong to the *original*, not the reposter, so we
    subtract the original's media from the outer layer to avoid mis-attributing
    (and duplicating) them.
    """
    outer_link_recs = _link_records(status)
    media: dict = {
        "images": _pic_urls(status),
        "video": _video_url(status),
        "links": [rec["display"] for rec in outer_link_recs],
        "retweet": None,
    }
    rt = status.get("retweeted_status")
    if isinstance(rt, dict) and _depth == 0:
        user = rt.get("user") or {}
        rt_media = extract_media(rt, _depth=1)
        media["retweet"] = {
            "author": user.get("screen_name"),
            "uid": user.get("idstr") or user.get("id"),
            "mblogid": rt.get("mblogid"),
            "text": full_text(rt),
            "media": rt_media,
        }
        # Media inherited from the original must not appear on the outer layer.
        if media["video"] and media["video"] == rt_media.get("video"):
            media["video"] = None
        rt_images = set(rt_media.get("images") or [])
        media["images"] = [u for u in media["images"] if u not in rt_images]

        # Links are trickier: in the waterfall (mymblog) Weibo attaches the
        # SOURCE weibo's url_struct to the OUTER status while leaving the inner
        # retweeted_status.url_struct empty — so plain outer-minus-inner
        # subtraction can't catch it. A link genuinely authored by the reposter
        # has its short_url (t.cn/...) present in the reposter's OWN raw text;
        # an inherited source link does not. Keep only the former on the outer
        # layer, and make sure inherited ones still show under the source block.
        outer_own_text = status.get("text_raw") or status.get("text") or ""
        rt_link_displays = set(rt_media.get("links") or [])
        kept: list[str] = []
        inherited: list[str] = []
        for rec in outer_link_recs:
            short = rec["short_url"]
            display = rec["display"]
            if display in rt_link_displays:
                continue  # already attributed to the source
            if short and short in outer_own_text:
                kept.append(display)  # the reposter's own link
            else:
                inherited.append(display)  # belongs to the source
        media["links"] = kept
        if inherited:
            src_media = media["retweet"]["media"]
            existing = src_media.get("links") or []
            src_media["links"] = existing + [x for x in inherited if x not in existing]
    return media


def all_image_urls(status: dict) -> list[str]:
    """Every image URL in a weibo, including images in the reposted original."""
    media = extract_media(status)
    urls = list(media.get("images") or [])
    rt = media.get("retweet")
    if rt:
        urls.extend((rt.get("media") or {}).get("images") or [])
    return urls


def _media_lines(media: dict, *, indent: str = "") -> list[str]:
    """Human-readable annotation lines for an extract_media() result."""
    lines: list[str] = []
    for img in media.get("images") or []:
        lines.append(f"{indent}📷 {img}")
    if media.get("video"):
        lines.append(f"{indent}🎬 {media['video']}")
    for link in media.get("links") or []:
        lines.append(f"{indent}🔗 {link}")
    return lines


def full_text_rich(status: dict) -> str:
    """full_text() plus appended annotation lines for images, video, links,
    and quoted/reposted content — so media-only or repost weibos are legible.
    """
    body = full_text(status)
    parts = [body]
    media = extract_media(status)

    # A link already resolved inline in the body (e.g. "网址在此：<url>") needn't
    # be repeated as a separate 🔗 annotation. Drop those from the outer links.
    outer_links = media.get("links") or []
    kept_links = [link for link in outer_links if _link_url(link) not in body]
    if len(kept_links) != len(outer_links):
        media = {**media, "links": kept_links}

    ann = _media_lines(media)
    if ann:
        parts.append("\n".join(ann))

    rt = media.get("retweet")
    if rt:
        # Header line: source author + a Markdown link to the source weibo.
        author = rt.get("author") or "未知"
        src_id = rt.get("mblogid")
        src_uid = rt.get("uid")
        if src_id and src_uid:
            url = f"https://weibo.com/{src_uid}/{src_id}"
            header = f"↩️ 转发自 @{author}（[源微博]({url})）:"
        else:
            header = f"↩️ 转发自 @{author}:"
        block = [header]
        if rt.get("text"):
            block.append(rt["text"])
        block.extend(_media_lines(rt.get("media") or {}))
        parts.append("\n".join(block))

    return "\n\n".join(p for p in parts if p.strip())


def to_markdown(status: dict) -> str:
    """Render a weibo as Markdown with a YAML frontmatter header.

    The header carries the stable metadata (id, url, timestamp, long-text flag);
    the body is the full text plus media/repost annotations. Volatile engagement
    counts (comments/reposts/attitudes) are intentionally omitted.
    """
    user = status.get("user") or {}
    uid = user.get("idstr") or user.get("id") or ""
    mblogid = status.get("mblogid") or status.get("bid") or ""
    url = f"https://weibo.com/{uid}/{mblogid}" if uid and mblogid else ""

    header = [
        "---",
        f"mblogid: {mblogid}",
        f"url: {url}",
        f"created_at: {format_weibo_time(status.get('created_at', ''))}",
        f"is_long_text: {str(bool(status.get('isLongText'))).lower()}",
        "---",
    ]
    body = full_text_rich(status)
    return "\n".join(header) + "\n\n" + body


def parse_weibo_time(created_at: str) -> datetime | None:
    """Parse Weibo's `created_at` string into a timezone-aware datetime.

    Weibo uses the Twitter-style format: "Sat Mar 14 07:20:55 +0800 2026".
    Returns None if the value is missing or unparseable.
    """
    if not created_at:
        return None
    try:
        dt = parsedate_to_datetime(created_at)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    # Normalize naive datetimes to UTC so comparisons never raise.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_weibo_time(created_at: str) -> str:
    """Format Weibo's `created_at` as 'YYYY-mm-dd HH:MM:SS' in its own timezone.

    Keeps the original offset (Beijing +0800) rather than converting to UTC,
    matching what weibo.com shows. Falls back to the raw string if unparseable.
    """
    dt = parse_weibo_time(created_at)
    if dt is None:
        return created_at or ""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_count(n: int | str) -> str:
    """Format large numbers with 万."""
    try:
        n = int(n)
    except (ValueError, TypeError):
        return str(n)
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


def require_auth() -> Credential:
    """Get credential or raise AuthRequiredError."""
    cred = get_credential()
    if not cred:
        console.print("[yellow]⚠️  未登录[/yellow]，使用 [bold]weibo login[/bold] 扫码登录")
        raise AuthRequiredError()
    return cred


def structured_output_options(command):
    """Decorator: add --json/--yaml/--md options to a Click command."""
    command = click.option("--md", "as_md", is_flag=True, help="以 Markdown (YAML frontmatter) 格式输出")(command)
    command = click.option("--yaml", "as_yaml", is_flag=True, help="以 YAML 格式输出")(command)
    command = click.option("--json", "as_json", is_flag=True, help="以 JSON 格式输出")(command)
    return command


def _markdown_output(data: Any) -> str:
    """Render command result as Markdown. Handles both a single weibo and a
    {statuses: [...]} list payload.
    """
    if isinstance(data, dict) and isinstance(data.get("statuses"), list):
        blocks = [to_markdown(s) for s in data["statuses"]]
        return "\n\n".join(blocks)
    if isinstance(data, dict):
        return to_markdown(data)
    return str(data)


def handle_command(credential, *, action, render=None, as_json=False, as_yaml=False, as_md=False) -> Any:
    """Run action → route output: JSON / YAML / Markdown / Rich render.

    Also supports SessionExpiredError auto browser refresh retry.
    """
    try:
        # First attempt
        try:
            with WeiboClient(credential) as client:
                data = action(client)
        except SessionExpiredError:
            from ..auth import extract_browser_credential
            fresh = extract_browser_credential()
            if fresh:
                with WeiboClient(fresh) as client:
                    data = action(client)
            else:
                raise

        # Output routing. --md takes priority and works regardless of TTY.
        if as_md:
            click.echo(_markdown_output(data))
        elif as_json:
            click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        elif as_yaml or not sys.stdout.isatty():
            try:
                import yaml
                click.echo(yaml.dump(data, allow_unicode=True, default_flow_style=False))
            except ImportError:
                click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        elif render:
            render(data)
        return data

    except WeiboApiError as exc:
        code = error_code_for_exception(exc)
        console.print(f"[red]❌ [{code}] {exc}[/red]")
        return None
