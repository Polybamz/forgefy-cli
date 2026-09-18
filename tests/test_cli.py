import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from forgefy_cli.cli import main
from forgefy_cli.config import BUILTINS, TEMPLATE, load_config, set_defaults, validate_provider
from forgefy_cli.context import build_prompt
from forgefy_cli.providers import ModelClient, ProviderError
from forgefy_cli.skills import system_prompt


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {"FORGEFY_CONFIG": str(self.root / "config.toml")})
        env.start()
        self.addCleanup(env.stop)

    def invoke(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_full_cli_request(self):
        (self.root / "main.py").write_text("x = 1", encoding="utf-8")
        def respond(request):
            self.assertEqual(request.url.path, "/v1/chat/completions")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "test-coder")
            self.assertIs(body["stream"], False)
            self.assertIn("x = 1", body["messages"][1]["content"])
            return httpx.Response(200, json={"choices": [{"message": {"content": "print('hello')"}, "finish_reason": "stop"}]})
        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch("forgefy_cli.cli.httpx.Client", return_value=client):
            code, out, _ = self.invoke(["run", "Write Python", "--model", "test-coder", "--workspace", str(self.root),
                                         "--file", "main.py", "--no-stream"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "print('hello')")

    def test_full_cli_request_streams_by_default(self):
        (self.root / "main.py").write_text("x = 1", encoding="utf-8")
        def respond(request):
            body = json.loads(request.content)
            self.assertIs(body["stream"], True)
            sse = (
                'data: {"choices": [{"delta": {"content": "print("}, "finish_reason": null}]}\n\n'
                'data: {"choices": [{"delta": {"content": "\'hello\')"}, "finish_reason": "stop"}]}\n\n'
                'data: [DONE]\n\n'
            )
            return httpx.Response(200, content=sse)
        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch("forgefy_cli.cli.httpx.Client", return_value=client):
            code, out, _ = self.invoke(["run", "Write Python", "--model", "test-coder",
                                         "--workspace", str(self.root), "--file", "main.py"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "print('hello')")

    def test_models(self):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": [{"id": "z"}, {"id": "a"}, {"id": "z"}]}))) as http:
            self.assertEqual(ModelClient(BUILTINS["ollama"], http).models(), ["a", "z"])

    def test_http_errors_do_not_expose_body(self):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="SECRET"))) as http:
            with self.assertRaises(ProviderError) as caught:
                ModelClient(BUILTINS["ollama"], http).models()
        self.assertNotIn("SECRET", str(caught.exception))

    def test_invalid_responses(self):
        for payload in ({}, {"choices": []}, {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}):
            with self.subTest(payload=payload), httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as http:
                with self.assertRaises(ProviderError):
                    ModelClient(BUILTINS["ollama"], http).complete("test", "system", "prompt")

    def test_missing_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                BUILTINS["openai"].headers()

    def test_provider_plugin(self):
        path = self.root / "config.toml"
        path.write_text('[providers.custom]\nbase_url = "http://localhost:1234/v1"\n', encoding="utf-8")
        self.assertIn("custom", load_config(path)[1])

    def test_insecure_provider_rejected(self):
        for url in ("http://example.com/v1", "https://user:secret@example.com/v1", "https://example.com/v1?key=secret"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_provider("custom", {"base_url": url})

    def test_context_boundary(self):
        with self.assertRaises(ValueError):
            build_prompt("code", self.root, [str(self.root)])
        (self.root / ".env").write_text("SECRET=1", encoding="utf-8")
        with self.assertRaises(ValueError):
            build_prompt("code", self.root, [".env"])
        with self.assertRaises(ValueError):
            build_prompt("x" * 120001, self.root, [])

    def test_config_init_preserves_existing(self):
        self.assertEqual(self.invoke(["config", "--init"])[0], 0)
        self.assertEqual(self.invoke(["config", "--init"])[0], 1)

    def test_model_required(self):
        code, _, err = self.invoke(["run", "Write Python"])
        self.assertEqual(code, 1)
        self.assertIn("--model", err)

    def test_skill_plugin(self):
        path = self.root / "skill.md"
        path.write_text("Use table-driven tests.", encoding="utf-8")
        self.assertIn("Use table-driven tests.", system_prompt("test", [path]))


class SetDefaultsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "config.toml"

    def test_creates_file_from_template_when_missing(self):
        result = set_defaults("forgefy", "some-model", self.path)
        self.assertEqual(result, self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn('default_provider = "forgefy"', text)
        self.assertIn('default_model = "some-model"', text)
        # The rest of TEMPLATE's helpful content (comments, plugin example)
        # is preserved, not replaced by a bare two-line file.
        self.assertIn("Provider plugin", text)
        data, _ = load_config(self.path)
        self.assertEqual(data["default_provider"], "forgefy")
        self.assertEqual(data["default_model"], "some-model")

    def test_upserts_into_existing_file_preserving_other_content(self):
        self.path.write_text(
            'default_provider = "ollama"\n'
            '# default_model = "old"\n'
            "\n"
            "[providers.custom]\n"
            'base_url = "http://localhost:1234/v1"\n',
            encoding="utf-8",
        )
        set_defaults("forgefy", "new-model", self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn('default_provider = "forgefy"', text)
        self.assertIn('default_model = "new-model"', text)
        self.assertNotIn("ollama", text)
        self.assertNotIn("old", text)
        self.assertIn("[providers.custom]", text)
        self.assertIn('base_url = "http://localhost:1234/v1"', text)

    def test_idempotent_rerun_does_not_duplicate_keys(self):
        set_defaults("forgefy", "model-a", self.path)
        set_defaults("forgefy", "model-b", self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text.count("default_provider ="), 1)
        self.assertEqual(text.count("default_model ="), 1)
        self.assertIn('default_model = "model-b"', text)

    def test_result_loads_cleanly_and_resolves_default_model(self):
        set_defaults("forgefy", "picked-model", self.path)
        data, providers = load_config(self.path)
        self.assertIn("forgefy", providers)
        self.assertEqual(data.get("default_model"), "picked-model")

    def test_rejects_values_with_quotes_or_newlines(self):
        for bad in ('has"quote', "has\nnewline"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    set_defaults(bad, "model", self.path)
                with self.assertRaises(ValueError):
                    set_defaults("forgefy", bad, self.path)
        self.assertFalse(self.path.exists())

    def test_template_still_has_both_keys_for_upsert_to_target(self):
        # Sanity check the assumption set_defaults relies on: TEMPLATE
        # already contains (commented or not) both keys to upsert into.
        self.assertIn("default_provider", TEMPLATE)
        self.assertIn("default_model", TEMPLATE)


if __name__ == "__main__":
    unittest.main()
