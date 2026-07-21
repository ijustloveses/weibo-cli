"""Personal & profile commands: profile, weibos, following, followers, reposts, home."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import click
from rich.panel import Panel

from ._common import console, format_count, handle_command, parse_weibo_time, require_auth, structured_output_options
from ..exceptions import WeiboApiError
from .renderers import render_repost_list, render_user_table, render_weibo_list

# Stop paging once we're this many pages deep, as a safety net against
# runaway pagination on prolific accounts.
_MAX_SINCE_PAGES = 20


def _weibo_id(status: dict) -> str:
    """Stable unique id for a status (mblogid preferred, mid as fallback)."""
    return str(status.get("mblogid") or status.get("bid") or status.get("mid") or status.get("id") or "")


def _extract_statuses(data) -> list[dict]:
    """Pull the status list out of a get_user_weibos response."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("list", data.get("statuses", []))
    return []


@click.command()
@click.argument("uid")
@structured_output_options
def profile(uid, as_json, as_yaml):
    """查看用户资料 (weibo profile <uid>)"""
    cred = require_auth()

    def _render(data):
        user = data.get("user", data)
        lines = []
        name = user.get("screen_name", "未知")
        verified = " ✓" if user.get("verified") else ""
        lines.append(f"[bold cyan]{name}{verified}[/bold cyan]")
        if user.get("verified_reason"):
            lines.append(f"[dim]{user['verified_reason']}[/dim]")
        if user.get("description"):
            lines.append(f"\n{user['description']}")

        lines.append("")
        stats = []
        if user.get("followers_count") is not None:
            stats.append(f"[bold]粉丝[/bold] {format_count(user['followers_count'])}")
        if user.get("friends_count") is not None:
            stats.append(f"[bold]关注[/bold] {format_count(user['friends_count'])}")
        if user.get("statuses_count") is not None:
            stats.append(f"[bold]微博[/bold] {format_count(user['statuses_count'])}")
        if stats:
            lines.append("  |  ".join(stats))

        if user.get("location"):
            lines.append(f"\n📍 {user['location']}")
        if user.get("gender"):
            gender = "♂ 男" if user["gender"] == "m" else "♀ 女" if user["gender"] == "f" else ""
            if gender:
                lines.append(f"  {gender}")

        console.print(Panel("\n".join(lines), title=f"@{name}", border_style="cyan", padding=(0, 1)))

        tabs = data.get("tabList", [])
        if tabs:
            tab_names = [t.get("tabName", t.get("name", "")) for t in tabs]
            console.print(f"[dim]可用 Tab: {' | '.join(tab_names)}[/dim]")

    def _action(client):
        return client.get_profile(uid)

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)


@click.command()
@click.argument("uid")
@click.option("--page", "-p", default=1, help="页码")
@click.option("--count", "-n", default=20, help="条数")
@structured_output_options
def weibos(uid, page, count, as_json, as_yaml):
    """查看用户微博列表 (weibo weibos <uid>)"""
    cred = require_auth()

    def _render(data):
        statuses = data if isinstance(data, list) else data.get("list", data.get("statuses", []))
        render_weibo_list(statuses, count=count, show_user=False)

    def _action(client):
        return client.get_user_weibos(uid, page=page)

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)


@click.command()
@click.argument("uid")
@click.option("--page", "-p", default=1, help="页码")
@structured_output_options
def following(uid, page, as_json, as_yaml):
    """查看用户关注列表 (weibo following <uid>)"""
    cred = require_auth()

    def _render(data):
        users = data.get("users", []) if isinstance(data, dict) else data
        render_user_table(users, title="关注列表", empty_msg="[yellow]暂无关注[/yellow]")

    def _action(client):
        return client.get_following(uid, page=page)

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)


@click.command()
@click.argument("uid")
@click.option("--page", "-p", default=1, help="页码")
@structured_output_options
def followers(uid, page, as_json, as_yaml):
    """查看用户粉丝列表 (weibo followers <uid>)"""
    cred = require_auth()

    def _render(data):
        users = data.get("users", []) if isinstance(data, dict) else data
        render_user_table(users, title="粉丝列表", empty_msg="[yellow]暂无粉丝[/yellow]")

    def _action(client):
        return client.get_followers(uid, page=page)

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)


