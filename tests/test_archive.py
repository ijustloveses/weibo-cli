"""Tests for the `archive` command — batch incremental Markdown export."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from weibo_cli.cli import cli
from weibo_cli.commands.personal import (
    _archive_filename,
    _latest_archived_mblogid,
    _sanitize,
    parse_user_list,
)


# ── parse_user_list ──────────────────────────────────────────────────


class TestParseUserList:
    def test_uid_and_name(self):
        assert parse_user_list("1560906700,阑夕") == [("1560906700", "阑夕")]

    def test_multiple_lines(self):
        text = "1560906700,阑夕\n1699432410,新华社"
        assert parse_user_list(text) == [("1560906700", "阑夕"), ("1699432410", "新华社")]

    def test_uid_only_defaults_name_to_uid(self):
        assert parse_user_list("12345") == [("12345", "12345")]

    def test_skips_comments_and_blanks(self):
        text = "# 大V列表\n\n1560906700,阑夕\n   \n# 注释\n1699432410,新华社"
        assert parse_user_list(text) == [("1560906700", "阑夕"), ("1699432410", "新华社")]

    def test_trims_whitespace(self):
        assert parse_user_list("  123 , 名字  ") == [("123", "名字")]

    def test_empty(self):
        assert parse_user_list("") == []


# ── _sanitize ────────────────────────────────────────────────────────


class TestSanitize:
    def test_replaces_path_separators(self):
        assert "/" not in _sanitize("a/b")
        assert "\\" not in _sanitize("a\\b")

    def test_replaces_illegal_chars(self):
        assert _sanitize('a<b>c:d"e') == "a_b_c_d_e"

    def test_empty_becomes_unknown(self):
        assert _sanitize("") == "unknown"


# ── _latest_archived_mblogid ─────────────────────────────────────────


class TestLatestArchived:
    def test_none_when_dir_missing(self, tmp_path):
        assert _latest_archived_mblogid(tmp_path / "nope") is None

    def test_none_when_no_archive_files(self, tmp_path):
        (tmp_path / "readme.txt").write_text("x")
        assert _latest_archived_mblogid(tmp_path) is None

    def test_picks_latest_by_timestamp(self, tmp_path):
        (tmp_path / "20260723_180000_OLD.md").write_text("x")
        (tmp_path / "20260724_142500_NEW.md").write_text("x")
        (tmp_path / "20260724_090000_MID.md").write_text("x")
        assert _latest_archived_mblogid(tmp_path) == "NEW"

    def test_ignores_non_archive_files(self, tmp_path):
        (tmp_path / "20260724_142500_REAL.md").write_text("x")
        (tmp_path / "notes.md").write_text("x")
        assert _latest_archived_mblogid(tmp_path) == "REAL"


# ── _archive_filename ────────────────────────────────────────────────


class TestArchiveFilename:
    def test_builds_name(self):
        s = {"mblogid": "Ra9Q6lN9q", "created_at": "Fri Jul 24 14:25:00 +0800 2026"}
        assert _archive_filename(s) == "20260724_142500_Ra9Q6lN9q.md"

    def test_none_without_time(self):
        assert _archive_filename({"mblogid": "x"}) is None

    def test_none_without_id(self):
        assert _archive_filename({"created_at": "Fri Jul 24 14:25:00 +0800 2026"}) is None


# ── archive command end-to-end (mocked) ──────────────────────────────


@pytest.fixture
def patched_auth(monkeypatch):
    from weibo_cli.auth import Credential
    monkeypatch.setattr("weibo_cli.commands._common.get_credential", lambda: Credential(cookies={"SUB": "x"}))


def _status(mblogid, *, hours_ago=0, text="正文"):
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "mblogid": mblogid, "mid": f"mid_{mblogid}",
        "created_at": ts.strftime("%a %b %d %H:%M:%S +0000 %Y"),
        "text_raw": text, "user": {"idstr": "123", "screen_name": "测试"},
        "isLongText": False,
    }


class TestArchiveCommand:
    def test_first_run_creates_files(self, tmp_path, monkeypatch, patched_auth):
        rows = [_status("A", hours_ago=1), _status("B", hours_ago=2), _status("OLD", hours_ago=48)]
        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_user_weibos",
                            lambda self, uid, page=1, count=20, feature=0: {"list": rows if page == 1 else []})

        users = tmp_path / "users.txt"
        users.write_text("123,测试", encoding="utf-8")
        out = tmp_path / "weibos"

        result = CliRunner().invoke(cli, ["archive", str(users), "--out", str(out)])
        assert result.exit_code == 0, result.output

        user_dir = out / "123_测试"
        files = sorted(f.name for f in user_dir.glob("*.md"))
        # A and B are within the last day; OLD (48h) is not.
        assert len(files) == 2
        assert any(f.endswith("_A.md") for f in files)
        assert any(f.endswith("_B.md") for f in files)
        assert not any(f.endswith("_OLD.md") for f in files)

    def test_incremental_uses_cursor(self, tmp_path, monkeypatch, patched_auth):
        # Pre-seed an existing archive file → cursor should be its mblogid.
        user_dir = tmp_path / "weibos" / "123_测试"
        user_dir.mkdir(parents=True)
        (user_dir / "20260723_120000_CURSOR.md").write_text("old", encoding="utf-8")

        captured = {}

        def fake_collect(client, uid, *, since_id=None, cutoff=None, max_pages=20, fetch_full=False):
            captured["since_id"] = since_id
            captured["cutoff"] = cutoff
            return [_status("NEW", hours_ago=1)]

        monkeypatch.setattr("weibo_cli.commands.personal.collect_since", fake_collect)

        users = tmp_path / "users.txt"
        users.write_text("123,测试", encoding="utf-8")

        result = CliRunner().invoke(cli, ["archive", str(users), "--out", str(tmp_path / "weibos")])
        assert result.exit_code == 0, result.output
        # Cursor mode: since_id set to the existing file's mblogid, no time cutoff.
        assert captured["since_id"] == "CURSOR"
        assert captured["cutoff"] is None
        assert (user_dir / "20260723_120000_CURSOR.md").exists()  # untouched
        assert list(user_dir.glob("*_NEW.md"))  # new one written

    def test_file_content_is_markdown(self, tmp_path, monkeypatch, patched_auth):
        rows = [_status("A", hours_ago=1)]
        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_user_weibos",
                            lambda self, uid, page=1, count=20, feature=0: {"list": rows if page == 1 else []})

        users = tmp_path / "users.txt"
        users.write_text("123,测试", encoding="utf-8")
        out = tmp_path / "weibos"

        CliRunner().invoke(cli, ["archive", str(users), "--out", str(out)])
        md_file = next((out / "123_测试").glob("*_A.md"))
        content = md_file.read_text(encoding="utf-8")
        assert content.startswith("---\n")
        assert "mblogid: A" in content
        assert "正文" in content

    def test_default_users_file(self, tmp_path, monkeypatch, patched_auth):
        # No file argument → reads DEFAULT_USERS_FILE.
        default_file = tmp_path / "users.txt"
        default_file.write_text("123,测试", encoding="utf-8")
        monkeypatch.setattr("weibo_cli.commands.personal.DEFAULT_USERS_FILE", default_file)

        rows = [_status("A", hours_ago=1)]
        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_user_weibos",
                            lambda self, uid, page=1, count=20, feature=0: {"list": rows if page == 1 else []})
        out = tmp_path / "weibos"

        result = CliRunner().invoke(cli, ["archive", "--out", str(out)])
        assert result.exit_code == 0, result.output
        assert list((out / "123_测试").glob("*_A.md"))

    def test_default_missing_gives_hint(self, tmp_path, monkeypatch, patched_auth):
        monkeypatch.setattr("weibo_cli.commands.personal.DEFAULT_USERS_FILE", tmp_path / "nope.txt")
        result = CliRunner().invoke(cli, ["archive"])
        assert result.exit_code == 0
        assert "默认文件不存在" in result.output

    def test_explicit_missing_file_errors(self, tmp_path, monkeypatch, patched_auth):
        result = CliRunner().invoke(cli, ["archive", str(tmp_path / "gone.txt")])
        assert result.exit_code == 0
        assert "找不到用户列表文件" in result.output

    def test_skips_non_numeric_uid(self, tmp_path, monkeypatch, patched_auth):
        # Reversed 'name,uid' format → first field isn't numeric → skip with hint.
        called = {"n": 0}

        def fake_weibos(self, uid, page=1, count=20, feature=0):
            called["n"] += 1
            return {"list": []}

        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_user_weibos", fake_weibos)
        users = tmp_path / "users.txt"
        users.write_text("李楠或kkk", encoding="utf-8")  # no uid

        result = CliRunner().invoke(cli, ["archive", str(users), "--out", str(tmp_path / "weibos")])
        assert result.exit_code == 0
        assert "uid 应为纯数字" in result.output
        assert called["n"] == 0  # never hit the API

    def test_one_bad_user_does_not_abort_others(self, tmp_path, monkeypatch, patched_auth):
        from weibo_cli.exceptions import WeiboApiError as _WErr

        def fake_collect(client, uid, **kw):
            if uid == "111":
                raise _WErr("HTTP 400 for ...")
            return [_status("OK", hours_ago=1)]

        monkeypatch.setattr("weibo_cli.commands.personal.collect_since", fake_collect)
        users = tmp_path / "users.txt"
        users.write_text("111,坏用户\n222,好用户", encoding="utf-8")
        out = tmp_path / "weibos"

        result = CliRunner().invoke(cli, ["archive", str(users), "--out", str(out)])
        assert result.exit_code == 0
        assert "抓取失败" in result.output          # bad user reported
        assert list((out / "222_好用户").glob("*_OK.md"))  # good user still archived

    def test_rerun_writes_nothing_new(self, tmp_path, monkeypatch, patched_auth):
        rows = [_status("A", hours_ago=1)]
        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_user_weibos",
                            lambda self, uid, page=1, count=20, feature=0: {"list": rows if page == 1 else []})
        users = tmp_path / "users.txt"
        users.write_text("123,测试", encoding="utf-8")
        out = tmp_path / "weibos"

        r1 = CliRunner().invoke(cli, ["archive", str(users), "--out", str(out)])
        assert r1.exit_code == 0
        files_after_1 = list((out / "123_测试").glob("*.md"))

        # Second run: cursor is now A, collect_since returns nothing newer.
        r2 = CliRunner().invoke(cli, ["archive", str(users), "--out", str(out)])
        assert r2.exit_code == 0
        files_after_2 = list((out / "123_测试").glob("*.md"))
        assert len(files_after_1) == len(files_after_2) == 1
