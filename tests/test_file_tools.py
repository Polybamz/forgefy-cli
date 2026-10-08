"""Workspace-scope and read-only FileTools tests. No network, shell, or new deps."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from forgefy_cli.file_tools import FileTools, TOOLS


class FileToolsScopeTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def tool(self, files=None, create=None, approve=lambda diff: True, scope='workspace'):
        return FileTools(self.root, files or [], approve, create, scope=scope)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
        return path

    def read(self, tools, path):
        return json.loads(tools.execute('read_file', json.dumps({'path': path})))

    def test_tool_schema_order_and_names(self):
        self.assertEqual([tool['function']['name'] for tool in TOOLS],
                         ['read_file', 'list_files', 'search_files', 'replace_text', 'create_file'])

    def test_workspace_scope_reads_unlisted_file(self):
        self.write('main.py', 'x = 1\n')
        tools = self.tool()
        self.assertEqual(tools.allowed, set())
        self.assertEqual(tools.creatable, [])
        self.assertEqual(tools.root, self.root.resolve())
        self.assertEqual(self.read(tools, 'main.py'), {'path': 'main.py', 'content': 'x = 1\n'})

    def test_workspace_scope_constructor_rejects_disallowed_paths(self):
        names = ('../main.py', '.env', 'credentials.json', str(self.root / 'main.py'))
        for name in names:
            for keyword in ('files', 'create'):
                with self.subTest(name=name, keyword=keyword), self.assertRaises(ValueError):
                    FileTools(self.root, [name] if keyword == 'files' else [], lambda diff: True,
                              [name] if keyword == 'create' else [], scope='workspace')

    def test_workspace_scope_execute_rejects_disallowed_paths(self):
        self.write('main.py', 'x = 1\n')
        tools = self.tool()
        for name in ('../main.py', '.env', 'credentials.json', str(self.root / 'main.py')):
            with self.subTest(name=name):
                self.assertIn('error', self.read(tools, name))
        for name in (str(self.root), '../', '..'):
            with self.subTest(listing=name):
                self.assertIn('error', json.loads(tools.execute('list_files', json.dumps({'path': name}))))
        self.assertIn('error', json.loads(tools.execute('search_files', json.dumps({
            'pattern': 'x', 'path': str(self.root)}))))

    def test_workspace_scope_narrowed_file_list_rejects_unlisted_path(self):
        self.write('main.py', 'x = 1\n')
        self.write('other.py', 'private\n')
        tools = self.tool(files=['main.py'])
        self.assertEqual(tools.allowed, {'main.py'})
        self.assertIn('error', self.read(tools, 'other.py'))
        self.assertEqual(self.read(tools, 'main.py')['content'], 'x = 1\n')
        result = json.loads(tools.execute('create_file', json.dumps({'path': 'new.py', 'content': 'pass\n'})))
        self.assertIn('error', result)
        self.assertFalse((self.root / 'new.py').exists())

    def test_workspace_scope_narrowed_create_list_allows_only_that_file(self):
        self.write('main.py', 'x = 1\n')
        tools = self.tool(create=['new.py'])
        self.assertEqual(tools.creatable, ['new.py'])
        self.assertIn('error', self.read(tools, 'main.py'))
        result = json.loads(tools.execute('create_file', json.dumps({'path': 'new.py', 'content': 'pass\n'})))
        self.assertEqual(result, {'status': 'created', 'path': 'new.py'})
        self.assertEqual((self.root / 'new.py').read_bytes(), b'pass\n')

    def test_workspace_scope_create_keeps_overwrite_and_parent_rules(self):
        self.write('existing.py', 'old\n')
        tools = self.tool()
        overwrite = json.loads(tools.execute('create_file', json.dumps({
            'path': 'existing.py', 'content': 'new\n'})))
        self.assertIn('error', overwrite)
        self.assertEqual((self.root / 'existing.py').read_bytes(), b'old\n')
        orphan = json.loads(tools.execute('create_file', json.dumps({
            'path': 'missing/new.py', 'content': 'x\n'})))
        self.assertIn('error', orphan)
        declined = self.tool(approve=lambda diff: False)
        result = json.loads(declined.execute('create_file', json.dumps({
            'path': 'new.py', 'content': 'x\n'})))
        self.assertIn('declined', result['error'])
        self.assertFalse((self.root / 'new.py').exists())

    def test_workspace_scope_replace_requires_read_then_approval(self):
        path = self.write('main.py', 'x = 1\n')
        approvals = []
        tools = self.tool(approve=lambda diff: approvals.append(diff) or True)
        unread = json.loads(tools.execute('replace_text', json.dumps({
            'path': 'main.py', 'old_text': 'x = 1', 'new_text': 'x = 2'})))
        self.assertIn('read_file', unread['error'])
        self.read(tools, 'main.py')
        result = json.loads(tools.execute('replace_text', json.dumps({
            'path': 'main.py', 'old_text': 'x = 1', 'new_text': 'x = 2'})))
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(path.read_bytes(), b'x = 2\n')
        self.assertEqual(tools.summary(), ['main.py'])
        self.assertEqual(len(approvals), 1)
        self.assertIn('+x = 2', approvals[0])

    def test_bad_scope_and_empty_explicit_are_rejected(self):
        for scope in ('', 'Workspace', 'global', 'workspace '):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                FileTools(self.root, [], lambda diff: True, scope=scope)
        with self.assertRaises(ValueError):
            FileTools(self.root, [], lambda diff: True)

    def test_explicit_scope_unlisted_read_still_errors(self):
        self.write('main.py', 'x = 1\n')
        self.write('other.py', 'private\n')
        tools = FileTools(self.root, ['main.py'], lambda diff: True)
        result = self.read(tools, 'other.py')
        self.assertIn('error', result)
        self.assertIn('not explicitly allowed', result['error'])
        self.assertEqual(self.read(tools, 'main.py')['content'], 'x = 1\n')

    def test_execute_validates_schema_and_unknown_tools(self):
        tools = self.tool()
        self.assertIn('schema', json.loads(tools.execute('search_files', json.dumps({'pattern': 'x'})))['error'])
        self.assertIn('schema', json.loads(tools.execute('list_files', json.dumps({'path': '.', 'extra': 'x'})))['error'])
        self.assertIn('schema', json.loads(tools.execute('list_files', json.dumps({'path': 1})))['error'])
        unknown = json.loads(tools.execute('delete_file', json.dumps({'path': '.'})))
        self.assertIn('search_files', unknown['error'])

    def test_list_files_directories_first_and_filtered(self):
        self.write('alpha.py', 'abc')
        self.write('Zeta.txt', 'zz')
        self.write('pkg/inner.py', 'x')
        self.write('other/keep.txt', 'y')
        self.write('.hidden', 'nope')
        self.write('.git/config', 'nope')
        self.write('credentials.json', 'nope')
        self.write('id_rsa', 'nope')
        self.write('server.pem', 'nope')
        tools = self.tool()
        result = json.loads(tools.execute('list_files', json.dumps({'path': '.'})))
        self.assertEqual(result['path'], '.')
        self.assertFalse(result['truncated'])
        self.assertEqual([entry['name'] for entry in result['entries']],
                         ['other', 'pkg', 'alpha.py', 'Zeta.txt'])
        self.assertEqual([entry['type'] for entry in result['entries']],
                         ['dir', 'dir', 'file', 'file'])
        self.assertEqual(result['entries'][2]['size'], 3)
        sub = json.loads(tools.execute('list_files', json.dumps({'path': 'pkg'})))
        self.assertEqual(sub['path'], 'pkg')
        self.assertEqual([entry['name'] for entry in sub['entries']], ['inner.py'])

    def test_list_files_truncates_at_cap(self):
        for index in range(205):
            self.write(f'file{index:03d}.txt', 'x')
        self.write('few/one.txt', 'x')
        self.write('few/two.txt', 'x')
        tools = self.tool()
        result = json.loads(tools.execute('list_files', json.dumps({'path': '.'})))
        self.assertEqual(len(result['entries']), 200)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['entries'][0]['name'], 'few')
        small = json.loads(tools.execute('list_files', json.dumps({'path': 'few'})))
        self.assertEqual(len(small['entries']), 2)
        self.assertFalse(small['truncated'])

    def test_list_files_non_directory_returns_error(self):
        self.write('main.py', 'x')
        tools = self.tool()
        self.assertIn('error', json.loads(tools.execute('list_files', json.dumps({'path': 'main.py'}))))
        self.assertIn('error', json.loads(tools.execute('list_files', json.dumps({'path': 'missing'}))))

    def test_search_files_line_numbers_and_filters(self):
        self.write('a.py', 'first\nneedle here\nthird\n')
        self.write('pkg/b.py', 'needle\nplain\n')
        self.write('.env', 'needle\n')
        self.write('.git/config', 'needle\n')
        self.write('credentials.json', 'needle\n')
        self.write('id_rsa', 'needle\n')
        self.write('key.pem', 'needle\n')
        self.write('binary.bin', b'needle\x00tail')
        self.write('big.txt', 'needle\n' + 'x' * 32001)
        self.write('latin.txt', b'needle \xff\xfe')
        tools = self.tool()
        result = json.loads(tools.execute('search_files', json.dumps({'pattern': 'needle', 'path': '.'})))
        self.assertEqual(result['matches'], [
            {'path': 'a.py', 'line': 2, 'text': 'needle here'},
            {'path': 'pkg/b.py', 'line': 1, 'text': 'needle'},
        ])
        # binary/oversized/undecodable/blocked files are visited but never matched.
        self.assertEqual(result['scanned'], 5)
        self.assertFalse(result['truncated'])

    def test_search_files_invalid_regex_and_long_pattern_error(self):
        self.write('a.py', 'x\n')
        tools = self.tool()
        invalid = json.loads(tools.execute('search_files', json.dumps({'pattern': '[', 'path': '.'})))
        self.assertIn('error', invalid)
        long = json.loads(tools.execute('search_files', json.dumps({'pattern': 'a' * 201, 'path': '.'})))
        self.assertIn('error', long)

    def test_search_files_truncates_at_match_cap(self):
        self.write('many.txt', ''.join(f'needle {index}\n' for index in range(250)))
        tools = self.tool()
        result = json.loads(tools.execute('search_files', json.dumps({'pattern': 'needle', 'path': '.'})))
        self.assertEqual(len(result['matches']), 200)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['matches'][0], {'path': 'many.txt', 'line': 1, 'text': 'needle 0'})
        self.assertEqual(result['matches'][-1]['line'], 200)

    def test_search_files_truncates_match_text(self):
        self.write('long.txt', 'needle ' + 'y' * 400 + '\n')
        result = json.loads(self.tool().execute('search_files', json.dumps({'pattern': 'needle', 'path': '.'})))
        self.assertEqual(len(result['matches'][0]['text']), 240)

    def test_search_files_no_match_is_empty_not_error(self):
        self.write('a.py', 'nothing to see\n')
        result = json.loads(self.tool().execute('search_files', json.dumps({'pattern': 'zzz', 'path': '.'})))
        self.assertEqual(result['matches'], [])
        self.assertFalse(result['truncated'])
        self.assertIn('error', json.loads(self.tool().execute('search_files', json.dumps({
            'pattern': 'x', 'path': 'a.py'}))))

    def test_search_files_truncates_after_scan_cap(self):
        self.write('plain.txt', 'nothing here\n')
        for index in range(2000):
            self.write(f'scan{index:04d}.txt', 'no match at all\n')
        result = json.loads(self.tool().execute('search_files', json.dumps({
            'pattern': 'needle', 'path': '.'})))
        self.assertEqual(result['matches'], [])
        self.assertEqual(result['scanned'], 2000)
        self.assertTrue(result['truncated'])

    def test_workspace_scope_omits_and_rejects_symlinks(self):
        self.write('target.py', 'x = 1\n')
        self.write('pkg/inside.py', 'x = 1\n')
        try:
            os.symlink(self.root / 'target.py', self.root / 'link.py')
            os.symlink(self.root / 'pkg', self.root / 'linkdir', target_is_directory=True)
        except OSError:
            self.skipTest('symlink creation is not permitted on this platform')
        tools = self.tool()
        self.assertIn('error', self.read(tools, 'link.py'))
        listing = json.loads(tools.execute('list_files', json.dumps({'path': '.'})))
        names = [entry['name'] for entry in listing['entries']]
        self.assertNotIn('link.py', names)
        self.assertNotIn('linkdir', names)
        found = json.loads(tools.execute('search_files', json.dumps({'pattern': 'x', 'path': '.'})))
        self.assertEqual([match['path'] for match in found['matches']], ['target.py', 'pkg/inside.py'])

    def test_workspace_scope_rejects_hardlinked_file(self):
        self.write('target.py', 'x = 1\n')
        try:
            os.link(self.root / 'target.py', self.root / 'hard.py')
        except OSError:
            self.skipTest('hardlink creation is not permitted on this platform')
        tools = self.tool()
        self.assertIn('error', self.read(tools, 'hard.py'))
        # The original name now has st_nlink == 2 as well, so it is rejected too.
        self.assertIn('error', self.read(tools, 'target.py'))


if __name__ == '__main__':
    unittest.main()