"""Search, hot-search and feed commands."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import click
from rich.table import Table

from ._common import (
    all_image_urls,
    console,
    extract_media,
    format_count,
    handle_command,
    require_auth,
    structured_output_options,
    to_markdown,
)
from ..client import WeiboClient
from ..exceptions import WeiboApiError
from .renderers import render_comment_list, render_weibo_list


@click.command(name="hot")
@click.option("--count", "-n", default=50, help="条数 (默认50)")
@structured_output_options
def hot(count, as_json, as_yaml, as_md):
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

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml, as_md=as_md)


@click.command()
@click.option("--count", "-n", default=10, help="条数 (1-20)")
@structured_output_options
def feed(count, as_json, as_yaml, as_md):
    """查看热门微博 Feed 📰"""
    from ..auth import get_credential

    cred = get_credential()

    def _render(data):
        statuses = data.get("statuses", [])
        render_weibo_list(statuses, count=count, border_style="blue", empty_msg="[yellow]暂无热门微博[/yellow]")

    def _action(client):
        return client.get_hot_timeline(count=min(count, 20))

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml, as_md=as_md)


@click.command()
@click.argument("mblogid")
@structured_output_options
def detail(mblogid, as_json, as_yaml, as_md):
    """查看微博详情 (weibo detail <mblogid>)"""
    cred = require_auth()

    def _render(data):
        click.echo(to_markdown(data))

    def _action(client):
        data = client.get_weibo_detail(mblogid)
        if isinstance(data, dict):
            data["media"] = extract_media(data)
        return data

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml, as_md=as_md)


@click.command()
@click.argument("mblogid")
@click.option("--output", "-o", "output_dir", default=None, help="保存目录 (默认 ./<mblogid>/)")
def download(mblogid, output_dir):
    """下载微博的所有图片 (weibo download <mblogid>)

    含转发原微博的图片。图片带微博 Referer 下载以绕过防盗链。
    """
    cred = require_auth()

    dest = Path(output_dir) if output_dir else Path(mblogid)

    try:
        with WeiboClient(cred) as client:
            data = client.get_weibo_detail(mblogid)
            urls = all_image_urls(data) if isinstance(data, dict) else []

            if not urls:
                console.print("[yellow]该微博没有图片[/yellow]")
                return

            dest.mkdir(parents=True, exist_ok=True)
            console.print(f"[dim]找到 {len(urls)} 张图片，保存到 {dest}/[/dim]")

            ok = 0
            for i, url in enumerate(urls, 1):
                name = Path(urlparse(url).path).name or f"{mblogid}_{i}.jpg"
                target = dest / f"{i:02d}_{name}"
                try:
                    target.write_bytes(client.download_bytes(url))
                    console.print(f"  [green]✓[/green] {target.name}")
                    ok += 1
                except WeiboApiError as exc:
                    console.print(f"  [red]✗[/red] 第 {i} 张失败: {exc}")

            console.print(f"[green]完成：{ok}/{len(urls)} 张[/green]")

    except WeiboApiError as exc:
        console.print(f"[red]❌ {exc}[/red]")


@click.command()
@click.argument("mblogid")
@click.option("--count", "-n", default=20, help="评论条数")
@structured_output_options
def comments(mblogid, count, as_json, as_yaml, as_md):
    """查看微博评论 (weibo comments <mblogid>)"""
    cred = require_auth()

    def _render(data):
        comment_list = data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []
        render_comment_list(comment_list, count=count)

    def _action(client):
        weibo = client.get_weibo_detail(mblogid)
        weibo_id = str(weibo.get("id", weibo.get("mid", "")))
        return client.get_comments(weibo_id, count=count)

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml, as_md=as_md)


@click.command()
@click.option("--count", "-n", default=16, help="条数 (默认16)")
@structured_output_options
def trending(count, as_json, as_yaml, as_md):
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

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml, as_md=as_md)


@click.command()
@click.argument("keyword")
@click.option("--count", "-n", default=10, help="显示条数")
@click.option("--page", "-p", default=1, help="页码")
@structured_output_options
def search(keyword, count, page, as_json, as_yaml, as_md):
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

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml, as_md=as_md)
