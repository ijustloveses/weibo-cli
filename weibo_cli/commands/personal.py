"""Personal & profile commands: profile, weibos, following, followers, reposts, home."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
from rich.panel import Panel

from ._common import (
    console,
    extract_media,
    format_count,
    handle_command,
    parse_weibo_time,
    require_auth,
    structured_output_options,
    to_markdown,
)
from ..client import WeiboClient
from ..constants import DEFAULT_USERS_FILE
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


def collect_since(client, uid, *, since_id=None, cutoff=None, max_pages=_MAX_SINCE_PAGES, fetch_full=False) -> list[dict]:
    """Page a user's weibos (newest-first), collecting those newer than a cursor
    or within a time window, then optionally enrich long ones + attach media.

    Exactly one of `since_id` (cursor mblogid) or `cutoff` (aware datetime) drives
    the stop condition; if both are None, collects up to max_pages.
    """
    collected: list[dict] = []
    seen_ids: set[str] = set()
    for page in range(1, max_pages + 1):
        data = client.get_user_weibos(uid, page=page, count=20)
        statuses = _extract_statuses(data)
        if not statuses:
            break

        reached_end = False
        for s in statuses:
            is_pinned = bool(s.get("isTop"))

            # Cursor mode: stop as soon as we hit the cursor weibo itself —
            # but a pinned weibo is out of chronological order, so it must not
            # trigger the stop (it may sit above much newer posts).
            if since_id and _weibo_id(s) == str(since_id):
                if is_pinned:
                    continue
                reached_end = True
                break

            # Time mode: statuses are newest-first, so once one is older than
            # the cutoff everything after it is older too — EXCEPT a pinned
            # weibo, whose old date says nothing about the posts below it.
            if cutoff is not None:
                ts = parse_weibo_time(s.get("created_at", ""))
                if ts is not None and ts < cutoff:
                    if is_pinned:
                        continue  # skip stale pin, keep scanning real posts
                    reached_end = True
                    break

            wid = _weibo_id(s)
            if wid and wid in seen_ids:
                continue  # a pin can also appear again in its natural position
            if wid:
                seen_ids.add(wid)
            collected.append(s)

        if reached_end or len(statuses) < 20:
            break

    # Optionally enrich long weibos with their full body via the detail API.
    # This covers both the outer weibo AND a long reposted source weibo, whose
    # text is truncated ("收起") in the timeline/mymblog response.
    if fetch_full:
        for s in collected:
            _enrich_long_text(client, s)
            rt = s.get("retweeted_status")
            if isinstance(rt, dict):
                _enrich_long_text(client, rt)

    # Attach structured media/repost context.
    for s in collected:
        s["media"] = extract_media(s)

    return collected


def _enrich_long_text(client, status: dict) -> None:
    """If *status* is a long weibo, fetch its detail and graft the full body
    (longText) back in place. No-op for short weibos or on failure.
    """
    if not status.get("isLongText"):
        return
    mblogid = _weibo_id(status)
    if not mblogid:
        return
    try:
        detail = client.get_weibo_detail(mblogid)
    except WeiboApiError:
        return  # keep the summary if a single detail fails
    long_text = detail.get("longText")
    if isinstance(long_text, dict):
        status["longText"] = long_text
    # Some weibos carry the full body in text_raw of the detail response
    # rather than longText.longTextContent; prefer the longer text_raw.
    detail_text = detail.get("text_raw") or ""
    if len(detail_text) > len(status.get("text_raw") or ""):
        status["text_raw"] = detail_text


@click.command()
@click.argument("uid")
@structured_output_options
def profile(uid, as_json, as_yaml, as_md):
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

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml, as_md=as_md)


@click.command()
@click.argument("uid")
@click.option("--page", "-p", default=1, help="页码")
@click.option("--count", "-n", default=20, help="条数")
@structured_output_options
def weibos(uid, page, count, as_json, as_yaml, as_md):
    """查看用户微博列表 (weibo weibos <uid>)"""
    cred = require_auth()

    def _render(data):
        statuses = data if isinstance(data, list) else data.get("list", data.get("statuses", []))
        render_weibo_list(statuses, count=count, show_user=False)

    def _action(client):
        return client.get_user_weibos(uid, page=page)

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml, as_md=as_md)


@click.command()
@click.argument("uid")
@click.option("--page", "-p", default=1, help="页码")
@structured_output_options
def following(uid, page, as_json, as_yaml, as_md):
    """查看用户关注列表 (weibo following <uid>)"""
    cred = require_auth()

    def _render(data):
        users = data.get("users", []) if isinstance(data, dict) else data
        render_user_table(users, title="关注列表", empty_msg="[yellow]暂无关注[/yellow]")

    def _action(client):
        return client.get_following(uid, page=page)

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml, as_md=as_md)


@click.command()
@click.argument("uid")
@click.option("--page", "-p", default=1, help="页码")
@structured_output_options
def followers(uid, page, as_json, as_yaml, as_md):
    """查看用户粉丝列表 (weibo followers <uid>)"""
    cred = require_auth()

    def _render(data):
        users = data.get("users", []) if isinstance(data, dict) else data
        render_user_table(users, title="粉丝列表", empty_msg="[yellow]暂无粉丝[/yellow]")

    def _action(client):
        return client.get_followers(uid, page=page)

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml, as_md=as_md)


@click.command()
@click.argument("mblogid")
@click.option("--count", "-n", default=10, help="转发条数")
@click.option("--page", "-p", default=1, help="页码")
@structured_output_options
def reposts(mblogid, count, page, as_json, as_yaml, as_md):
    """查看微博转发 (weibo reposts <mblogid>)"""
    cred = require_auth()

    def _render(data):
        repost_list = data.get("data", []) if isinstance(data, dict) else data
        render_repost_list(repost_list, count=count)

    def _action(client):
        weibo = client.get_weibo_detail(mblogid)
        weibo_id = str(weibo.get("id", weibo.get("mid", "")))
        return client.get_reposts(weibo_id, page=page, count=count)

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml, as_md=as_md)


@click.command()
@click.option("--count", "-n", default=20, help="条数 (1-50)")
@structured_output_options
def home(count, as_json, as_yaml, as_md):
    """查看关注者 Feed (weibo home) 🏠"""
    cred = require_auth()

    def _render(data):
        statuses = data.get("statuses", [])
        render_weibo_list(statuses, count=count, border_style="green", empty_msg="[yellow]暂无关注者微博[/yellow]")

    def _action(client):
        return client.get_friends_timeline(count=min(count, 50))

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml, as_md=as_md)


@click.command()
@click.argument("uid")
@click.option("--since", "since_id", default=None, help="游标 mblogid：只返回比它更新的微博")
@click.option("--days", default=1, help="未给 --since 时，返回最近 N 天的微博 (默认 1)")
@click.option("--max-pages", default=_MAX_SINCE_PAGES, help=f"最多翻页数 (默认 {_MAX_SINCE_PAGES})")
@click.option("--full", "fetch_full", is_flag=True, help="对长微博自动补拉全文（每条长微博多一次请求，较慢）")
@structured_output_options
def since(uid, since_id, days, max_pages, fetch_full, as_json, as_yaml, as_md):
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
        collected = collect_since(client, uid, since_id=since_id, cutoff=cutoff,
                                  max_pages=max_pages, fetch_full=fetch_full)
        return {"uid": str(uid), "count": len(collected), "statuses": collected}

    def _render(data):
        statuses = data.get("statuses", [])
        if since_id:
            header = f"[dim]@{uid} 比 {since_id} 更新的微博：{len(statuses)} 条[/dim]"
        else:
            header = f"[dim]@{uid} 最近 {days} 天的微博：{len(statuses)} 条[/dim]"
        console.print(header)
        render_weibo_list(statuses, count=len(statuses), show_user=False, empty_msg="[yellow]没有更新的微博[/yellow]")

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml, as_md=as_md)


# ── Archive (batch incremental Markdown export) ─────────────────────

# Matches archive filenames: 20260724_142500_Ra9Q6lN9q.md → captures the
# YYYYmmdd_HHMMSS sort key and the mblogid.
_ARCHIVE_FILE_RE = re.compile(r"^(\d{8}_\d{6})_(.+)\.md$")


def _sanitize(name: str) -> str:
    """Make a string safe for use as a path component."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "")
    return name.strip().strip(".") or "unknown"