@click.command()
@click.argument("mblogid")
@click.option("--count", "-n", default=10, help="转发条数")
@click.option("--page", "-p", default=1, help="页码")
@structured_output_options
def reposts(mblogid, count, page, as_json, as_yaml):
    """查看微博转发 (weibo reposts <mblogid>)"""
    cred = require_auth()

    def _render(data):
        repost_list = data.get("data", []) if isinstance(data, dict) else data
        render_repost_list(repost_list, count=count)

    def _action(client):
        weibo = client.get_weibo_detail(mblogid)
        weibo_id = str(weibo.get("id", weibo.get("mid", "")))
        return client.get_reposts(weibo_id, page=page, count=count)

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)


@click.command()
@click.option("--count", "-n", default=20, help="条数 (1-50)")
@structured_output_options
def home(count, as_json, as_yaml):
    """查看关注者 Feed (weibo home) 🏠"""
    cred = require_auth()

    def _render(data):
        statuses = data.get("statuses", [])
        render_weibo_list(statuses, count=count, border_style="green", empty_msg="[yellow]暂无关注者微博[/yellow]")

    def _action(client):
        return client.get_friends_timeline(count=min(count, 50))

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)


@click.command()
@click.argument("uid")
@click.option("--since", "since_id", default=None, help="游标 mblogid：只返回比它更新的微博")
@click.option("--days", default=1, help="未给 --since 时，返回最近 N 天的微博 (默认 1)")
@click.option("--max-pages", default=_MAX_SINCE_PAGES, help=f"最多翻页数 (默认 {_MAX_SINCE_PAGES})")
@click.option("--full", "fetch_full", is_flag=True, help="对长微博自动补拉全文（每条长微博多一次请求，较慢）")
@structured_output_options
def since(uid, since_id, days, max_pages, fetch_full, as_json, as_yaml):
    """增量拉取：某用户比 <mblogid> 更新的微博，或最近 N 天的微博

    \b
    weibo since <uid> --since <mblogid>   # 比该条更新的所有微博
    weibo since <uid>                     # 最近 1 天的微博
    weibo since <uid> --days 3            # 最近 3 天的微博
    weibo since <uid> --full              # 长微博自动补拉全文

    列表接口对长微博只返回摘要（约前 180 字）。加 --full 后，会对
    标记为长微博的条目逐条调用 detail 补全 longText 全文。
    """
    cred = require_auth()

    cutoff = None
    if not since_id:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    def _action(client):
        collected: list[dict] = []
        for page in range(1, max_pages + 1):
            data = client.get_user_weibos(uid, page=page, count=20)
            statuses = _extract_statuses(data)
            if not statuses:
                break

            reached_end = False
            for s in statuses:
                # Cursor mode: stop as soon as we hit the cursor weibo itself.
                if since_id and _weibo_id(s) == str(since_id):
                    reached_end = True
                    break
                # Time mode: statuses are newest-first, so once one is older
                # than the cutoff, everything after it is older too.
                if cutoff is not None:
                    ts = parse_weibo_time(s.get("created_at", ""))
                    if ts is not None and ts < cutoff:
                        reached_end = True
                        break
                collected.append(s)

            if reached_end or len(statuses) < 20:
                break

        # Optionally enrich long weibos with their full body via the detail API.
        if fetch_full:
            for s in collected:
                if not s.get("isLongText"):
                    continue
                mblogid = _weibo_id(s)
                if not mblogid:
                    continue
                try:
                    detail = client.get_weibo_detail(mblogid)
                except WeiboApiError:
                    continue  # keep the summary if a single detail fails
                long_text = detail.get("longText")
                if isinstance(long_text, dict):
                    s["longText"] = long_text

        return {"uid": str(uid), "count": len(collected), "statuses": collected}

    def _render(data):
        statuses = data.get("statuses", [])
        if since_id:
            header = f"[dim]@{uid} 比 {since_id} 更新的微博：{len(statuses)} 条[/dim]"
        else:
            header = f"[dim]@{uid} 最近 {days} 天的微博：{len(statuses)} 条[/dim]"
        console.print(header)
        render_weibo_list(statuses, count=len(statuses), show_user=False, empty_msg="[yellow]没有更新的微博[/yellow]")

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)
