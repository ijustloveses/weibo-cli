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


def strip_topic_tags(text: str) -> str:
    """Remove Weibo #topic# tags from *text* while preserving Markdown.

    Weibo topics are paired-hash spans (``#话题#``). We delete those, but never
    touch text inside fenced code blocks (```` ``` ````) or inline code
    (`` ` ``), so `#include`, `# comment`, `color: #fff` etc. survive. Markdown
    headings are safe regardless, since a leading `# ` has no closing hash.
    """
    if not text:
        return text

    # Protect code spans by swapping them for placeholders that contain no '#'.
    placeholders: list[str] = []

    def _stash(match: re.Match) -> str:
        placeholders.append(match.group(0))
        return f"\x00CODE{len(placeholders) - 1}\x00"

    protected = _CODE_SPAN_RE.sub(_stash, text)

    # Drop topic tags from the non-code text.
    protected = _TOPIC_TAG_RE.sub("", protected)

    # Restore code spans.
    def _restore(match: re.Match) -> str:
        return placeholders[int(match.group(1))]

    protected = re.sub(r"\x00CODE(\d+)\x00", _restore, protected)

    # Collapse whitespace runs left behind by removed tags, but keep newlines.
    protected = re.sub(r"[^\S\n]{2,}", " ", protected)
    # Trim trailing spaces on each line and leading/trailing blank space.
    protected = "\n".join(line.rstrip() for line in protected.split("\n"))
    return protected.strip()


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
    return strip_topic_tags(body)


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
    """Decorator: add --json/--yaml options to a Click command."""
    command = click.option("--yaml", "as_yaml", is_flag=True, help="以 YAML 格式输出")(command)
    command = click.option("--json", "as_json", is_flag=True, help="以 JSON 格式输出")(command)
    return command


def handle_command(credential, *, action, render=None, as_json=False, as_yaml=False) -> Any:
    """Run action → route output: JSON / YAML(non-TTY) / Rich render.

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

        # Output routing
        if as_json:
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
