"""Tests for _common.py utilities and new API methods."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from weibo_cli.commands._common import format_count, full_text, strip_html, strip_topic_tags
from weibo_cli.exceptions import SessionExpiredError, WeiboApiError


# ── strip_html tests ─────────────────────────────────────────────────


class TestStripHtml:
    def test_basic_tags(self):
        assert strip_html("<b>hello</b>") == "hello"

    def test_nested_tags(self):
        assert strip_html("<a href='#'><span>link</span></a>") == "link"

    def test_empty_string(self):
        assert strip_html("") == ""

    def test_none_input(self):
        assert strip_html(None) == ""

    def test_no_tags(self):
        assert strip_html("plain text") == "plain text"

    def test_self_closing_tags(self):
        assert strip_html("hello<br/>world") == "helloworld"

    def test_mixed_content(self):
        assert strip_html("Hello <b>world</b>! <i>Good</i>") == "Hello world! Good"


# ── format_count tests ──────────────────────────────────────────────


class TestFormatCount:
    def test_small_number(self):
        assert format_count(1000) == "1000"

    def test_exact_10000(self):
        assert format_count(10000) == "1.0万"

    def test_large_number(self):
        assert format_count(113614253) == "11361.4万"

    def test_string_input(self):
        assert format_count("5000") == "5000"

    def test_string_large(self):
        assert format_count("50000") == "5.0万"

    def test_invalid_string(self):
        assert format_count("abc") == "abc"

    def test_none_input(self):
        assert format_count(None) == "None"

    def test_zero(self):
        assert format_count(0) == "0"


# ── strip_topic_tags tests ───────────────────────────────────────────


class TestStripTopicTags:
    def test_removes_trailing_tags(self):
        text = "GitHub: github.com/x/y\n\n#HOW I AI#  #程序员#"
        assert strip_topic_tags(text) == "GitHub: github.com/x/y"

    def test_removes_leading_tag(self):
        assert strip_topic_tags("#热点# 今天天气不错") == "今天天气不错"

    def test_removes_mid_text_tag(self):
        assert strip_topic_tags("看看 #科技# 相关的内容") == "看看 相关的内容"

    def test_removes_multiple_tags(self):
        assert strip_topic_tags("#a# 正文 #b# 更多 #c#") == "正文 更多"

    # ── Markdown safety ──

    def test_keeps_markdown_h1(self):
        assert strip_topic_tags("# 标题\n正文") == "# 标题\n正文"

    def test_keeps_markdown_h2(self):
        assert strip_topic_tags("## 二级标题") == "## 二级标题"

    def test_keeps_hash_in_inline_code(self):
        # #include is paired-ish only if wrapped; here protect inline code entirely.
        assert strip_topic_tags("用 `#include <stdio.h>` 引入") == "用 `#include <stdio.h>` 引入"

    def test_keeps_color_hex_in_inline_code(self):
        assert strip_topic_tags("设 `color: #fff` 即可") == "设 `color: #fff` 即可"

    def test_keeps_paired_hash_inside_code(self):
        # A paired #x# inside inline code must NOT be stripped.
        assert strip_topic_tags("代码 `a #x# b` 结束") == "代码 `a #x# b` 结束"

    def test_keeps_fenced_code_block(self):
        text = "说明\n```\n# comment\nx = '#not a tag#'\n```\n#真话题#"
        result = strip_topic_tags(text)
        assert "# comment" in result
        assert "'#not a tag#'" in result
        assert "#真话题#" not in result

    def test_markdown_heading_after_tag_removed(self):
        text = "#话题#\n# 真Markdown标题\n正文"
        result = strip_topic_tags(text)
        assert "#话题#" not in result
        assert "# 真Markdown标题" in result

    # ── edge cases ──

    def test_empty(self):
        assert strip_topic_tags("") == ""

    def test_no_tags(self):
        assert strip_topic_tags("普通文本没有标签") == "普通文本没有标签"

    def test_single_hash_not_removed(self):
        # A lone hash (no closing pair) is not a topic tag.
        assert strip_topic_tags("价格是 100# 一件") == "价格是 100# 一件"

    def test_collapses_double_space_from_removed_tags(self):
        # "#a#  #b#" between words shouldn't leave a big gap.
        assert strip_topic_tags("start #a#  #b# end") == "start end"


# ── full_text tests ──────────────────────────────────────────────────


class TestFullText:
    def test_prefers_long_text_content(self):
        s = {"text_raw": "short preview", "longText": {"longTextContent": "the full long body here"}}
        assert full_text(s) == "the full long body here"

    def test_falls_back_to_text_raw(self):
        s = {"text_raw": "just a normal weibo"}
        assert full_text(s) == "just a normal weibo"

    def test_falls_back_to_text(self):
        s = {"text": "<a>hello</a> world"}
        assert full_text(s) == "hello world"

    def test_strips_html_from_long_content(self):
        s = {"text_raw": "x", "longText": {"longTextContent": "<b>bold</b> and <i>italic</i>"}}
        assert full_text(s) == "bold and italic"

    def test_uses_content_key_when_longtextcontent_absent(self):
        s = {"text_raw": "x", "longText": {"content": "alternate long field that is clearly longer"}}
        assert full_text(s) == "alternate long field that is clearly longer"

    def test_shorter_long_text_ignored(self):
        # If longTextContent is somehow shorter, keep the longer text_raw.
        s = {"text_raw": "a much longer preview than the long field", "longText": {"longTextContent": "tiny"}}
        assert full_text(s) == "a much longer preview than the long field"

    def test_empty_status(self):
        assert full_text({}) == ""

    def test_longtext_not_dict(self):
        # Some list responses have longText as a bool/None — must not crash.
        s = {"text_raw": "fine", "longText": True}
        assert full_text(s) == "fine"


# ── _handle_response unwrap tests ────────────────────────────────────


class TestHandleResponseUnwrap:
    def test_unwrap_true_extracts_data(self, mock_client):
        raw = {"ok": 1, "data": {"items": [1, 2, 3]}}
        result = mock_client._handle_response(raw, "test", unwrap=True)
        assert result == {"items": [1, 2, 3]}

    def test_unwrap_false_returns_full(self, mock_client):
        raw = {"ok": 1, "data": {"items": [1, 2, 3]}}
        result = mock_client._handle_response(raw, "test", unwrap=False)
        assert result == raw

    def test_unwrap_false_with_raw_api(self, mock_client):
        """APIs like statuses/show return data at top level, not wrapped."""
        raw = {"ok": 1, "mblogid": "abc", "text": "hello"}
        result = mock_client._handle_response(raw, "test", unwrap=False)
        assert result == raw
        assert result["mblogid"] == "abc"

    def test_session_expired_precise_match(self, mock_client):
        """Only precise keywords should trigger SessionExpiredError."""
        raw = {"ok": 0, "message": "请先登录"}
        with pytest.raises(SessionExpiredError):
            mock_client._handle_response(raw, "test")

    def test_login_related_not_falsely_matched(self, mock_client):
        """Messages containing '登录' but not matching keywords should raise WeiboApiError."""
        raw = {"ok": 0, "message": "登录设备异常"}
        with pytest.raises(WeiboApiError, match="登录设备异常"):
            mock_client._handle_response(raw, "test")


# ── New API method tests ─────────────────────────────────────────────


def _mock_response(data):
    """Create a mock httpx response for the given data."""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = json.dumps(data)
    resp.json.return_value = data
    resp.cookies = httpx.Cookies()
    return resp


class TestGetFollowersAPI:
    def test_get_followers_passes_params(self, mock_client):
        response = {"ok": 1, "users": [{"screen_name": "test"}]}
        mock_client._http.request.return_value = _mock_response(response)

        result = mock_client.get_followers("12345", page=2)
        params = mock_client._http.request.call_args[1].get("params", {})
        assert params["uid"] == "12345"
        assert params["page"] == "2"
        assert params["relate"] == "fans"
        assert "users" in result


class TestGetFriendsTimelineAPI:
    def test_get_friends_timeline_default(self, mock_client):
        response = {"ok": 1, "statuses": [], "max_id": "0"}
        mock_client._http.request.return_value = _mock_response(response)

        result = mock_client.get_friends_timeline()
        params = mock_client._http.request.call_args[1].get("params", {})
        assert params["count"] == "20"
        assert params["max_id"] == "0"
        assert "statuses" in result


class TestGetFollowingAPI:
    def test_get_following_passes_uid(self, mock_client):
        response = {"ok": 1, "users": []}
        mock_client._http.request.return_value = _mock_response(response)

        mock_client.get_following("12345")
        params = mock_client._http.request.call_args[1].get("params", {})
        assert params["uid"] == "12345"
        assert params["page"] == "1"


class TestGetConfigAPI:
    def test_get_config_returns_data(self, mock_client):
        response = {"ok": 1, "data": {"uid": "12345", "login": True}}
        mock_client._http.request.return_value = _mock_response(response)

        result = mock_client.get_config()
        assert result["uid"] == "12345"


class TestSearchWeiboAPI:
    def test_search_weibo_uses_mobile_client(self, mock_client):
        """Verify search_weibo creates a separate mobile client."""
        search_response = {"ok": 1, "data": {"cards": []}}
        mock_mobile = MagicMock()
        mock_mobile.__enter__ = MagicMock(return_value=mock_mobile)
        mock_mobile.__exit__ = MagicMock(return_value=False)
        mock_mobile.request.return_value = _mock_response(search_response)

        with patch.object(mock_client, '_build_mobile_client', return_value=mock_mobile):
            result = mock_client.search_weibo("test")

        assert result == search_response
        mock_mobile.request.assert_called_once()
        call_args = mock_mobile.request.call_args
        assert call_args[1]["params"]["containerid"] == "100103type=1&q=test"
