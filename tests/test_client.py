"""Unit tests for WeiboClient — mock all API methods, verify URL/params/response handling."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from weibo_cli.client import WeiboClient
from weibo_cli.exceptions import SessionExpiredError, WeiboApiError


# ── Response handling ────────────────────────────────────────────────


class TestHandleResponse:
    def test_ok_1_with_data_key(self, mock_client):
        raw = {"ok": 1, "data": {"realtime": [{"word": "test"}]}}
        result = mock_client._handle_response(raw, "test")
        assert result == {"realtime": [{"word": "test"}]}

    def test_ok_1_without_data_key(self, mock_client):
        raw = {"ok": 1, "statuses": []}
        result = mock_client._handle_response(raw, "test")
        assert result == raw

    def test_ok_minus_100_raises_session_expired(self, mock_client):
        raw = {"ok": -100, "url": "https://weibo.com/login.php"}
        with pytest.raises(SessionExpiredError):
            mock_client._handle_response(raw, "test")

    def test_ok_0_login_message_raises_session_expired(self, mock_client):
        raw = {"ok": 0, "message": "请先登录"}
        with pytest.raises(SessionExpiredError):
            mock_client._handle_response(raw, "test")

    def test_ok_0_login_后使用_raises_session_expired(self, mock_client):
        raw = {"ok": 0, "message": "请登录后使用"}
        with pytest.raises(SessionExpiredError):
            mock_client._handle_response(raw, "test")

    def test_ok_0_generic_error(self, mock_client):
        raw = {"ok": 0, "message": "参数错误"}
        with pytest.raises(WeiboApiError, match="参数错误"):
            mock_client._handle_response(raw, "test")


# ── Context manager ─────────────────────────────────────────────────


class TestContextManager:
    def test_enter_creates_client(self, mock_credential):
        client = WeiboClient(mock_credential, request_delay=0)
        with client as c:
            assert c.client is not None
            assert c._http is not None

    def test_exit_closes_client(self, mock_credential):
        client = WeiboClient(mock_credential, request_delay=0)
        with client:
            pass
        assert client._http is None

    def test_client_without_context_raises(self, mock_credential):
        client = WeiboClient(mock_credential, request_delay=0)
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = client.client


# ── Rate limiting ────────────────────────────────────────────────────


class TestRateLimiting:
    def test_mark_request_increments_counter(self, mock_client):
        assert mock_client._request_count == 0
        mock_client._mark_request()
        assert mock_client._request_count == 1
        mock_client._mark_request()
        assert mock_client._request_count == 2


# ── API method tests (mocked HTTP) ──────────────────────────────────


class TestHotSearchAPI:
    def test_get_hot_search_calls_correct_url(self, mock_client, hot_search_response):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps(hot_search_response)
        mock_resp.json.return_value = hot_search_response
        mock_resp.cookies = httpx.Cookies()
        mock_client._http.request.return_value = mock_resp

        result = mock_client.get_hot_search()
        assert "realtime" in result
        assert len(result["realtime"]) == 2
        assert result["realtime"][0]["word"] == "省考"

        # Verify correct URL was called
        call_args = mock_client._http.request.call_args
        assert call_args[0][0] == "GET"
        assert "/ajax/side/hotSearch" in call_args[0][1]


class TestProfileAPI:
    def test_get_profile_passes_uid(self, mock_client, profile_response):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps(profile_response)
        mock_resp.json.return_value = profile_response
        mock_resp.cookies = httpx.Cookies()
        mock_client._http.request.return_value = mock_resp

        result = mock_client.get_profile("1699432410")
        assert result["user"]["screen_name"] == "新华社"

        call_args = mock_client._http.request.call_args
        assert "/ajax/profile/info" in call_args[0][1]
        params = call_args[1].get("params", {})
        assert params["uid"] == "1699432410"


class TestWeiboDetailAPI:
    def test_get_weibo_detail(self, mock_client, weibo_detail_response):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps(weibo_detail_response)
        mock_resp.json.return_value = weibo_detail_response
        mock_resp.cookies = httpx.Cookies()
        mock_client._http.request.return_value = mock_resp

        result = mock_client.get_weibo_detail("Qw06Kd98p")
        assert result["mblogid"] == "Qw06Kd98p"
        assert result["user"]["screen_name"] == "新华社"

        call_args = mock_client._http.request.call_args
        assert "/ajax/statuses/show" in call_args[0][1]
        params = call_args[1].get("params", {})
        assert params["id"] == "Qw06Kd98p"


class TestHotTimelineAPI:
    def test_get_hot_timeline_default_params(self, mock_client):
        hot_response = {"ok": 1, "statuses": [], "max_id": "0"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps(hot_response)
        mock_resp.json.return_value = hot_response
        mock_resp.cookies = httpx.Cookies()
        mock_client._http.request.return_value = mock_resp

        result = mock_client.get_hot_timeline()
        assert "statuses" in result

        call_args = mock_client._http.request.call_args
        params = call_args[1].get("params", {})
        assert params["group_id"] == "102803"
        assert params["count"] == "10"

    def test_get_hot_timeline_custom_count(self, mock_client):
        hot_response = {"ok": 1, "statuses": [], "max_id": "0"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps(hot_response)
        mock_resp.json.return_value = hot_response
        mock_resp.cookies = httpx.Cookies()
        mock_client._http.request.return_value = mock_resp

        mock_client.get_hot_timeline(count=5)
        params = mock_client._http.request.call_args[1].get("params", {})
        assert params["count"] == "5"


class TestCommentsAPI:
    def test_get_comments_default_params(self, mock_client):
        comments_response = {"ok": 1, "data": [], "max_id": 0}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps(comments_response)
        mock_resp.json.return_value = comments_response
        mock_resp.cookies = httpx.Cookies()
        mock_client._http.request.return_value = mock_resp

        mock_client.get_comments("12345")
        params = mock_client._http.request.call_args[1].get("params", {})
        assert params["id"] == "12345"
        assert params["count"] == "20"
        assert "max_id" not in params

    def test_get_comments_with_max_id(self, mock_client):
        comments_response = {"ok": 1, "data": [], "max_id": 0}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps(comments_response)
        mock_resp.json.return_value = comments_response
        mock_resp.cookies = httpx.Cookies()
        mock_client._http.request.return_value = mock_resp

        mock_client.get_comments("12345", max_id=999)
        params = mock_client._http.request.call_args[1].get("params", {})
        assert params["max_id"] == "999"


class TestRepostsAPI:
    def test_get_reposts(self, mock_client):
        reposts_response = {"ok": 1, "data": [], "total_number": 0}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps(reposts_response)
        mock_resp.json.return_value = reposts_response
        mock_resp.cookies = httpx.Cookies()
        mock_client._http.request.return_value = mock_resp

        mock_client.get_reposts("12345", page=2)
        params = mock_client._http.request.call_args[1].get("params", {})
        assert params["id"] == "12345"
        assert params["page"] == "2"


# ── Retry behavior ──────────────────────────────────────────────────


class TestRetryBehavior:
    def test_retries_on_timeout(self, mock_client):
        mock_client._max_retries = 2
        mock_client._http.request.side_effect = httpx.TimeoutException("timeout")

        with pytest.raises(WeiboApiError, match="failed after"):
            mock_client._request("GET", "/ajax/test")

        assert mock_client._http.request.call_count == 2

    def test_retries_on_server_error(self, mock_client):
        mock_client._max_retries = 2
        error_resp = MagicMock()
        error_resp.status_code = 502
        error_resp.cookies = httpx.Cookies()
        mock_client._http.request.return_value = error_resp

        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.text = '{"ok": 1}'
        success_resp.json.return_value = {"ok": 1}
        success_resp.cookies = httpx.Cookies()

        mock_client._http.request.side_effect = [error_resp, success_resp]
        result = mock_client._request("GET", "/ajax/test")
        assert result == {"ok": 1}

    def test_4xx_wrapped_as_weibo_error(self, mock_client):
        # A 400 (e.g. bad uid) must surface as WeiboApiError, not raw httpx,
        # and must not be retried.
        resp = MagicMock()
        resp.status_code = 400
        resp.cookies = httpx.Cookies()
        mock_client._http.request.return_value = resp

        with pytest.raises(WeiboApiError, match="HTTP 400"):
            mock_client._request("GET", "/ajax/statuses/mymblog")
        assert mock_client._http.request.call_count == 1  # no retry on 4xx

    def test_html_response_raises_error(self, mock_client):
        html_resp = MagicMock()
        html_resp.status_code = 200
        html_resp.text = "<html>Login Required</html>"
        html_resp.cookies = httpx.Cookies()
        html_resp.raise_for_status.return_value = None
        mock_client._http.request.return_value = html_resp

        with pytest.raises(WeiboApiError, match="HTML"):
            mock_client._request("GET", "/ajax/test")


# ── Cookie merging ───────────────────────────────────────────────────


class TestCookieMerging:
    def test_merge_response_cookies(self, mock_credential):
        """Verify that response cookies are merged back into the session."""
        client = WeiboClient(mock_credential, request_delay=0)
        with client:
            resp = MagicMock()
            resp.cookies = httpx.Cookies()
            resp.cookies.set("NEW_COOKIE", "new_value")
            client._merge_response_cookies(resp)
            assert client.client.cookies.get("NEW_COOKIE") == "new_value"
