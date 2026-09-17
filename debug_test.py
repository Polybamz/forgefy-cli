"""Debug script for test_create.py failure"""
import json
import os
import sys
from pathlib import Path
import tempfile
import contextlib
import io
from unittest.mock import patch
import httpx

sys.path.insert(0, r'C:\Users\USER\Desktop\polycarp\forgefy-cli')
from forgefy_cli.cli import main

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        print('=== Turn', len(requests), '===')
        for i, msg in enumerate(body['messages']):
            content = msg.get('content')
            if content:
                try:
                    parsed = json.loads(content)
                    print(f'  [{i}] role={msg.get("role")} parsed_content={parsed}')
                except (json.JSONDecodeError, TypeError):
                    print(f'  [{i}] role={msg.get("role")} content={repr(content)[:100]}')
            else:
                print(f'  [{i}] role={msg.get("role")} content=None')
        if len(requests) == 1:
            message = {'content': None, 'tool_calls': [{
                'id': 'create1', 'type': 'function', 'function': {
                    'name': 'create_file',
                    'arguments': json.dumps({
                        'path': 'hello.py',
                        'content': 'print("hello")' + '\n',
                    }),
                },
            }]}
            finish = 'tool_calls'
        else:
            message = {'content': 'Created hello.py. Tests not run.'}
            finish = 'stop'
        return httpx.Response(200, json={'choices': [{'finish_reason': finish, 'message': message}]})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    output, errors = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, {'FORGEFY_CONFIG': str(root / 'config.toml')}), \
            patch('forgefy_cli.cli.httpx.Client', return_value=client), \
            patch('sys.stdin.isatty', return_value=True), \
            patch('builtins.input', return_value='yes'), \
            contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        code = main(['edit', 'Create a hello program', '--provider', 'ollama', '--model', 'mock',
                     '--workspace', str(root), '--create', 'hello.py'])
    print('Exit code:', code)
    print('Final requests:', len(requests))
    if len(requests) >= 2:
        last_msg = requests[1]['messages'][-1]
        print('Last message on turn 2:', repr(last_msg))
        if 'content' in last_msg:
            print('Content type:', type(last_msg['content']))
            print('Content:', repr(last_msg['content'])[:300])