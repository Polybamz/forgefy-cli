import unittest
from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

import httpx

from forgefy_cli.release import compare, installed_version, latest_version

PYPI_URL = "https://pypi.org/pypi/forgefy-cli/json"


class CompareTests(unittest.TestCase):
    def test_compare_table(self):
        cases = [
            ("0.3.0", "0.3.0", 0),
            ("0.3.1", "0.3.0", 1),
            ("0.3.0", "0.3.1", -1),
            ("0.3", "0.3.0", 0),
            ("0.3.0", "0.3", 0),
            ("0.3.1rc1", "0.3.1", 0),
            ("1.2.3-beta", "1.2.3", 0),
            ("1.0.0", "0.9.9", 1),
            ("0.9.9", "1.0.0", -1),
            ("1.10.0", "1.9.0", 1),
            ("1", "1.0.1", -1),
            ("2.0.0", "10.0.0", -1),
        ]
        for left, right, expected in cases:
            with self.subTest(left=left, right=right):
                self.assertEqual(compare(left, right), expected)


class LatestVersionTests(unittest.TestCase):
    def _client(self, respond):
        return httpx.Client(transport=httpx.MockTransport(respond))

    def test_latest_version_reads_pypi_json(self):
        def respond(request):
            self.assertEqual(str(request.url), PYPI_URL)
            return httpx.Response(200, json={"info": {"version": "1.4.2"}})

        with self._client(respond) as http:
            self.assertEqual(latest_version(http), "1.4.2")

    def test_latest_version_none_on_http_error(self):
        with self._client(lambda request: httpx.Response(500, json={"detail": "down"})) as http:
            self.assertIsNone(latest_version(http))

    def test_latest_version_none_on_request_error(self):
        def respond(request):
            raise httpx.ConnectError("refused", request=request)

        with self._client(respond) as http:
            self.assertIsNone(latest_version(http))

    def test_latest_version_none_on_malformed_payloads(self):
        payloads = [
            httpx.Response(200, text="not json"),
            httpx.Response(200, json=[1, 2, 3]),
            httpx.Response(200, json={}),
            httpx.Response(200, json={"info": "nope"}),
            httpx.Response(200, json={"info": {"version": 3}}),
        ]
        for response in payloads:
            with self.subTest(status=response.status_code, body=response.text):
                with self._client(lambda request, response=response: response) as http:
                    self.assertIsNone(latest_version(http))


class InstalledVersionTests(unittest.TestCase):
    def test_installed_version_is_a_string(self):
        self.assertIsInstance(installed_version(), str)

    def test_installed_version_falls_back_when_metadata_missing(self):
        with patch("importlib.metadata.version", side_effect=PackageNotFoundError("forgefy-cli")):
            self.assertEqual(installed_version(), "0.0.0-dev")


if __name__ == "__main__":
    unittest.main()