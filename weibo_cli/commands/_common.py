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
