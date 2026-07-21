"""Search, hot-search and feed commands."""

from __future__ import annotations

import click
from rich.panel import Panel
from rich.table import Table

from ._common import console, format_count, full_text, handle_command, require_auth, structured_output_options
from .renderers import render_comment_list, render_weibo_list


@click.command(name="hot")
@click.option("--count", "-n", default=50, help="条数 (默认50)")
@structured_output_options
def hot(count, as_json, as_yaml):
    """查看微博热搜榜 🔥"""
    from ..auth import get_credential

    cred = get_credential()

    def _render(data):
        table = Table(title="🔥 微博热搜", show_lines=False, padding=(0, 1))
        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("热搜词", style="bold")
        table.add_column("标签", width=4)
        table.add_column("热度", justify="right", style="cyan")

        items = data.get("realtime") or data.get("band_list") or []
        for i, item in enumerate(items[:count], 1):
            word = item.get("word", item.get("note", ""))
            icon = item.get("icon_desc", item.get("label_name", ""))
            num = item.get("num", item.get("raw_hot", ""))

            icon_color = "red" if icon == "沸" else "yellow" if icon == "热" else "green" if icon == "新" else ""
            icon_text = f"[{icon_color}]{icon}[/{icon_color}]" if icon_color and icon else icon
            num_str = format_count(num) if num else ""

            table.add_row(str(i), word, icon_text, num_str)

        console.print(table)

    def _action(client):
        return client.get_hot_search()

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)


@click.command()
@click.option("--count", "-n", default=10, help="条数 (1-20)")
@structured_output_options
def feed(count, as_json, as_yaml):
    """查看热门微博 Feed 📰"""
    from ..auth import get_credential

    cred = get_credential()

    def _render(data):
        statuses = data.get("statuses", [])
        render_weibo_list(statuses, count=count, border_style="blue", empty_msg="[yellow]暂无热门微博[/yellow]")

    def _action(client):
        return client.get_hot_timeline(count=min(count, 20))

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)


@click.command()
@click.argument("mblogid")
@structured_output_options
def detail(mblogid, as_json, as_yaml):
    """查看微博详情 (weibo detail <mblogid>)"""
    cred = require_auth()

    def _render(data):
        user = data.get("user", {})
        name = user.get("screen_name", "未知")
        verified = " ✓" if user.get("verified") else ""
        text = full_text(data)
        source = data.get("source", "")
        created = data.get("created_at", "")
        reposts = data.get("reposts_count", 0)
        comments_count = data.get("comments_count", 0)
        likes = data.get("attitudes_count", 0)
        reads = data.get("reads_count", 0)

        content = f"[bold cyan]{name}{verified}[/bold cyan]"
        if user.get("verified_reason"):
            content += f"  [dim]{user['verified_reason']}[/dim]"
        content += f"\n[dim]{created}  via {source}[/dim]\n\n"
        content += f"{text}\n\n"

        if data.get("pic_ids"):
            content += f"[dim]📷 {len(data['pic_ids'])} 张图片[/dim]\n"

        content += f"👁 {reads}  💬 {comments_count}  🔁 {reposts}  ❤️ {likes}"

        console.print(Panel(content, title=f"微博 {data.get('mblogid', '')}", border_style="cyan", padding=(0, 1)))

    def _action(client):
        return client.get_weibo_detail(mblogid)

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)


@click.command()
@click.argument("mblogid")
@click.option("--count", "-n", default=20, help="评论条数")
@structured_output_options
def comments(mblogid, count, as_json, as_yaml):
    """查看微博评论 (weibo comments <mblogid>)"""
    cred = require_auth()

    def _render(data):
        comment_list = data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []
        render_comment_list(comment_list, count=count)

    def _action(client):
        weibo = client.get_weibo_detail(mblogid)
        weibo_id = str(weibo.get("id", weibo.get("mid", "")))
        return client.get_comments(weibo_id, count=count)

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)


@click.command()
@click.option("--count", "-n", default=16, help="条数 (默认16)")
@structured_output_options
def trending(count, as_json, as_yaml):
    """查看实时搜索趋势 📈"""
    from ..auth import get_credential

    cred = get_credential()

    def _render(data):
        items = data.get("realtime", [])
        table = Table(title="📈 实时搜索趋势", show_lines=False, padding=(0, 1))
        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("关键词", style="bold")
        table.add_column("描述", style="dim")

        for i, item in enumerate(items[:count], 1):
            word = item.get("word", "")
            desc = str(item.get("description", ""))
            table.add_row(str(i), word, desc[:40])

        console.print(table)

    def _action(client):
        return client.get_search_band()

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)


@click.command()
@click.argument("keyword")
@click.option("--count", "-n", default=10, help="显示条数")
@click.option("--page", "-p", default=1, help="页码")
@structured_output_options
def search(keyword, count, page, as_json, as_yaml):
    """搜索微博 (weibo search <关键词>) 🔍"""
    from ..auth import get_credential

    cred = get_credential()

    def _render(data):
        # Mobile API returns cards in data.cards or data.data.cards
        cards = []
        if isinstance(data, dict):
            cards_data = data.get("data", data)
            if isinstance(cards_data, dict):
                cards = cards_data.get("cards", [])

        # Extract weibos from cards
        statuses = []
        for card in cards:
            if card.get("card_type") == 9:
                mblog = card.get("mblog", {})
                if mblog:
                    statuses.append(mblog)
            elif card.get("card_group"):
                for sub in card["card_group"]:
                    if sub.get("card_type") == 9:
                        mblog = sub.get("mblog", {})
                        if mblog:
                            statuses.append(mblog)

        if not statuses:
            console.print(f"[yellow]未找到 \"{keyword}\" 相关微博[/yellow]")
            return

        render_weibo_list(statuses, count=count, border_style="magenta")

    def _action(client):
        return client.search_weibo(keyword, page=page)

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)
