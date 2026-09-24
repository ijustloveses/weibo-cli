"""Tests for _common.py utilities and new API methods."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from weibo_cli.commands._common import (
    all_image_urls,
    extract_media,
    format_count,
    full_text,
    full_text_rich,
    strip_html,
    strip_topic_tags,
    to_markdown,
)
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

    # ── zero-width + t.cn cleanup ──

    def test_removes_zero_width_chars(self):
        assert strip_topic_tags("正文​​​") == "正文"

    def test_removes_zero_width_in_middle(self):
        assert strip_topic_tags("a​b") == "ab"

    def test_removes_unresolvable_tcn_link(self):
        # No link_map → the short link is meaningless noise → dropped.
        assert strip_topic_tags("看视频 http://t.cn/AX9CV0W0 结束") == "看视频 结束"

    def test_removes_https_tcn_link(self):
        assert strip_topic_tags("x https://t.cn/Abc123 y") == "x y"

    def test_resolves_tcn_link_inline(self):
        # With a link_map, the short link is replaced by the real URL in place,
        # preserving surrounding context like "网址在此：<url>".
        text = "网址在此：http://t.cn/AX91O2zl 谢谢"
        link_map = {"http://t.cn/AX91O2zl": "https://qwenwork.cn/?x=1"}
        assert strip_topic_tags(text, link_map) == "网址在此：https://qwenwork.cn/?x=1 谢谢"

    def test_resolves_tcn_link_scheme_mismatch(self):
        # Body uses http, url_struct keys on https (or vice versa) → still resolves.
        text = "看 https://t.cn/ABC 完"
        link_map = {"http://t.cn/ABC": "https://real.example/p"}
        assert strip_topic_tags(text, link_map) == "看 https://real.example/p 完"

    def test_resolves_tcn_link_to_markdown_display(self):
        # When the map value is a Markdown link, it is inlined verbatim.
        text = "文章：http://t.cn/AX938lL9"
        link_map = {"http://t.cn/AX938lL9": "[标题](https://weibo.com/ttarticle/p/show?id=1)"}
        assert strip_topic_tags(text, link_map) == "文章：[标题](https://weibo.com/ttarticle/p/show?id=1)"

    def test_tcn_inside_existing_markdown_link_keeps_anchor(self):
        # The author already wrote [微博](t.cn/...). We must swap only the URL for
        # the resolved long URL, NOT wrap it again into [微博]([title](url)).
        text = "写了一篇 [微博](http://t.cn/AX9cPwZb)，很详细"
        link_map = {"http://t.cn/AX9cPwZb": "[微博正文](https://weibo.com/5078115336/R9J11nr9S)"}
        assert strip_topic_tags(text, link_map) == (
            "写了一篇 [微博](https://weibo.com/5078115336/R9J11nr9S)，很详细"
        )

    def test_tcn_in_markdown_link_with_bare_url_map(self):
        # Same, but the map value is a bare URL (untitled link).
        text = "见 [这里](http://t.cn/ABC) 结束"
        link_map = {"http://t.cn/ABC": "https://real.example/p"}
        assert strip_topic_tags(text, link_map) == "见 [这里](https://real.example/p) 结束"

    def test_tcn_in_markdown_link_unresolved_keeps_anchor_text(self):
        # Unresolvable href inside a Markdown link → keep just the anchor text.
        text = "见 [这里](http://t.cn/ABC) 结束"
        assert strip_topic_tags(text) == "见 这里 结束"

    def test_keeps_tcn_in_code(self):
        assert strip_topic_tags("代码 `http://t.cn/xyz` 结束") == "代码 `http://t.cn/xyz` 结束"

    def test_keeps_other_urls(self):
        # Non-t.cn URLs must survive.
        assert strip_topic_tags("看 https://github.com/x/y 项目") == "看 https://github.com/x/y 项目"


# ── extract_media / full_text_rich tests ─────────────────────────────


class TestExtractMedia:
    def test_images_highest_quality_in_order(self):
        s = {
            "pic_ids": ["b", "a"],
            "pic_infos": {
                "a": {"largest": {"url": "A_large"}, "bmiddle": {"url": "A_mid"}},
                "b": {"original": {"url": "B_orig"}},
            },
        }
        assert extract_media(s)["images"] == ["B_orig", "A_large"]

    def test_video_returns_page_url_from_object_id(self):
        # We return the stable video page, NOT the time-limited mp4 stream.
        s = {"page_info": {"object_type": "video", "object_id": "1034:5324190534795325",
                           "media_info": {"stream_url": "http://v/x.mp4"}}}
        assert extract_media(s)["video"] == "https://video.weibo.com/show?fid=1034:5324190534795325"

    def test_video_falls_back_to_url_struct(self):
        s = {"page_info": {"object_type": "video"},
             "url_struct": [{"long_url": "https://video.weibo.com/show?fid=1034:999"}]}
        assert extract_media(s)["video"] == "https://video.weibo.com/show?fid=1034:999"

    def test_video_never_returns_mp4_stream(self):
        s = {"page_info": {"object_type": "video", "object_id": "1034:777",
                           "media_info": {"stream_url": "http://cdn/x.mp4?ssig=abc&Expires=123"}}}
        assert ".mp4" not in extract_media(s)["video"]

    def test_no_video_when_not_video_page(self):
        s = {"page_info": {"object_type": "article"}}
        assert extract_media(s)["video"] is None

    def test_expands_short_links_as_markdown(self):
        # Titled links render as Markdown: [title](url).
        s = {"url_struct": [{"short_url": "http://t.cn/x", "long_url": "http://real/x", "url_title": "标题"}]}
        assert extract_media(s)["links"] == ["[标题](http://real/x)"]

    def test_untitled_link_is_bare_url(self):
        s = {"url_struct": [{"short_url": "http://t.cn/x", "long_url": "http://real/x"}]}
        assert extract_media(s)["links"] == ["http://real/x"]

    def test_retweet_extracted_with_media(self):
        s = {
            "text_raw": "引起舒适",
            "retweeted_status": {
                "mblogid": "ORIG1",
                "user": {"screen_name": "原作者"},
                "text_raw": "原微博正文",
                "page_info": {"object_type": "video", "object_id": "1034:555"},
            },
        }
        media = extract_media(s)
        assert media["retweet"]["author"] == "原作者"
        assert media["retweet"]["mblogid"] == "ORIG1"
        assert media["retweet"]["text"] == "原微博正文"
        assert media["retweet"]["media"]["video"] == "https://video.weibo.com/show?fid=1034:555"

    def test_repost_video_not_duplicated_on_outer(self):
        # Weibo copies the original's page_info onto the outer status. The video
        # belongs to the original only — outer must not repeat it.
        s = {
            "text_raw": "引起舒适",
            "page_info": {"object_type": "video", "object_id": "1034:999"},
            "retweeted_status": {
                "user": {"screen_name": "原作者"}, "text_raw": "原文",
                "page_info": {"object_type": "video", "object_id": "1034:999"},
            },
        }
        media = extract_media(s)
        assert media["video"] is None  # not on outer
        assert media["retweet"]["media"]["video"] == "https://video.weibo.com/show?fid=1034:999"

    def test_repost_outer_keeps_own_new_video(self):
        # If the reposter adds a DIFFERENT video, keep it on the outer layer.
        s = {
            "text_raw": "我也发个视频",
            "page_info": {"object_type": "video", "object_id": "1034:111"},
            "retweeted_status": {
                "user": {"screen_name": "原作者"}, "text_raw": "原文",
                "page_info": {"object_type": "video", "object_id": "1034:222"},
            },
        }
        media = extract_media(s)
        assert media["video"] == "https://video.weibo.com/show?fid=1034:111"
        assert media["retweet"]["media"]["video"] == "https://video.weibo.com/show?fid=1034:222"

    def test_repost_duplicate_images_removed_from_outer(self):
        s = {
            "text_raw": "转",
            "pic_ids": ["a"], "pic_infos": {"a": {"largest": {"url": "SHARED"}}},
            "retweeted_status": {
                "user": {"screen_name": "x"}, "text_raw": "原",
                "pic_ids": ["a"], "pic_infos": {"a": {"largest": {"url": "SHARED"}}},
            },
        }
        media = extract_media(s)
        assert media["images"] == []  # deduped off outer
        assert media["retweet"]["media"]["images"] == ["SHARED"]

    def test_waterfall_inherited_link_moved_to_source(self):
        # Waterfall (mymblog) attaches the SOURCE's url_struct to the OUTER
        # status while the inner retweeted_status.url_struct is empty. The link
        # is not in the reposter's own text → it belongs to the source.
        s = {
            "text_raw": "还是回归线下消费吧//@某人:评论",  # no t.cn link of its own
            "url_struct": [{
                "short_url": "http://t.cn/AX9aIyEc",
                "long_url": "https://weibo.com/ttarticle/p/show?id=2309405323914204545144",
                "url_title": "线上女装最难的一年",
            }],
            "retweeted_status": {
                "user": {"screen_name": "凤凰网财经", "idstr": "1988800805"},
                "mblogid": "Ra3bEiVkh",
                "text_raw": "【多家百万粉女装店相继闭店】...http://t.cn/AX9aIyEc",
                "url_struct": [],  # empty in the waterfall
            },
        }
        media = extract_media(s)
        assert media["links"] == []  # not attributed to the reposter
        rt_links = media["retweet"]["media"]["links"]
        assert any("2309405323914204545144" in link for link in rt_links)

    def test_reposter_own_link_stays_on_outer(self):
        # A link whose short_url IS in the reposter's own text is genuinely
        # theirs and must stay on the outer layer.
        s = {
            "text_raw": "看这个 http://t.cn/OWN123 很好//@x:评论",
            "url_struct": [{
                "short_url": "http://t.cn/OWN123",
                "long_url": "https://example.com/mine",
                "url_title": "我的链接",
            }],
            "retweeted_status": {
                "user": {"screen_name": "x", "idstr": "1"},
                "mblogid": "SRC",
                "text_raw": "源微博正文",
                "url_struct": [],
            },
        }
        media = extract_media(s)
        assert any("example.com/mine" in link for link in media["links"])
        assert media["retweet"]["media"]["links"] == []

    def test_retweet_recursion_only_one_level(self):
        # A retweet inside a retweet must not recurse infinitely.
        s = {
            "retweeted_status": {
                "user": {"screen_name": "A"}, "text_raw": "x",
                "retweeted_status": {"user": {"screen_name": "B"}, "text_raw": "y"},
            }
        }
        media = extract_media(s)
        assert media["retweet"]["author"] == "A"
        assert media["retweet"]["media"]["retweet"] is None  # inner not expanded

    def test_empty_status(self):
        m = extract_media({})
        assert m == {"images": [], "video": None, "links": [], "retweet": None}

    def test_all_image_urls_includes_retweet(self):
        s = {
            "pic_ids": ["a"], "pic_infos": {"a": {"largest": {"url": "OWN"}}},
            "retweeted_status": {
                "user": {"screen_name": "x"}, "text_raw": "t",
                "pic_ids": ["b"], "pic_infos": {"b": {"largest": {"url": "RT"}}},
            },
        }
        assert all_image_urls(s) == ["OWN", "RT"]

    def test_all_image_urls_empty(self):
        assert all_image_urls({"text_raw": "no pics"}) == []


class TestFullTextRich:
    def test_appends_image_line(self):
        s = {"text_raw": "看图", "pic_ids": ["a"], "pic_infos": {"a": {"largest": {"url": "IMG"}}}}
        out = full_text_rich(s)
        assert "看图" in out
        assert "📷 IMG" in out

    def test_appends_video_line(self):
        s = {"text_raw": "看视频", "page_info": {"object_type": "video", "object_id": "1034:42"}}
        out = full_text_rich(s)
        assert "🎬 https://video.weibo.com/show?fid=1034:42" in out

    def test_inline_resolved_link_not_duplicated_as_annotation(self):
        # The link is resolved inline (as a Markdown link) in the body → it must
        # NOT also appear as a separate 🔗 annotation line.
        s = {
            "text_raw": "网址在此：http://t.cn/AX91O2zl",
            "url_struct": [{"short_url": "http://t.cn/AX91O2zl",
                            "long_url": "https://qwenwork.cn/x", "url_title": "网页链接"}],
        }
        out = full_text_rich(s)
        assert "网址在此：[网页链接](https://qwenwork.cn/x)" in out
        assert "🔗" not in out  # not duplicated

    def test_link_not_in_body_still_annotated_as_markdown(self):
        # A url_struct link NOT present in the body text (Weibo appends it as a
        # card) still surfaces as a 🔗 annotation, in Markdown-link form.
        s = {
            "text_raw": "纯文字没有短链",
            "url_struct": [{"short_url": "http://t.cn/OTHER",
                            "long_url": "https://example.com/card", "url_title": "标题"}],
        }
        out = full_text_rich(s)
        assert "🔗 [标题](https://example.com/card)" in out

    def test_repost_block(self):
        s = {
            "text_raw": "引起舒适",
            "retweeted_status": {
                "user": {"screen_name": "原作者", "idstr": "999"},
                "mblogid": "ORIGID",
                "text_raw": "原文内容",
            },
        }
        out = full_text_rich(s)
        assert "引起舒适" in out
        # No blockquote "> " prefix any more; header links to the source weibo.
        assert "> " not in out
        assert "↩️ 转发自 @原作者" in out
        assert "[源微博](https://weibo.com/999/ORIGID)" in out
        assert "原文内容" in out

    def test_repost_multiline_not_quoted(self):
        s = {
            "text_raw": "转",
            "retweeted_status": {"user": {"screen_name": "作者"}, "text_raw": "第一行\n第二行"},
        }
        out = full_text_rich(s)
        # Lines kept as-is, without any "> " prefix.
        assert "第一行" in out
        assert "第二行" in out
        assert "> " not in out

    def test_repost_without_ids_has_no_link(self):
        # Missing uid/mblogid → header degrades gracefully, no broken link.
        s = {
            "text_raw": "转",
            "retweeted_status": {"user": {"screen_name": "作者"}, "text_raw": "原文"},
        }
        out = full_text_rich(s)
        assert "↩️ 转发自 @作者:" in out
        assert "源微博" not in out

    def test_plain_weibo_unchanged(self):
        s = {"text_raw": "普通微博"}
        assert full_text_rich(s) == "普通微博"


# ── to_markdown tests ────────────────────────────────────────────────


class TestToMarkdown:
    def _sample(self):
        return {
            "mblogid": "R9EM6ApWL",
            "user": {"idstr": "5648162302", "screen_name": "高飞"},
            "created_at": "Tue Jul 21 07:20:00 +0800 2026",
            "isLongText": True,
            "comments_count": 8,
            "reposts_count": 41,
            "attitudes_count": 46,
            "text_raw": "正文内容",
        }

    def test_has_frontmatter_delimiters(self):
        md = to_markdown(self._sample())
        assert md.startswith("---\n")
        assert "\n---\n" in md

    def test_frontmatter_fields(self):
        md = to_markdown(self._sample())
        assert "mblogid: R9EM6ApWL" in md
        assert 'author: "高飞"' in md
        assert "url: https://weibo.com/5648162302/R9EM6ApWL" in md
        assert "created_at: 2026-07-21 07:20:00" in md
        assert "is_long_text: true" in md

    def test_author_quoted_and_escaped(self):
        # Screen names with YAML-special chars must stay valid (quoted/escaped).
        s = self._sample()
        s["user"] = {"idstr": "1", "screen_name": '奇怪: 名字 "带引号"'}
        md = to_markdown(s)
        assert 'author: "奇怪: 名字 \\"带引号\\""' in md
        # And the whole frontmatter round-trips through a YAML parser.
        import yaml
        fm = md.split("---\n")[1]
        assert yaml.safe_load(fm)["author"] == '奇怪: 名字 "带引号"'

    def test_author_empty_when_no_user(self):
        s = self._sample()
        s["user"] = {}
        assert 'author: ""' in to_markdown(s)

    def test_frontmatter_omits_volatile_counts(self):
        # Engagement counts change over time → not persisted in frontmatter.
        md = to_markdown(self._sample())
        assert "comments_count" not in md
        assert "reposts_count" not in md
        assert "attitudes_count" not in md

    def test_body_after_frontmatter(self):
        md = to_markdown(self._sample())
        header, _, body = md.partition("\n---\n\n")
        assert body.strip() == "正文内容"

    def test_is_long_text_false_lowercase(self):
        s = self._sample()
        s["isLongText"] = False
        assert "is_long_text: false" in to_markdown(s)

    def test_url_empty_when_no_uid(self):
        s = self._sample()
        s["user"] = {}
        assert "url: \n" in to_markdown(s) or "url:\n" in to_markdown(s)

    def test_media_in_body(self):
        s = self._sample()
        s["pic_ids"] = ["a"]
        s["pic_infos"] = {"a": {"largest": {"url": "IMG"}}}
        md = to_markdown(s)
        assert "📷 IMG" in md


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

    def test_resolves_inline_tcn_from_url_struct(self):
        # A titled t.cn link in the body is resolved via url_struct to a Markdown
        # link inline, so both context and a readable title are preserved.
        s = {
            "text_raw": "网址在此：http://t.cn/AX91O2zl 谢谢",
            "url_struct": [{"short_url": "http://t.cn/AX91O2zl",
                            "long_url": "https://qwenwork.cn/?a=1", "url_title": "网页链接"}],
        }
        assert full_text(s) == "网址在此：[网页链接](https://qwenwork.cn/?a=1) 谢谢"

    def test_resolves_inline_untitled_tcn_to_bare_url(self):
        # Without a title, the inline resolution is the bare URL.
        s = {
            "text_raw": "网址在此：http://t.cn/AX91O2zl",
            "url_struct": [{"short_url": "http://t.cn/AX91O2zl",
                            "long_url": "https://qwenwork.cn/?a=1"}],
        }
        assert full_text(s) == "网址在此：https://qwenwork.cn/?a=1"

    def test_drops_inline_tcn_when_no_url_struct(self):
        # No url_struct → unresolvable → dropped as noise.
        s = {"text_raw": "看 http://t.cn/AX91O2zl 完"}
        assert full_text(s) == "看 完"


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
