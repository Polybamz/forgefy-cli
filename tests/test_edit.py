"""Mocked CLI editing integration and local permission regression tests."""
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
from forgefy_cli.file_tools import FileTools


class EditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'main.py'
        self.path.write_bytes(b'x = 1\n')

    def test_cli_read_approve_replace(self):
        requests = []
        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            turn = len(requests)
            if turn == 3:
                self.assertIn('applied', body['messages'][-1]['content'])
                return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': 'Updated; tests not run.'}}]})
            name = 'read_file' if turn == 1 else 'replace_text'
            args = {'path': 'main.py'}
            if turn == 2:
                self.assertIn('x = 1', body['messages'][-1]['content'])
                args.update(old_text='x = 1', new_text='x = 2')
            return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
                'content': None, 'tool_calls': [{'id': f'call{turn}', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(args)}}]}}]})
        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch.dict(os.environ, {'FORGEFY_CONFIG': str(self.root / 'config.toml')}), \
                patch('forgefy_cli.cli.httpx.Client', return_value=client), \
                patch('sys.stdin.isatty', return_value=True), \
                patch('builtins.input', return_value='yes') as approval, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(['edit', 'Change x to 2', '--provider', 'ollama', '--model', 'mock',
                         '--workspace', str(self.root), '--file', 'main.py'])
        self.assertEqual(code, 0)
        self.assertEqual(self.path.read_bytes(), b'x = 2\n')
        approval.assert_called_once()
        self.assertEqual(len(requests), 3)

    def tool(self, approve=lambda diff: True):
        return FileTools(self.root, ['main.py'], approve)

    def replace(self, tools):
        return json.loads(tools.execute('replace_text', json.dumps({
            'path': 'main.py', 'old_text': 'x = 1', 'new_text': 'x = 2'})))

    def read(self, tools):
        return tools.execute('read_file', '{"path":"main.py"}')

    def test_unread_file_rejected(self):
        self.assertIn('read_file', self.replace(self.tool())['error'])
        self.assertEqual(self.path.read_bytes(), b'x = 1\n')

    def test_denied_approval(self):
        tools = self.tool(lambda diff: False)
        self.read(tools)
        self.assertIn('declined', self.replace(tools)['error'])
        self.assertEqual(self.path.read_bytes(), b'x = 1\n')

    def test_stale_file(self):
        tools = self.tool()
        self.read(tools)
        self.path.write_bytes(b'x = 3\n')
        self.assertIn('changed', self.replace(tools)['error'])
        self.assertEqual(self.path.read_bytes(), b'x = 3\n')

    def test_change_during_approval(self):
        def approval(diff):
            self.path.write_bytes(b'x = 4\n')
            return True
        tools = self.tool(approval)
        self.read(tools)
        self.assertIn('changed', self.replace(tools)['error'])
        self.assertEqual(self.path.read_bytes(), b'x = 4\n')

    def test_unlisted_file(self):
        (self.root / 'other.py').write_text('private', encoding='utf-8')
        self.assertIn('error', json.loads(self.tool().execute('read_file', '{"path":"other.py"}')))

    def test_traversal_and_secrets(self):
        for name in ('../main.py', '.env', 'credentials.json', 'main.py:stream'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                FileTools(self.root, [name], lambda diff: True)


if __name__ == '__main__':
    unittest.main()
