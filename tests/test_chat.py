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

    def chat(self, model, system, messages, on_token=None):
        self.calls.append(list(messages))
        return f"reply{len(self.calls)}"


class ChatTests(unittest.TestCase):
    def run_chat(self, client, lines):
        outputs = []
        iterator = iter(lines)
        # on_token=None: exercise the old blocking contract here (one output()
        # call per full reply) — streaming itself is covered by TestStreaming below.
        code = chat_loop(client, "m", "sys", input_fn=lambda *a: next(iterator), output=outputs.append, on_token=None)
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
            def chat(self, model, system, messages, on_token=None):
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
            def chat(self, model, system, messages, on_token=None):
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


class StreamingFakeClient:
    """Simulates on_token by splitting the canned reply into fragments."""

    def __init__(self, reply="streamed reply"):
        self.reply = reply
        self.calls = []

    def chat(self, model, system, messages, on_token=None):
        self.calls.append(list(messages))
        if on_token is not None:
            for ch in self.reply:
                on_token(ch)
        return self.reply


class StreamingChatLoopTests(unittest.TestCase):
    def test_on_token_receives_fragments_and_output_gets_one_blank_close(self):
        outputs = []
        client = StreamingFakeClient("hi")
        fragments = []
        iterator = iter(["hello", "/exit"])
        code = chat_loop(client, "m", "sys", input_fn=lambda *a: next(iterator),
                          output=outputs.append, on_token=fragments.append)
        self.assertEqual(code, 0)
        self.assertEqual(fragments, ["h", "i"])
        # The full reply is never re-printed through output() once streamed —
        # only the trailing blank-line close, so it isn't shown twice.
        self.assertEqual(outputs, [""])

    def test_history_still_records_the_full_reply(self):
        client = StreamingFakeClient("full text")
        iterator = iter(["hi", "/exit"])
        turns = []
        chat_loop(client, "m", "sys", input_fn=lambda *a: next(iterator),
                  output=lambda t: None, on_token=lambda t: None, on_turn=turns.append)
        self.assertEqual(turns[-1], [("user", "hi"), ("assistant", "full text")])

    def test_provider_error_during_streaming_keeps_session_alive(self):
        class FailingStream(StreamingFakeClient):
            def chat(self, model, system, messages, on_token=None):
                raise ProviderError("stream broke")

        outputs = []
        iterator = iter(["hi", "again", "/exit"])
        code = chat_loop(FailingStream(), "m", "sys", input_fn=lambda *a: next(iterator), output=outputs.append)
        self.assertEqual(code, 0)
        self.assertEqual(outputs.count("Forgefy: stream broke"), 2)


class ModelClientStreamingTests(unittest.TestCase):
    """Exercises ModelClient._stream_complete_messages against real SSE-shaped bytes."""

    @staticmethod
    def sse(*fragments, finish_reason="stop"):
        lines = [
            f'data: {json.dumps({"choices": [{"delta": {"content": f}, "finish_reason": None}]})}\n\n'
            for f in fragments
        ]
        lines.append(f'data: {json.dumps({"choices": [{"delta": {}, "finish_reason": finish_reason}]})}\n\n')
        lines.append("data: [DONE]\n\n")
        return "".join(lines)

    def test_streams_fragments_and_returns_full_text(self):
        def respond(request):
            self.assertTrue(json.loads(request.content)["stream"])
            return httpx.Response(200, content=self.sse("Hel", "lo", " world"))

        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = ModelClient(BUILTINS["ollama"], http)
            fragments = []
            result = client.complete("m", "sys", "hi", on_token=fragments.append)
        self.assertEqual(result, "Hello world")
        self.assertEqual(fragments, ["Hel", "lo", " world"])

    def test_length_finish_reason_raises(self):
        def respond(request):
            return httpx.Response(200, content=self.sse("partial", finish_reason="length"))

        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = ModelClient(BUILTINS["ollama"], http)
            with self.assertRaises(ProviderError) as ctx:
                client.complete("m", "sys", "hi", on_token=lambda t: None)
        self.assertIn("truncated", str(ctx.exception))

    def test_http_error_status_surfaces_before_streaming_body(self):
        def respond(request):
            return httpx.Response(429, json={"error": "rate limited"})

        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = ModelClient(BUILTINS["ollama"], http)
            with self.assertRaises(ProviderError) as ctx:
                client.complete("m", "sys", "hi", on_token=lambda t: None)
        self.assertIn("HTTP 429", str(ctx.exception))

    def test_non_sse_body_raises_clear_error(self):
        def respond(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]})

        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = ModelClient(BUILTINS["ollama"], http)
            with self.assertRaises(ProviderError) as ctx:
                client.complete("m", "sys", "hi", on_token=lambda t: None)
        self.assertIn("no-stream", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