def parse_user_list(text: str) -> list[tuple[str, str]]:
    """Parse a users file into [(uid, name), ...].

    Each non-empty, non-comment line is 'uid,name' or 'uid'. Whitespace around
    fields is trimmed; lines starting with '#' are comments.
    """
    users: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",", 1)]
        uid = parts[0]
        name = parts[1] if len(parts) > 1 and parts[1] else uid
        if uid:
            users.append((uid, name))
    return users


def _latest_archived_mblogid(user_dir: Path) -> str | None:
    """The mblogid of the newest already-archived weibo in *user_dir*, by the
    YYYYmmdd_HHMMSS prefix in the filename. None if the dir has no archive files.
    """
    best_key = ""
    best_mblogid = None
    if not user_dir.is_dir():
        return None
    for f in user_dir.iterdir():
        if not f.is_file():
            continue
        m = _ARCHIVE_FILE_RE.match(f.name)
        if m and m.group(1) > best_key:
            best_key = m.group(1)
            best_mblogid = m.group(2)
    return best_mblogid


def _archive_filename(status: dict) -> str | None:
    """Build '{YYYYmmdd}_{HHMMSS}_{mblogid}.md' from a status; None if unusable."""
    mblogid = _weibo_id(status)
    dt = parse_weibo_time(status.get("created_at", ""))
    if not mblogid or dt is None:
        return None
    return f"{dt.strftime('%Y%m%d_%H%M%S')}_{mblogid}.md"


