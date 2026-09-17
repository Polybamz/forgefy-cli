"""End-to-end CLI entry-point test for explicitly approved file creation."""
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


class CreateCliTests(unittest.TestCase):
    def test_create_file_through_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requests = []

            def respond(request):
                body = json.loads(request.content)
                requests.append(body)
                if len(requests) == 1:
                    self.assertIn('create_file', [tool['function']['name'] for tool in body['tools']])
                    message = {'content': None, 'tool_calls': [{
                        'id': 'create1', 'type': 'function', 'function': {
                            'name': 'create_file', 'arguments': json.dumps({
                                'path': 'hello.py', 'content': 'print("hello")\n',
                            }),
                        },
                    }]}
                    finish = 'tool_calls'
                else:
                    self.assertEqual(json.loads(body['messages'][-1]['content'])['status'], 'created')
                    message = {'content': 'Created hello.py. Tests not run.'}
                    finish = 'stop'
                return httpx.Response(200, json={'choices': [{'finish_reason': finish, 'message': message}]})

            client = httpx.Client(transport=httpx.MockTransport(respond))
            output, errors = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {'FORGEFY_CONFIG': str(root / 'config.toml')}), \
                    patch('forgefy_cli.cli.httpx.Client', return_value=client), \
                    patch('sys.stdin.isatty', return_value=True), \
                    patch('builtins.input', return_value='yes') as approval, \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = main(['edit', 'Create a hello program', '--provider', 'ollama', '--model', 'mock',
                             '--workspace', str(root), '--create', 'hello.py'])
            self.assertEqual(code, 0, errors.getvalue())
            self.assertEqual((root / 'hello.py').read_bytes(), b'print("hello")\n')
            self.assertEqual(len(requests), 2)
            approval.assert_called_once()
            self.assertIn('+print("hello")', errors.getvalue())
            self.assertIn('Files actually changed: hello.py', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
