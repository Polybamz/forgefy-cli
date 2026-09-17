import unittest
from unittest.mock import patch

import httpx
import json

from forgefy_cli.config import BUILTINS
from forgefy_cli.providers import ModelClient

from forgefy_cli.chat import chat_loop
from forgefy_cli.providers import ProviderError


class FakeClient:
    def __init__(self):
        self.calls = []

    def chat(self, model, system, messages):
        self.calls.append(list(messages))
        return f"reply{len(self.calls)}"


class ChatTests(unittest.TestCase):
    def run_chat(self, client, lines):
        outputs = []
        iterator = iter(lines)
        code = chat_loop(client, "m", "sys", input_fn=lambda *a: next(iterator), output=outputs.append)
        return code, outputs

    def test_history_is_sent_each_turn(self):
        client = FakeClient()
        code, outputs = self.run_chat(client, ["hello there", "follow up", "/exit"])
        self.assertEqual(code, 0)
        self.assertEqual(outputs, ["reply1", "reply2"])
        self.assertEqual(client.calls[0], [("user", "hello there")])
        self.assertEqual(client.calls[1], [("user", "hello there"), ("assistant", "reply1"), ("user", "follow up")])

    def test_new_clears_history(self):
        client = FakeClient()
        code, outputs = self.run_chat(client, ["one", "/new", "two", "/exit"])
        self.assertEqual(code, 0)
        self.assertEqual(client.calls[0], [("user", "one")])
        self.assertEqual(client.calls[1], [("user", "two")])
        self.assertEqual(outputs[-1], "reply2")

    def test_provider_error_keeps_session_alive(self):
        class Failing(FakeClient):
            def chat(self, model, system, messages):
                raise ProviderError("boom")

        code, outputs = self.run_chat(Failing(), ["hi", "again", "/exit"])
        self.assertEqual(code, 0)
        self.assertEqual(outputs.count("Forgefy: boom"), 2)

    def test_oversized_turn_is_rejected(self):
        client = FakeClient()
        code, outputs = self.run_chat(client, ["x" * 120001, "/exit"])
        self.assertEqual(code, 0)
        self.assertEqual(client.calls, [])
        self.assertIn("context cap", outputs[0])

    def test_trimming_keeps_complete_pairs(self):
        client = FakeClient()
        with patch("forgefy_cli.chat.LIMIT", 15):
            _, outputs = self.run_chat(client, ["1234567890", "second", "/exit"])
        self.assertEqual(client.calls[1], [("user", "second")])
        self.assertIn("omitted 1", outputs[-2])

    def test_failed_trim_does_not_destroy_history(self):
        class Intermittent(FakeClient):
            def chat(self, model, system, messages):
                self.calls.append(list(messages))
                if len(self.calls) == 2:
                    raise ProviderError("failed")
                return "ok"
        client = Intermittent()
        with patch("forgefy_cli.chat.LIMIT", 15):
            self.run_chat(client, ["first", "x" * 12, "next", "/exit"])
        self.assertEqual(client.calls[2], [("user", "first"), ("assistant", "ok"), ("user", "next")])

    def test_chat_wire_format(self):
        requests = []
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]})
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            self.run_chat(ModelClient(BUILTINS["ollama"], http), ["one", "two", "/exit"])
        self.assertEqual(requests[1]["messages"], [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "two"},
        ])

    def test_eof_ends_session(self):
        def closed(*a):
            raise EOFError

        code = chat_loop(FakeClient(), "m", "sys", input_fn=closed, output=str)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
