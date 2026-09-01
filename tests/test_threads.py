"""Tests for the `threads` command — fetch specific weibos by URL into Markdown."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from weibo_cli.cli import cli
from weibo_cli.commands.personal import parse_thread_list


# ── parse_thread_list ────────────────────────────────────────────────


class TestParseThreadList:
    def test_full_url(self):
        assert parse_thread_list("https://weibo.com/1233486457/RfrszzFA4") == [
            ("1233486457", "RfrszzFA4")
        ]

    def test_multiple_lines(self):
        text = "https://weibo.com/111/AAA\nhttps://weibo.com/222/BBB"
        assert parse_thread_list(text) == [("111", "AAA"), ("222", "BBB")]

    def test_bare_uid_slug(self):
        assert parse_thread_list("333/CCC") == [("333", "CCC")]

    def test_http_and_query_and_fragment(self):
        text = "http://weibo.com/444/DDD?mark_id=x#comment"
        assert parse_thread_list(text) == [("444", "DDD")]

    def test_skips_comments_and_blanks(self):
        text = "# 收藏\n\nhttps://weibo.com/111/AAA\n   \n# note\nhttps://weibo.com/222/BBB"
        assert parse_thread_list(text) == [("111", "AAA"), ("222", "BBB")]

    def test_dedups_first_wins(self):
        text = "https://weibo.com/111/AAA\nhttps://weibo.com/111/AAA"
        assert parse_thread_list(text) == [("111", "AAA")]

    def test_ignores_non_weibo_lines(self):
        assert parse_thread_list("just some text\nhttps://example.com/x/y") == []

    def test_empty(self):
        assert parse_thread_list("") == []


# ── threads command end-to-end (mocked) ──────────────────────────────


@pytest.fixture
def patched_auth(monkeypatch):
    from weibo_cli.auth import Credential
    monkeypatch.setattr("weibo_cli.commands._common.get_credential",
                        lambda: Credential(cookies={"SUB": "x"}))


def _detail(mblogid, *, uid="123", name="测试", text="正文", created="Fri Aug 28 08:28:00 +0800 2026"):
    return {
        "mblogid": mblogid, "mid": f"mid_{mblogid}", "created_at": created,
        "text_raw": text, "user": {"idstr": uid, "screen_name": name},
        "isLongText": False,
    }


class TestThreadsCommand:
    def test_writes_per_author_dirs(self, tmp_path, monkeypatch, patched_auth):
        details = {
            "AAA": _detail("AAA", uid="111", name="阑夕", text="甲"),
            "BBB": _detail("BBB", uid="222", name="高飞", text="乙"),
        }
        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_weibo_detail",
                            lambda self, mid: details[mid])

        f = tmp_path / "threads.txt"
        f.write_text("https://weibo.com/111/AAA\nhttps://weibo.com/222/BBB", encoding="utf-8")
        out = tmp_path / "threads"

        result = CliRunner().invoke(cli, ["threads", str(f), "--out", str(out), "--no-full"])
        assert result.exit_code == 0, result.output

        assert list((out / "111_阑夕").glob("*_AAA.md"))
        assert list((out / "222_高飞").glob("*_BBB.md"))

    def test_file_content_is_markdown(self, tmp_path, monkeypatch, patched_auth):
        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_weibo_detail",
                            lambda self, mid: _detail(mid, uid="111", name="阑夕", text="正文内容"))
        f = tmp_path / "threads.txt"
        f.write_text("https://weibo.com/111/AAA", encoding="utf-8")
        out = tmp_path / "threads"

        CliRunner().invoke(cli, ["threads", str(f), "--out", str(out), "--no-full"])
        md = next((out / "111_阑夕").glob("*_AAA.md")).read_text(encoding="utf-8")
        assert md.startswith("---\n")
        assert "mblogid: AAA" in md
        assert "正文内容" in md

    def test_filename_uses_created_time(self, tmp_path, monkeypatch, patched_auth):
        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_weibo_detail",
                            lambda self, mid: _detail(mid, uid="111", name="x",
                                                      created="Fri Jul 24 14:25:00 +0800 2026"))
        f = tmp_path / "threads.txt"
        f.write_text("https://weibo.com/111/AAA", encoding="utf-8")
        out = tmp_path / "threads"

        CliRunner().invoke(cli, ["threads", str(f), "--out", str(out), "--no-full"])
        names = [p.name for p in (out / "111_x").glob("*.md")]
        assert names == ["20260724_142500_AAA.md"]

    def test_skips_existing(self, tmp_path, monkeypatch, patched_auth):
        calls = {"n": 0}

        def fake_detail(self, mid):
            calls["n"] += 1
            return _detail(mid, uid="111", name="x")

        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_weibo_detail", fake_detail)
        f = tmp_path / "threads.txt"
        f.write_text("https://weibo.com/111/AAA", encoding="utf-8")
        out = tmp_path / "threads"

        r1 = CliRunner().invoke(cli, ["threads", str(f), "--out", str(out), "--no-full"])
        assert r1.exit_code == 0
        files1 = list((out / "111_x").glob("*.md"))

        r2 = CliRunner().invoke(cli, ["threads", str(f), "--out", str(out), "--no-full"])
        assert r2.exit_code == 0
        files2 = list((out / "111_x").glob("*.md"))
        assert len(files1) == len(files2) == 1
        assert "跳过 1" in r2.output  # second run reports the skip

    def test_one_failure_does_not_abort(self, tmp_path, monkeypatch, patched_auth):
        from weibo_cli.exceptions import WeiboApiError

        def fake_detail(self, mid):
            if mid == "BAD":
                raise WeiboApiError("HTTP 400")
            return _detail(mid, uid="111", name="x")

        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_weibo_detail", fake_detail)
        f = tmp_path / "threads.txt"
        f.write_text("https://weibo.com/999/BAD\nhttps://weibo.com/111/OK", encoding="utf-8")
        out = tmp_path / "threads"

        result = CliRunner().invoke(cli, ["threads", str(f), "--out", str(out), "--no-full"])
        assert result.exit_code == 0
        assert "抓取失败" in result.output
        assert list((out / "111_x").glob("*_OK.md"))  # good one still written

    def test_full_enriches_long_weibo_and_source(self, tmp_path, monkeypatch, patched_auth):
        # --full (default) must enrich a long outer weibo AND its long source.
        outer = _detail("OUTER", uid="111", name="x", text="短预览")
        outer["isLongText"] = True
        outer["retweeted_status"] = {
            "mblogid": "SRC", "mid": "mid_SRC", "isLongText": True,
            "user": {"screen_name": "源", "idstr": "222"},
            "text_raw": "源截断", "created_at": outer["created_at"],
        }
        detail_calls = []

        def fake_detail(self, mid):
            detail_calls.append(mid)
            if mid == "OUTER":
                return outer
            if mid == "SRC":
                return {"isLongText": True, "text_raw": "源微博完整全文很长很长"}
            return {}

        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_weibo_detail", fake_detail)
        f = tmp_path / "threads.txt"
        f.write_text("https://weibo.com/111/OUTER", encoding="utf-8")
        out = tmp_path / "threads"

        result = CliRunner().invoke(cli, ["threads", str(f), "--out", str(out)])
        assert result.exit_code == 0, result.output
        # Both the outer and the source detail were fetched.
        assert "OUTER" in detail_calls and "SRC" in detail_calls
        md = next((out / "111_x").glob("*_OUTER.md")).read_text(encoding="utf-8")
        assert "源微博完整全文很长很长" in md

    def test_default_threads_file(self, tmp_path, monkeypatch, patched_auth):
        default_file = tmp_path / "threads.txt"
        default_file.write_text("https://weibo.com/111/AAA", encoding="utf-8")
        monkeypatch.setattr("weibo_cli.commands.personal.DEFAULT_THREADS_FILE", default_file)
        monkeypatch.setattr("weibo_cli.client.WeiboClient.get_weibo_detail",
                            lambda self, mid: _detail(mid, uid="111", name="x"))
        out = tmp_path / "threads"

        result = CliRunner().invoke(cli, ["threads", "--out", str(out), "--no-full"])
        assert result.exit_code == 0
        assert list((out / "111_x").glob("*_AAA.md"))

    def test_default_missing_gives_hint(self, tmp_path, monkeypatch, patched_auth):
        monkeypatch.setattr("weibo_cli.commands.personal.DEFAULT_THREADS_FILE", tmp_path / "nope.txt")
        result = CliRunner().invoke(cli, ["threads"])
        assert result.exit_code == 0
        assert "默认文件不存在" in result.output

    def test_explicit_missing_file_errors(self, tmp_path, patched_auth):
        result = CliRunner().invoke(cli, ["threads", str(tmp_path / "gone.txt")])
        assert result.exit_code == 0
        assert "找不到列表文件" in result.output

    def test_empty_list_reported(self, tmp_path, patched_auth):
        f = tmp_path / "threads.txt"
        f.write_text("# only comments\n\n", encoding="utf-8")
        result = CliRunner().invoke(cli, ["threads", str(f), "--out", str(tmp_path / "o")])
        assert result.exit_code == 0
        assert "列表为空" in result.output
