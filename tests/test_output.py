"""Reporter contract: plain text streams stay pipeable; --json is one object per line."""
import io
import json
import unittest

from forgefy_cli.output import Reporter


class TextModeTests(unittest.TestCase):
    def reporter(self):
        self.out, self.err = io.StringIO(), io.StringIO()
        return Reporter(json_mode=False, stdout=self.out, stderr=self.err, clock=lambda: 1.5)

    def test_say_goes_to_stdout_and_others_to_stderr(self):
        reporter = self.reporter()
        reporter.say('reply')
        reporter.info('status')
        reporter.error('Forgefy: boom')
        reporter.ask('Proposed change')
        self.assertEqual(self.out.getvalue(), 'reply\n')
        self.assertEqual(self.err.getvalue(), 'status\nForgefy: boom\nProposed change\n')

    def test_stream_fragments_join_on_one_line(self):
        reporter = self.reporter()
        for chunk in ('Hel', 'lo', ' world'):
            reporter.stream(chunk)
        reporter.close_stream()
        self.assertEqual(self.out.getvalue(), 'Hello world\n')

    def test_say_after_stream_closes_the_open_line(self):
        reporter = self.reporter()
        reporter.stream('partial')
        reporter.say('reply')
        self.assertEqual(self.out.getvalue(), 'partial\nreply\n')

    def test_close_stream_without_fragments_writes_nothing(self):
        reporter = self.reporter()
        reporter.close_stream()
        self.assertEqual(self.out.getvalue(), '')

    def test_streams_are_resolved_lazily(self):
        reporter = Reporter(json_mode=False, clock=lambda: 0)
        self.out, self.err = io.StringIO(), io.StringIO()
        reporter._stdout, reporter._stderr = self.out, self.err
        reporter.say('x')
        self.assertEqual(self.out.getvalue(), 'x\n')


class JsonModeTests(unittest.TestCase):
    def reporter(self):
        self.out, self.err = io.StringIO(), io.StringIO()
        return Reporter(json_mode=True, stdout=self.out, stderr=self.err, clock=lambda: 2.0)

    def lines(self):
        return [json.loads(line) for line in self.out.getvalue().splitlines()]

    def test_every_message_is_one_json_object_per_line(self):
        reporter = self.reporter()
        reporter.say('reply')
        reporter.info('status')
        reporter.error('Forgefy: boom')
        reporter.ask('Proposed change')
        self.assertEqual(self.lines(), [
            {'type': 'say', 'say': 'text', 'text': 'reply', 'ts': 2000},
            {'type': 'say', 'say': 'info', 'text': 'status', 'ts': 2000},
            {'type': 'say', 'say': 'error', 'text': 'Forgefy: boom', 'ts': 2000},
            {'type': 'ask', 'ask': 'approval', 'text': 'Proposed change', 'ts': 2000},
        ])
        self.assertEqual(self.err.getvalue(), '', 'JSON mode must not write human text to stderr')

    def test_streamed_fragments_are_marked_partial(self):
        reporter = self.reporter()
        reporter.stream('a')
        reporter.stream('b')
        reporter.close_stream()
        self.assertEqual(self.lines(), [
            {'type': 'say', 'say': 'text', 'text': 'a', 'partial': True, 'ts': 2000},
            {'type': 'say', 'say': 'text', 'text': 'b', 'partial': True, 'ts': 2000},
        ])

    def test_every_line_is_parseable_even_with_embedded_newlines(self):
        reporter = self.reporter()
        reporter.say('line one\nline two')
        raw = self.out.getvalue()
        self.assertEqual(len(raw.splitlines()), 1)
        self.assertEqual(self.lines()[0]['text'], 'line one\nline two')


if __name__ == '__main__':
    unittest.main()
