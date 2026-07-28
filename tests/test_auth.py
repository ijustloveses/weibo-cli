"""Unit tests for auth module — credential persistence, browser extraction, QR flow."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock


from weibo_cli.auth import (
    Credential,
    _render_qr_half_blocks,
    clear_credential,
    extract_browser_credential,
    get_credential,
    load_credential,
    save_credential,
)


# ── Credential class ────────────────────────────────────────────────


class TestCredential:
    def test_valid_credential(self):
        cred = Credential(cookies={"SUB": "abc", "SUBP": "xyz"})
        assert cred.is_valid

    def test_empty_credential_invalid(self):
        cred = Credential(cookies={})
        assert not cred.is_valid

    def test_to_dict_includes_saved_at(self):
        cred = Credential(cookies={"SUB": "abc"})
        d = cred.to_dict()
        assert "cookies" in d
        assert "saved_at" in d
        assert isinstance(d["saved_at"], float)

    def test_from_dict(self):
        cred = Credential.from_dict({"cookies": {"SUB": "abc"}, "saved_at": 0})
        assert cred.cookies == {"SUB": "abc"}

    def test_from_dict_missing_cookies(self):
        cred = Credential.from_dict({})
        assert cred.cookies == {}
        assert not cred.is_valid

    def test_cookie_header_format(self):
        cred = Credential(cookies={"A": "1", "B": "2"})
        header = cred.as_cookie_header()
        assert "A=1" in header
        assert "B=2" in header
        assert "; " in header

    def test_roundtrip(self):
        original = Credential(cookies={"SUB": "abc", "SUBP": "xyz", "X-CSRF-TOKEN": "csrf"})
        d = original.to_dict()
        restored = Credential.from_dict(d)
        assert restored.cookies == original.cookies


# ── Credential persistence ──────────────────────────────────────────


class TestCredentialPersistence:
    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr("weibo_cli.auth.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("weibo_cli.auth.CREDENTIAL_FILE", tmp_path / "credential.json")

        cred = Credential(cookies={"SUB": "test_sub"})
        save_credential(cred)

        loaded = load_credential()
        assert loaded is not None
        assert loaded.cookies == {"SUB": "test_sub"}

    def test_load_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("weibo_cli.auth.CREDENTIAL_FILE", tmp_path / "nonexistent.json")
        assert load_credential() is None

    def test_load_invalid_json(self, tmp_path, monkeypatch):
        cred_file = tmp_path / "credential.json"
        cred_file.write_text("not valid json!!!")
        monkeypatch.setattr("weibo_cli.auth.CREDENTIAL_FILE", cred_file)
        assert load_credential() is None

    def test_load_empty_cookies(self, tmp_path, monkeypatch):
        cred_file = tmp_path / "credential.json"
        cred_file.write_text(json.dumps({"cookies": {}, "saved_at": time.time()}))
        monkeypatch.setattr("weibo_cli.auth.CREDENTIAL_FILE", cred_file)
        assert load_credential() is None

    def test_clear_credential(self, tmp_path, monkeypatch):
        cred_file = tmp_path / "credential.json"
        cred_file.write_text("{}")
        monkeypatch.setattr("weibo_cli.auth.CREDENTIAL_FILE", cred_file)
        clear_credential()
        assert not cred_file.exists()

    def test_clear_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("weibo_cli.auth.CREDENTIAL_FILE", tmp_path / "nonexistent.json")
        # Should not raise
        clear_credential()

    def test_load_triggers_refresh_when_stale(self, tmp_path, monkeypatch):
        cred_file = tmp_path / "credential.json"
        old_time = time.time() - (8 * 86400)  # 8 days ago
        cred_file.write_text(json.dumps({"cookies": {"SUB": "old"}, "saved_at": old_time}))
        monkeypatch.setattr("weibo_cli.auth.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("weibo_cli.auth.CREDENTIAL_FILE", cred_file)

        fresh_cred = Credential(cookies={"SUB": "fresh"})
        monkeypatch.setattr("weibo_cli.auth.extract_browser_credential", lambda: fresh_cred)

        loaded = load_credential()
        assert loaded is not None
        assert loaded.cookies["SUB"] == "fresh"

    def test_load_uses_old_when_refresh_fails(self, tmp_path, monkeypatch):
        cred_file = tmp_path / "credential.json"
        old_time = time.time() - (8 * 86400)
        cred_file.write_text(json.dumps({"cookies": {"SUB": "old"}, "saved_at": old_time}))
        monkeypatch.setattr("weibo_cli.auth.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("weibo_cli.auth.CREDENTIAL_FILE", cred_file)
        monkeypatch.setattr("weibo_cli.auth.extract_browser_credential", lambda: None)

        loaded = load_credential()
        assert loaded is not None
        assert loaded.cookies["SUB"] == "old"

    def test_file_permissions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("weibo_cli.auth.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("weibo_cli.auth.CREDENTIAL_FILE", tmp_path / "credential.json")

        save_credential(Credential(cookies={"SUB": "test"}))
        perms = (tmp_path / "credential.json").stat().st_mode & 0o777
        assert perms == 0o600


# ── Browser cookie extraction ───────────────────────────────────────


class TestBrowserExtraction:
    def test_extraction_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr("weibo_cli.auth.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("weibo_cli.auth.CREDENTIAL_FILE", tmp_path / "credential.json")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"browser": "Chrome", "cookies": {"SUB": "extracted"}})

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)
        cred = extract_browser_credential()
        assert cred is not None
        assert cred.cookies["SUB"] == "extracted"

    def test_extraction_no_cookies(self, monkeypatch):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"error": "no_cookies"})

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)
        assert extract_browser_credential() is None

    def test_extraction_not_installed(self, monkeypatch):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"error": "not_installed"})

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)
        assert extract_browser_credential() is None

    def test_extraction_subprocess_failure(self, monkeypatch):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)
        assert extract_browser_credential() is None

    def test_extraction_timeout(self, monkeypatch):
        import subprocess
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired("cmd", 15)))
        assert extract_browser_credential() is None

    def test_extraction_invalid_json(self, monkeypatch):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not json"

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)
        assert extract_browser_credential() is None

    def test_extraction_with_cookie_source(self, monkeypatch, tmp_path):
        monkeypatch.setattr("weibo_cli.auth.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("weibo_cli.auth.CREDENTIAL_FILE", tmp_path / "credential.json")

        captured_cmd = {}

        def fake_run(cmd, **kw):
            captured_cmd["args"] = cmd
            result = MagicMock()
            result.returncode = 0
            result.stdout = json.dumps({"browser": "Firefox", "cookies": {"SUB": "fx"}})
            return result

        monkeypatch.setattr("subprocess.run", fake_run)
        cred = extract_browser_credential(cookie_source="Firefox")
        assert cred is not None
        assert "Firefox" in captured_cmd["args"]


# ── get_credential chain ────────────────────────────────────────────


class TestGetCredential:
    def test_returns_saved_first(self, monkeypatch):
        saved = Credential(cookies={"SUB": "saved"})
        monkeypatch.setattr("weibo_cli.auth.load_credential", lambda: saved)
        monkeypatch.setattr("weibo_cli.auth.extract_browser_credential", lambda: None)

        result = get_credential()
        assert result.cookies["SUB"] == "saved"

    def test_falls_back_to_browser(self, monkeypatch):
        browser_cred = Credential(cookies={"SUB": "browser"})
        monkeypatch.setattr("weibo_cli.auth.load_credential", lambda: None)
        monkeypatch.setattr("weibo_cli.auth.extract_browser_credential", lambda: browser_cred)

        result = get_credential()
        assert result.cookies["SUB"] == "browser"

    def test_returns_none_when_all_fail(self, monkeypatch):
        monkeypatch.setattr("weibo_cli.auth.load_credential", lambda: None)
        monkeypatch.setattr("weibo_cli.auth.extract_browser_credential", lambda: None)

        assert get_credential() is None


# ── QR rendering ────────────────────────────────────────────────────


class TestQRRendering:
    def test_render_empty_matrix(self):
        assert _render_qr_half_blocks([]) == ""

    def test_render_small_matrix(self):
        matrix = [
            [True, False],
            [False, True],
        ]
        result = _render_qr_half_blocks(matrix)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_render_all_true(self):
        # 4x4 matrix ensures full blocks survive the quiet zone padding
        matrix = [[True]*4 for _ in range(4)]
        result = _render_qr_half_blocks(matrix)
        assert "█" in result

    def test_render_all_false(self):
        matrix = [[False, False], [False, False]]
        result = _render_qr_half_blocks(matrix)
        # Should produce spaces (with quiet zone)
        assert isinstance(result, str)


# ── QR image download ────────────────────────────────────────────────


class TestDownloadQrImage:
    def test_saves_png_bytes(self, tmp_path, monkeypatch):
        import httpx

        from weibo_cli.auth import _download_qr_image

        resp = MagicMock()
        resp.content = b"\x89PNG\r\n\x1a\nFAKE"
        resp.raise_for_status.return_value = None
        monkeypatch.setattr(httpx, "get", lambda *a, **k: resp)

        dest = tmp_path / "qr.png"
        result = _download_qr_image("https://qr.example/img.png", dest)
        assert result == dest
        assert dest.read_bytes() == b"\x89PNG\r\n\x1a\nFAKE"

    def test_prefixes_scheme_relative_url(self, tmp_path, monkeypatch):
        import httpx

        from weibo_cli.auth import _download_qr_image

        seen = {}

        def fake_get(url, *a, **k):
            seen["url"] = url
            resp = MagicMock()
            resp.content = b"x"
            resp.raise_for_status.return_value = None
            return resp

        monkeypatch.setattr(httpx, "get", fake_get)
        _download_qr_image("//qr.example/img.png", tmp_path / "qr.png")
        assert seen["url"].startswith("https://")

    def test_returns_none_on_failure(self, tmp_path, monkeypatch):
        import httpx

        from weibo_cli.auth import _download_qr_image

        def boom(*a, **k):
            raise httpx.ConnectError("no network")

        monkeypatch.setattr(httpx, "get", boom)
        # Best-effort: swallow the error and return None (terminal QR still shown).
        assert _download_qr_image("https://qr.example/img.png", tmp_path / "qr.png") is None
