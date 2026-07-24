"""Shared renderers for CLI output.

Eliminates rendering duplication across search.py, personal.py, etc.
Each renderer takes parsed API data and prints Rich output.
"""

from __future__ import annotations

import click
from rich.table import Table

from ._common import console, format_count, strip_html, to_markdown


# ── Weibo card (Markdown) ───────────────────────────────────────────


def render_weibo_card(
    s: dict,
    index: int,
    *,
    border_style: str = "blue",
    show_user: bool = True,
    max_text: int | None = None,
) -> None:
    """Print a single weibo as Markdown (YAML frontmatter + body).

    Used by: feed, home, search, weibos, since.
    """
    click.echo(to_markdown(s))


def render_weibo_list(
    statuses: list[dict],
    *,
    count: int = 20,
    border_style: str = "blue",
    show_user: bool = True,
    empty_msg: str = "[yellow]暂无微博[/yellow]",
) -> None:
    """Print a list of weibo statuses as Markdown blocks separated by rules."""
    if not statuses:
        console.print(empty_msg)
        return
    for i, s in enumerate(statuses[:count], 1):
        if i > 1:
            click.echo()  # blank line between weibos
        click.echo(to_markdown(s))


# ── User list table ─────────────────────────────────────────────────


def render_user_table(users: list[dict], *, title: str = "用户列表", empty_msg: str = "[yellow]暂无用户[/yellow]") -> None:
    """Render a user list as a Rich Table. Used by following, followers."""
    if not users:
        console.print(empty_msg)
        return

    table = Table(title=title, show_lines=False, padding=(0, 1))
    table.add_column("UID", style="dim", width=12)
    table.add_column("昵称", style="bold")
    table.add_column("粉丝", justify="right")
    table.add_column("简介", max_width=40)

    for u in users:
        uid_str = str(u.get("id", u.get("idstr", "")))
        name = u.get("screen_name", "")
        verified = " ✓" if u.get("verified") else ""
        follower_count = format_count(u.get("followers_count", 0))
        desc = (u.get("description", "") or "")[:40]
        table.add_row(uid_str, f"{name}{verified}", follower_count, desc)

    console.print(table)


# ── Comment list ────────────────────────────────────────────────────


def render_comment_list(comments: list[dict], *, count: int = 20) -> None:
    """Render comment entries. Used by comments command."""
    if not comments:
        console.print("[yellow]暂无评论[/yellow]")
        return

    for c in comments[:count]:
        user = c.get("user", {})
        name = user.get("screen_name", "未知")
        text = strip_html(c.get("text", ""))
        created = c.get("created_at", "")
        likes = c.get("like_counts", 0)

        console.print(f"  [bold]{name}[/bold]  [dim]{created}[/dim]")
        console.print(f"    {text}")
        if likes:
            console.print(f"    [dim]❤️ {likes}[/dim]")
        console.print()


# ── Repost list ─────────────────────────────────────────────────────


def render_repost_list(reposts: list[dict], *, count: int = 10) -> None:
    """Render repost entries. Used by reposts command."""
    if not reposts:
        console.print("[yellow]暂无转发[/yellow]")
        return

    for _i, r in enumerate(reposts[:count], 1):
        user = r.get("user", {})
        name = user.get("screen_name", "未知")
        text = strip_html(r.get("text", ""))
        created = r.get("created_at", "")

        console.print(f"  [bold]{name}[/bold]  [dim]{created}[/dim]")
        console.print(f"    {text}")
        console.print()
