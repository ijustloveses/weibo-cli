"""Tests for the `since` command — incremental fetch by cursor or time window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from weibo_cli.cli import cli
from weibo_cli.commands._common import parse_weibo_time


# ── parse_weibo_time ─────────────────────────────────────────────────


class TestParseWeiboTime:
    def test_standard_format(self):
        dt = parse_weibo_time("Sat Mar 14 07:20:55 +0800 2026")
        assert dt is not None
        assert dt.year == 2026 and dt.month == 3 and dt.day == 14
        assert dt.tzinfo is not None

    def test_empty_returns_none(self):
        assert parse_weibo_time("") is None
        assert parse_weibo_time(None) is None

    def test_garbage_returns_none(self):
        assert parse_weibo_time("not a date") is None

    def test_comparable_across_timezones(self):
        earlier = parse_weibo_time("Sat Mar 14 07:00:00 +0800 2026")
        later = parse_weibo_time("Sat Mar 14 09:00:00 +0800 2026")
        assert earlier < later


# ── since command: pagination + filtering ────────────────────────────


def _status(mblogid, *, hours_ago=0):
    """Build a fake status dated `hours_ago` before now (UTC)."""
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    # Weibo's created_at format, always +0000 to keep it deterministic.
    created = ts.strftime("%a %b %d %H:%M:%S +0000 %Y")
    return {"mblogid": mblogid, "mid": f"mid_{mblogid}", "created_at": created, "text_raw": f"post {mblogid}"}


@pytest.fixture
def patched_auth(monkeypatch):
    """Bypass real auth for the CLI invocation."""
    from weibo_cli.auth import Credential
    monkeypatch.setattr("weibo_cli.commands._common.get_credential", lambda: Credential(cookies={"SUB": "x"}))


def _run_since(monkeypatch, pages, extra_args):
    """Invoke `weibo since` with get_user_weibos returning `pages` in order."""
    calls = {"n": 0}

    def fake_get_user_weibos(self, uid, page=1, count=20, feature=0):
        idx = page - 1
        statuses = pages[idx] if idx < len(pages) else []
        calls["n"] = max(calls["n"], page)
        return {"list": statuses}

    monkeypatch.setattr("weibo_cli.client.WeiboClient.get_user_weibos", fake_get_user_weibos)

    runner = CliRunner()
    result = runner.invoke(cli, ["since", "12345", "--json", *extra_args])
    return result, calls


class TestSinceCursor:
    def test_stops_at_cursor(self, monkeypatch, patched_auth):
        # Newest-first: b3, b2, b1(cursor). Should return b3, b2 only.
        page1 = [_status("b3"), _status("b2"), _status("b1")]
        result, _ = _run_since(monkeypatch, [page1], ["--since", "b1"])
        assert result.exit_code == 0
        assert '"b3"' in result.output
        assert '"b2"' in result.output
        assert '"count": 2' in result.output

    def test_cursor_across_pages(self, monkeypatch, patched_auth):
        # 20 items on page 1 (full page → keep paging), cursor on page 2.
        page1 = [_status(f"p1_{i}") for i in range(20)]
        page2 = [_status("p2_0"), _status("cursor"), _status("p2_2")]
        result, calls = _run_since(monkeypatch, [page1, page2], ["--since", "cursor"])
        assert result.exit_code == 0
        assert '"count": 21' in result.output  # 20 + p2_0
        assert calls["n"] == 2  # paged into page 2


class TestSinceTimeWindow:
    def test_last_day_default(self, monkeypatch, patched_auth):
        # Two recent, one 30h old → only the two recent within 1 day.
        page1 = [_status("new1", hours_ago=1), _status("new2", hours_ago=5), _status("old", hours_ago=30)]
        result, _ = _run_since(monkeypatch, [page1], [])
        assert result.exit_code == 0
        assert '"count": 2' in result.output
        assert '"new1"' in result.output
        assert '"old"' not in result.output

    def test_days_flag(self, monkeypatch, patched_auth):
        page1 = [_status("d1", hours_ago=10), _status("d2", hours_ago=40), _status("d3", hours_ago=80)]
        result, _ = _run_since(monkeypatch, [page1], ["--days", "3"])
        assert result.exit_code == 0
        # 10h and 40h within 3 days (72h); 80h is not.
        assert '"count": 2' in result.output

    def test_stops_paging_when_older_than_cutoff(self, monkeypatch, patched_auth):
        # Full first page all old → should stop, not fetch page 2.
        page1 = [_status(f"old_{i}", hours_ago=48) for i in range(20)]
        page2 = [_status("should_not_reach")]
        result, calls = _run_since(monkeypatch, [page1, page2], [])
        assert result.exit_code == 0
        assert '"count": 0' in result.output
        assert calls["n"] == 1  # never paged to page 2


# ── since --full: long-text enrichment ───────────────────────────────


class TestSinceFull:
    def test_full_enriches_long_weibos_only(self, monkeypatch, patched_auth):
        # Two statuses: one long, one short.
        long_s = _status("longone", hours_ago=1)
        long_s["isLongText"] = True
        long_s["text_raw"] = "short preview"
        short_s = _status("shortone", hours_ago=2)  # no isLongText

        def fake_weibos(self, uid, page=1, count=20, feature=0):
            return {"list": [long_s, short_s] if page == 1 else []}

        detail_calls = []

        def fake_detail(self, mblogid):
            detail_calls.append(mblogid)
            return {"longText": {"longTextContent": f"FULL BODY of {mblogid}"}}

        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_user_weibos", fake_weibos)
        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_weibo_detail", fake_detail)

        result = CliRunner().invoke(cli, ["since", "12345", "--json", "--full"])
        assert result.exit_code == 0
        # detail called only for the long weibo, never the short one.
        assert detail_calls == ["longone"]
        assert "FULL BODY of longone" in result.output

    def test_no_full_flag_skips_detail(self, monkeypatch, patched_auth):
        long_s = _status("longone", hours_ago=1)
        long_s["isLongText"] = True

        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_user_weibos",
                            lambda self, uid, page=1, count=20, feature=0: {"list": [long_s] if page == 1 else []})

        called = {"detail": False}

        def fake_detail(self, mblogid):
            called["detail"] = True
            return {}

        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_weibo_detail", fake_detail)

        result = CliRunner().invoke(cli, ["since", "12345", "--json"])
        assert result.exit_code == 0
        assert called["detail"] is False  # no --full → no detail calls

    def test_full_survives_detail_failure(self, monkeypatch, patched_auth):
        from weibo_cli.exceptions import WeiboApiError
        long_s = _status("longone", hours_ago=1)
        long_s["isLongText"] = True
        long_s["text_raw"] = "keep this preview"

        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_user_weibos",
                            lambda self, uid, page=1, count=20, feature=0: {"list": [long_s] if page == 1 else []})

        def boom(self, mblogid):
            raise WeiboApiError("detail failed")

        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_weibo_detail", boom)

        result = CliRunner().invoke(cli, ["since", "12345", "--json", "--full"])
        assert result.exit_code == 0  # does not crash
        assert '"count": 1' in result.output
        assert "keep this preview" in result.output
