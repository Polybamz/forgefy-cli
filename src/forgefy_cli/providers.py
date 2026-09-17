"""OpenAI-compatible model transport, shared by built-in and custom profiles."""
from __future__ import annotations

import json
from typing import Callable

import httpx

from .config import Provider

_STATUS_HINTS = {
    400: ("Request rejected. Check the model and request options. "
          "For edit, choose a model supporting OpenAI-compatible tool calling."),
    401: "Check the provider API key.",
    403: "Check account permissions and model access.",
    404: "Check the model ID and provider base URL.",
    429: "Rate limit or quota reached. Retry later or choose another model.",
}


class ProviderError(RuntimeError):
    """Safe-to-display transport/protocol error (never contains response bodies)."""


class ModelClient:
    def __init__(self, provider: Provider, client: httpx.Client):
        self.provider = provider
        self.client = client

    def _status_error(self, exc: httpx.HTTPStatusError) -> ProviderError:
        code = exc.response.status_code
        return ProviderError(f"{self.provider.name}: HTTP {code}. " + _STATUS_HINTS.get(code, "Request failed."))

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self.client.request(
                method, self.provider.base_url + path,
                headers=self.provider.headers(), follow_redirects=False, **kwargs,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._status_error(exc) from None
        except httpx.RequestError:
            raise ProviderError(f"Cannot reach {self.provider.name}; check the server and network.") from None
        try:
            data = response.json()
        except ValueError:
            raise ProviderError("Provider returned invalid JSON.") from None
        if not isinstance(data, dict) or data.get("error"):
            raise ProviderError("Provider returned an error or an invalid response.")
        return data

    def models(self) -> list[str]:
        data = self._request("GET", "/models").get("data")
        if not isinstance(data, list):
            raise ProviderError("Provider did not return a model list.")
        return sorted({item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)})

    def complete(self, model: str, system: str, prompt: str, on_token: Callable[[str], None] | None = None) -> str:
        return self._complete_messages(model, [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ], on_token)

    def chat(self, model: str, system: str, history: list[tuple[str, str]],
             on_token: Callable[[str], None] | None = None) -> str:
        messages = [{"role": "system", "content": system}]
        messages += [{"role": role, "content": content} for role, content in history]
        return self._complete_messages(model, messages, on_token)

    def _complete_messages(self, model: str, messages: list[dict[str, str]],
                            on_token: Callable[[str], None] | None = None) -> str:
        if on_token is not None:
            return self._stream_complete_messages(model, messages, on_token)
        data = self._request("POST", "/chat/completions", json={
            "model": model,
            "messages": messages,
            "stream": False,
        })
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError("Provider response contains no assistant message.") from None
        if choice.get("finish_reason") == "length":
            raise ProviderError("Model output was truncated; reduce the task size or adjust server output limits.")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("Provider returned no text. Choose a text-generation model.")
        return content

    def _stream_complete_messages(self, model: str, messages: list[dict[str, str]],
                                   on_token: Callable[[str], None]) -> str:
        """Stream a completion, calling on_token for each text fragment as it arrives.

        Returns the full accumulated text, so callers (chat_loop, run) can treat a
        streamed reply exactly like a blocking one for history/context purposes.
        """
        payload = {"model": model, "messages": messages, "stream": True}
        pieces: list[str] = []
        finish_reason: str | None = None
        try:
            with self.client.stream(
                "POST", self.provider.base_url + "/chat/completions",
                headers=self.provider.headers(), json=payload, follow_redirects=False,
            ) as response:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    exc.response.read()
                    raise self._status_error(exc) from None
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") if isinstance(chunk, dict) else None
                    if not choices:
                        continue
                    choice = choices[0]
                    piece = (choice.get("delta") or {}).get("content")
                    if isinstance(piece, str) and piece:
                        pieces.append(piece)
                        on_token(piece)
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
        except httpx.RequestError:
            raise ProviderError(f"Cannot reach {self.provider.name}; check the server and network.") from None
        content = "".join(pieces)
        if finish_reason == "length":
            raise ProviderError("Model output was truncated; reduce the task size or adjust server output limits.")
        if not content.strip():
            raise ProviderError("Provider returned no text, or does not support streaming — try --no-stream.")
        return content

    def tool_turn(self, model: str, messages: list[dict], tools: list[dict]) -> dict:
        """Validate a complete tool-call envelope before any local tool runs."""
        data = self._request("POST", "/chat/completions", json={
            "model": model, "messages": messages, "tools": tools,
            "tool_choice": "auto", "stream": False,
        })
        try:
            choice = data["choices"][0]
            message = choice["message"]
            if not isinstance(message, dict) or choice.get("finish_reason") not in {"stop", "tool_calls"}:
                raise ValueError
            content = message.get("content")
            calls = message.get("tool_calls", [])
            if content is not None and not isinstance(content, str):
                raise ValueError
            if not isinstance(calls, list) or len(calls) > 8:
                raise ValueError
            clean, ids = [], set()
            for call in calls:
                function = call["function"]
                call_id = call["id"]
                name, arguments = function["name"], function["arguments"]
                if (call.get("type") != "function" or not isinstance(call_id, str)
                        or not call_id or call_id in ids or len(call_id) > 200
                        or not isinstance(name, str) or len(name) > 100
                        or not isinstance(arguments, str) or len(arguments) > 200000):
                    raise ValueError
                ids.add(call_id)
                clean.append({"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}})
            if not clean and (not content or not content.strip()):
                raise ValueError
            if content and len(content) > 120000:
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise ProviderError("Invalid or incomplete tool response. Choose a model supporting OpenAI-compatible tool calling.") from None
        result = {"role": "assistant", "content": content}
        if clean:
            result["tool_calls"] = clean
        return result

