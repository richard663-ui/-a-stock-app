import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules import cloud_bridge as cb  # noqa: E402

URL = "https://example.supabase.co"


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload
        self.content = b"" if payload is None else json.dumps(payload).encode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, script):
        self.script = script          # callable(method, url, headers, body) -> FakeResponse
        self.calls = []
        self.trust_env = False
        self.headers = {}

    def request(self, method, url, headers=None, timeout=None, verify=None, **kwargs):
        self.calls.append((method, url, dict(headers or {})))
        return self.script(method, url, headers or {}, kwargs.get("json"))

    def post(self, url, json=None, headers=None, timeout=None, verify=None):
        self.calls.append(("POST", url, dict(headers or {})))
        return self.script("POST", url, headers or {}, json)

    def mount(self, *args):
        pass


class CloudBridgeAuthTests(unittest.TestCase):
    def setUp(self):
        cb._TOKENS.clear()

    def bridge(self, config, script):
        session = FakeSession(script)
        with mock.patch.object(cb, "_build_session", return_value=session):
            return cb.CloudBridge(config), session

    def test_new_secret_key_is_never_sent_as_bearer(self):
        bridge, session = self.bridge(cb.BridgeConfig(URL, "sb_secret_abc", "family-qmt-01"),
                                      lambda m, u, h, b: FakeResponse(200, []))
        bridge.get_requested_symbol()
        headers = session.calls[0][2]
        self.assertEqual(headers["apikey"], "sb_secret_abc")
        self.assertNotIn("Authorization", headers)

    def test_legacy_service_role_jwt_keeps_both_headers(self):
        bridge, session = self.bridge(cb.BridgeConfig(URL, "eyJlegacy", "family-qmt-01"),
                                      lambda m, u, h, b: FakeResponse(200, []))
        bridge.get_requested_symbol()
        self.assertEqual(session.calls[0][2]["Authorization"], "Bearer eyJlegacy")

    def test_device_mode_signs_in_once_and_reuses_token(self):
        def script(method, url, headers, body):
            if "/auth/v1/token" in url:
                return FakeResponse(200, {"access_token": "tok-1", "refresh_token": "r1", "expires_in": 3600})
            return FakeResponse(200, [{"symbol": "600522.SH"}])
        config = cb.BridgeConfig(URL, "", "family-qmt-01", publishable_key="sb_publishable_x",
                                 device_email="bridge@example.com", device_password="pw")
        self.assertEqual(config.mode, "device")
        self.assertEqual(config.validation_errors(), [])
        bridge, session = self.bridge(config, script)
        self.assertEqual(bridge.get_requested_symbol(), "600522.SH")
        bridge.get_requested_symbol()
        token_calls = [c for c in session.calls if "/auth/v1/token" in c[1]]
        self.assertEqual(len(token_calls), 1)
        rest_headers = [c[2] for c in session.calls if "/rest/v1/" in c[1]]
        self.assertTrue(all(h["Authorization"] == "Bearer tok-1" and h["apikey"] == "sb_publishable_x" for h in rest_headers))

    def test_device_mode_reauthenticates_once_on_401(self):
        state = {"token": 0}

        def script(method, url, headers, body):
            if "/auth/v1/token" in url:
                state["token"] += 1
                return FakeResponse(200, {"access_token": f"tok-{state['token']}", "expires_in": 3600})
            if headers.get("Authorization") == "Bearer tok-1":
                return FakeResponse(401, {"message": "JWT expired"})
            return FakeResponse(200, [])
        config = cb.BridgeConfig(URL, "", "family-qmt-01", publishable_key="sb_publishable_x",
                                 device_email="bridge@example.com", device_password="pw")
        bridge, session = self.bridge(config, script)
        bridge.get_requested_symbol()
        self.assertEqual(state["token"], 2)

    def test_config_without_any_credential_is_invalid(self):
        errors = cb.BridgeConfig(URL, "", "family-qmt-01").validation_errors()
        self.assertTrue(any("SUPABASE_SERVICE_KEY is missing" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
