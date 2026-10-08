"""The Cline-style CLI surface: bare prompt, -p, --json, --auto-approve and friends."""
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
from forgefy_cli.history import load_messages, save_messages, save_session


class CliInterfaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, {
            'FORGEFY_CONFIG': str(self.root / 'config.toml'),
            'FORGEFY_HISTORY_DIR': str(self.root / 'history'),
            'FORGEFY_CREDENTIALS': str(self.root / 'credentials.json'),
        })
        env.start()
        self.addCleanup(env.stop)

    def invoke(self, args, client=None, answer='yes', isatty=True):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            stack.enter_context(patch('sys.stdin.isatty', return_value=isatty))
            if client is not None:
                stack.enter_context(patch('forgefy_cli.cli.httpx.Client', return_value=client))
            if answer is not None:
                stack.enter_context(patch('builtins.input', return_value=answer))
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def agent_client(self, requests, tool_call=None, text='done.'):
        """Answer turn 1 with `tool_call` if given, then always finish with `text`."""
        def respond(request):
            requests.append(json.loads(request.content))
            if tool_call is not None and len(requests) == 1:
                return httpx.Response(200, json={'choices': [{
                    'finish_reason': 'tool_calls',
                    'message': {'content': None, 'tool_calls': [tool_call]}}]})
            return httpx.Response(200, json={'choices': [{
                'finish_reason': 'stop', 'message': {'content': text}}]})
        return httpx.Client(transport=httpx.MockTransport(respond))

    @staticmethod
    def call(name, **arguments):
        return {'id': 'call1', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(arguments)}}

    def test_bare_prompt_runs_the_agent_with_workspace_tools(self):
        requests = []
        (self.root / 'app.py').write_text('x = 1\n', encoding='utf-8')
        code, out, err = self.invoke(['Explain app.py', '-c', str(self.root), '-m', 'mock'], client=self.agent_client(requests))
        self.assertEqual(code, 0, err)
        names = [tool['function']['name'] for tool in requests[0]['tools']]
        for expected in ('read_file', 'list_files', 'search_files', 'replace_text', 'create_file'):
            self.assertIn(expected, names)
        self.assertEqual(out.strip(), 'done.')
        self.assertIn('Files actually changed: none', err)

    def test_workspace_scope_creates_a_new_file_after_approval(self):
        (self.root / 'app.py').write_text('x = 1\n', encoding='utf-8')
        requests = []
        client = self.agent_client(requests, tool_call=self.call('create_file', path='new.py', content='print(1)\n'))
        code, _, err = self.invoke(['Add new.py', '-c', str(self.root), '-m', 'mock'], client=client)
        self.assertEqual(code, 0, err)
        self.assertEqual((self.root / 'new.py').read_text(encoding='utf-8'), 'print(1)\n')
        self.assertIn('Files actually changed: new.py', err)

    def test_plan_mode_offers_read_only_tools_and_refuses_a_write(self):
        requests = []
        client = self.agent_client(requests, tool_call=self.call('create_file', path='nope.py', content='x'))
        code, _, err = self.invoke(['-p', 'Plan a change', '-c', str(self.root), '-m', 'mock'], client=client)
        self.assertEqual(code, 0, err)
        names = sorted(tool['function']['name'] for tool in requests[0]['tools'])
        self.assertEqual(names, ['list_files', 'read_file', 'search_files'])
        self.assertFalse((self.root / 'nope.py').exists())
        self.assertIn('not available in this mode', requests[1]['messages'][-1]['content'])
        self.assertIn('Plan mode', err)

    def test_json_mode_emits_only_json_lines(self):
        code, out, err = self.invoke(['--json', 'Explain', '-c', str(self.root), '-m', 'mock'], client=self.agent_client([]))
        self.assertEqual(code, 0, err)
        lines = [json.loads(line) for line in out.splitlines()]
        self.assertTrue(lines)
        for line in lines:
            self.assertIn(line['type'], {'say', 'ask'})
            self.assertIsInstance(line['ts'], int)
        replies = [line for line in lines if line.get('say') == 'text']
        self.assertEqual(replies[-1]['text'], 'done.')
        self.assertEqual(err, '')

    def test_auto_approve_applies_a_change_without_asking(self):
        requests = []
        client = self.agent_client(requests, tool_call=self.call('create_file', path='auto.py', content='print(2)\n'))
        code, _, err = self.invoke(['Add auto.py', '-c', str(self.root), '-m', 'mock', '--auto-approve', 'true'],
                                   client=client, answer=None, isatty=False)
        self.assertEqual(code, 0, err)
        # input() is not patched and stdin is not a tty here, so the change could
        # only have been applied by --auto-approve short-circuiting the prompt.
        self.assertEqual((self.root / 'auto.py').read_text(encoding='utf-8'), 'print(2)\n')
        self.assertIn('auto-approving', err)

    def test_declined_change_leaves_the_workspace_alone(self):
        requests = []
        client = self.agent_client(requests, tool_call=self.call('create_file', path='no.py', content='x'))
        code, _, err = self.invoke(['Add no.py', '-c', str(self.root), '-m', 'mock'], client=client, answer='no')
        self.assertEqual(code, 0, err)
        self.assertFalse((self.root / 'no.py').exists())
        self.assertIn('Files actually changed: none', err)

    def test_config_and_data_dir_redirect_state(self):
        code, out, _ = self.invoke(['--config', str(self.root / 'cfg'), 'config'])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), str(self.root / 'cfg' / 'config.toml'))
        code, out, _ = self.invoke(['config', '--config', str(self.root / 'direct.toml')])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), str(self.root / 'direct.toml'))
        code, out, _ = self.invoke(['--data-dir', str(self.root / 'state'), 'config'])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), str(self.root / 'state' / 'config.toml'))

    def test_version_command(self):
        code, out, _ = self.invoke(['version'])
        self.assertEqual(code, 0)
        self.assertIn('Forgefy CLI', out)

    def test_auth_checks_a_provider_key_without_network(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('OPENAI_API_KEY', None)
            code, _, err = self.invoke(['auth', 'openai'])
        self.assertEqual(code, 1)
        self.assertIn('OPENAI_API_KEY', err)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}):
            code, _, err = self.invoke(['auth', 'openai'])
        self.assertEqual(code, 0)
        self.assertIn('OPENAI_API_KEY is set', err)

    def test_update_reports_a_newer_release(self):
        client = httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={'info': {'version': '999.0.0'}})))
        code, _, err = self.invoke(['update'], client=client)
        self.assertEqual(code, 0)
        self.assertIn('Update available', err)
        self.assertIn('999.0.0', err)

    def test_history_lists_shows_and_deletes_sessions(self):
        save_messages('demo', [{'role': 'user', 'content': 'hi there'},
                               {'role': 'assistant', 'content': 'hello'}])
        save_session('notes', [('user', 'remember this')])
        code, out, _ = self.invoke(['history', '--json'])
        self.assertEqual(code, 0)
        rows = {row['name']: row for row in (json.loads(line) for line in out.splitlines())}
        self.assertEqual(rows['demo']['kind'], 'agent')
        self.assertEqual(rows['demo']['messages'], 2)
        self.assertEqual(rows['notes']['kind'], 'chat')
        code, out, _ = self.invoke(['history', '--show', 'demo'])
        self.assertEqual(code, 0)
        self.assertIn('user> hi there', out)
        code, _, _ = self.invoke(['h', '--delete', 'demo'])
        self.assertEqual(code, 0)
        self.assertEqual(load_messages('demo'), [])
        self.assertEqual(self.invoke(['history', '--delete', 'demo'])[0], 1)

    def test_id_resumes_a_saved_agent_session(self):
        save_messages('alpha', [{'role': 'user', 'content': 'earlier request'},
                                {'role': 'assistant', 'content': 'earlier reply'}])
        requests = []
        code, _, err = self.invoke(['follow up', '--id', 'alpha', '-c', str(self.root), '-m', 'mock', '-p'],
                                   client=self.agent_client(requests))
        self.assertEqual(code, 0, err)
        self.assertIn("Resumed agent session 'alpha'", err)
        contents = [message.get('content') for message in requests[0]['messages']]
        self.assertIn('earlier request', contents)

    def test_history_with_no_sessions_is_not_an_error(self):
        code, _, err = self.invoke(['history'])
        self.assertEqual(code, 0)
        self.assertIn('No saved sessions', err)


if __name__ == '__main__':
    unittest.main()