@click.command()
@click.argument("users_file", required=False, type=click.Path(dir_okay=False))
@click.option("--days", default=1, help="首次抓取时的时间窗口（天，默认 1）")
@click.option("--max-pages", default=_MAX_SINCE_PAGES, help=f"每个用户最多翻页数 (默认 {_MAX_SINCE_PAGES})")
@click.option("--no-full", is_flag=True, help="不补拉长微博全文（默认补拉）")
@click.option("--delay", default=2.5, show_default=True, help="请求最小间隔秒数（越大越安全越慢）")
@click.option("--out", "out_dir", default="weibos", help="输出根目录 (默认 ./weibos)")
def archive(users_file, days, max_pages, no_full, delay, out_dir):
    """批量增量归档：把用户列表里每个人的新微博存成 Markdown 文件

    \b
    用户列表文件：每行 'uid,用户名'（# 开头为注释），例如：
        1560906700,阑夕
        1699432410,新华社

    \b
    不指定文件时，默认读取 ~/.config/weibo-cli/users.txt

    \b
    输出结构：
        weibos/{uid}_{name}/{YYYYmmdd}_{HHMMSS}_{mblogid}.md

    每个用户首次抓取最近 --days 天；已有归档时，从文件名时间最大者的
    mblogid 作游标，只抓比它更新的微博（增量）。
    """
    cred = require_auth()
    fetch_full = not no_full

    users_path = Path(users_file) if users_file else DEFAULT_USERS_FILE
    if not users_path.is_file():
        if users_file:
            console.print(f"[red]❌ 找不到用户列表文件：{users_path}[/red]")
        else:
            console.print(f"[yellow]未指定用户列表，且默认文件不存在：{users_path}[/yellow]")
            console.print("  提示：在该路径创建 users.txt（每行 'uid,用户名'），或用 weibo archive <文件> 指定")
        return

    users = parse_user_list(users_path.read_text(encoding="utf-8"))
    if not users:
        console.print("[yellow]用户列表为空[/yellow]")
        return

    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)

    total_new = 0
    try:
        with WeiboClient(cred, request_delay=delay) as client:
            for uid, name in users:
                # uid must be numeric; a non-numeric uid usually means the file
                # has 'name,uid' reversed or is missing the uid.
                if not str(uid).isdigit():
                    console.print(f"  [yellow]⚠ 跳过 '{uid},{name}'：uid 应为纯数字（每行格式是 'uid,用户名'）[/yellow]")
                    continue

                user_dir = root / f"{_sanitize(uid)}_{_sanitize(name)}"
                user_dir.mkdir(parents=True, exist_ok=True)

                cursor = _latest_archived_mblogid(user_dir)
                if cursor:
                    console.print(f"[dim]@{name} ({uid})：增量，游标 {cursor}[/dim]")
                    cutoff = None
                else:
                    console.print(f"[dim]@{name} ({uid})：首次，最近 {days} 天[/dim]")
                    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

                try:
                    statuses = collect_since(client, uid, since_id=cursor, cutoff=cutoff,
                                             max_pages=max_pages, fetch_full=fetch_full)
                except WeiboApiError as exc:
                    console.print(f"  [red]✗ 抓取失败：{exc}[/red]")
                    continue

                written = 0
                for s in statuses:
                    fname = _archive_filename(s)
                    if not fname:
                        continue
                    target = user_dir / fname
                    if target.exists():
                        continue  # already archived
                    target.write_text(to_markdown(s), encoding="utf-8")
                    written += 1

                total_new += written
                console.print(f"  [green]+{written}[/green] 条新微博 → {user_dir}/")

    except WeiboApiError as exc:
        console.print(f"[red]❌ {exc}[/red]")
        return

    console.print(f"[green]完成：共 {total_new} 条新微博，{len(users)} 个用户[/green]")
