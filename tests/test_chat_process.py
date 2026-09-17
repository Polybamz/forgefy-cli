"""Process-level chat checks against a loopback-only OpenAI-compatible server."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ChatProcessTests(unittest.TestCase):
    def run_session(self, lines, responses):
        executable = Path(sys.executable).parent / ('forgefy.exe' if os.name == 'nt' else 'forgefy')
        if not executable.is_file():
            self.skipTest('Install forgefy-cli into the test interpreter environment first.')
        requests = []
        pending = iter(responses)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, body))
                status, payload = next(pending, (500, {'error': 'Unexpected extra request'}))
                data = json.dumps(payload).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with tempfile.TemporaryDirectory() as directory:
                    config = Path(directory) / 'config.toml'
                    config.write_text(
                        'default_provider = "mock"\ndefault_model = "test-model"\n'
                        '[providers.mock]\n'
                        f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n',
                        encoding='utf-8',
                    )
                    env = dict(os.environ, FORGEFY_CONFIG=str(config), PYTHONIOENCODING='utf-8',
                               NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
                    result = subprocess.run(
                        [str(executable), 'chat'], input='\n'.join(lines) + '\n',
                        capture_output=True, text=True, encoding='utf-8', timeout=15,
                        cwd=directory, env=env,
                    )
            finally:
                server.shutdown()
                thread.join(timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(len(requests), len(responses))
        for path, body in requests:
            self.assertEqual(path, '/v1/chat/completions')
            self.assertEqual(body['model'], 'test-model')
            self.assertIs(body['stream'], False)
            self.assertEqual(body['messages'][0]['role'], 'system')
        return result, [body['messages'][1:] for _, body in requests]

    @staticmethod
    def answer(text):
        return 200, {'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]}

    def test_installed_chat_history_reset_and_help(self):
        result, messages = self.run_session(
            ['/help', 'first', 'second', '/new', 'fresh', '/exit'],
            [self.answer('reply one'), self.answer('reply two'), self.answer('fresh reply')],
        )
        self.assertEqual(messages, [
            [{'role': 'user', 'content': 'first'}],
            [{'role': 'user', 'content': 'first'}, {'role': 'assistant', 'content': 'reply one'},
             {'role': 'user', 'content': 'second'}],
            [{'role': 'user', 'content': 'fresh'}],
        ])
        for text in ('Commands:', 'Context cleared.', 'reply one', 'reply two', 'fresh reply'):
            self.assertIn(text, result.stdout)
        self.assertIn('mock / test-model', result.stderr)

    def test_http_error_preserves_history_and_hides_body(self):
        result, messages = self.run_session(
            ['first', 'failed', 'retry', '/quit'],
            [self.answer('saved'), (429, {'error': 'private-provider-detail'}), self.answer('recovered')],
        )
        self.assertEqual(messages[2], [
            {'role': 'user', 'content': 'first'}, {'role': 'assistant', 'content': 'saved'},
            {'role': 'user', 'content': 'retry'},
        ])
        self.assertIn('HTTP 429', result.stdout)
        self.assertIn('recovered', result.stdout)
        self.assertNotIn('private-provider-detail', result.stdout + result.stderr)

    def test_bad_request_explains_tool_compatibility_without_body(self):
        result, _ = self.run_session(
            ['hello', '/exit'], [(400, {'error': 'private-provider-detail'})],
        )
        self.assertIn('HTTP 400', result.stdout)
        self.assertIn('tool calling', result.stdout)
        self.assertNotIn('private-provider-detail', result.stdout + result.stderr)

    def test_invalid_response_recovery_and_eof(self):
        result, messages = self.run_session(
            ['first', 'retry'], [(200, {'choices': []}), self.answer('valid reply')],
        )
        self.assertEqual(messages[1], [{'role': 'user', 'content': 'retry'}])
        self.assertIn('no assistant message', result.stdout)
        self.assertIn('valid reply', result.stdout)


if __name__ == '__main__':
    unittest.main()
