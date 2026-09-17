import unittest
from unittest.mock import patch

import httpx

from forgefy_cli.login import device_login, logout


def _start_response(expires_in=600, interval=5):
    return httpx.Response(
        200,
        json={
            "device_code": "dc-secret",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://forgefy.app/cli-auth",
            "verification_uri_complete": "https://forgefy.app/cli-auth?user_code=ABCD-EFGH",
            "expires_in": expires_in,
            "interval": interval,
        },
    )


class DeviceLoginTests(unittest.TestCase):
    def _run(self, poll_bodies, expires_in=600, interval=1):
        polls = iter(poll_bodies)

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/device/start"):
                return _start_response(expires_in=expires_in, interval=interval)
            if request.url.path.endswith("/device/poll"):
                body = next(polls)
                if isinstance(body, int):  # simulate an HTTP error status
                    return httpx.Response(body, json={"detail": "error"})
                return httpx.Response(200, json=body)
            raise AssertionError(f"unexpected request: {request.url}")

        client = httpx.Client(transport=httpx.MockTransport(respond))
        outputs = []
        opened = []
        clock = {"t": 0.0}

        def fake_now():
            return clock["t"]

        def fake_sleep(seconds):
            clock["t"] += seconds

        with patch("forgefy_cli.login.save_credentials") as mock_save:
            mock_save.return_value = "/fake/path"
            code = device_login(
                client,
                api_url="https://forgefy.app",
                open_browser=lambda url: opened.append(url) or True,
                output=outputs.append,
                sleep=fake_sleep,
                now=fake_now,
            )
        client.close()
        return code, outputs, opened, mock_save

    def test_happy_path_saves_credentials_after_pending_then_approved(self):
        code, outputs, opened, mock_save = self._run(
            [{"status": "pending"}, {"status": "approved", "api_key": "fgy_live_abc"}]
        )
        self.assertEqual(code, 0)
        self.assertEqual(opened, ["https://forgefy.app/cli-auth?user_code=ABCD-EFGH"])
        mock_save.assert_called_once_with("fgy_live_abc")
        self.assertTrue(any("Confirmation code: ABCD-EFGH" in line for line in outputs))
        self.assertTrue(any("Logged in" in line for line in outputs))

    def test_denied_returns_1_without_saving(self):
        code, outputs, _, mock_save = self._run([{"status": "denied"}])
        self.assertEqual(code, 1)
        mock_save.assert_not_called()
        self.assertTrue(any("denied" in line for line in outputs))

    def test_expired_returns_1(self):
        code, outputs, _, mock_save = self._run([{"status": "expired"}])
        self.assertEqual(code, 1)
        mock_save.assert_not_called()
        self.assertTrue(any("expired" in line for line in outputs))

    def test_approved_without_key_returns_1(self):
        code, _outputs, _, mock_save = self._run([{"status": "approved"}])
        self.assertEqual(code, 1)
        mock_save.assert_not_called()

    def test_transient_poll_error_is_retried(self):
        code, _outputs, _, mock_save = self._run(
            [500, {"status": "approved", "api_key": "fgy_live_retry"}]
        )
        self.assertEqual(code, 0)
        mock_save.assert_called_once_with("fgy_live_retry")

    def test_timeout_returns_1_when_never_approved(self):
        # expires_in shorter than one interval — loop body never executes.
        code, outputs, _, mock_save = self._run([], expires_in=0, interval=5)
        self.assertEqual(code, 1)
        mock_save.assert_not_called()
        self.assertTrue(any("timed out" in line for line in outputs))

    def test_start_network_error_returns_1(self):
        def respond(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        client = httpx.Client(transport=httpx.MockTransport(respond))
        outputs = []
        code = device_login(client, api_url="https://forgefy.app", output=outputs.append)
        client.close()
        self.assertEqual(code, 1)
        self.assertTrue(any("cannot reach" in line for line in outputs))

    def test_start_http_error_returns_1(self):
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"detail": "down"})

        client = httpx.Client(transport=httpx.MockTransport(respond))
        outputs = []
        code = device_login(client, api_url="https://forgefy.app", output=outputs.append)
        client.close()
        self.assertEqual(code, 1)


class LogoutTests(unittest.TestCase):
    def test_logout_reports_removal(self):
        with patch("forgefy_cli.login.clear_credentials", return_value=True):
            self.assertEqual(logout(), 0)

    def test_logout_reports_not_logged_in(self):
        with patch("forgefy_cli.login.clear_credentials", return_value=False):
            self.assertEqual(logout(), 0)


if __name__ == "__main__":
    unittest.main()
