"""Direct CLI regression tests; synthetic traces do not prove native skill use."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).with_name('omc_peer_trace.py')


def call(name, ident, **args):
    return {'type': 'assistant', 'message': {'content': [
        {'type': 'tool_use', 'id': ident, 'name': name, 'input': args}]}}


def result(ident, error=False):
    return {'type': 'user', 'message': {'content': [
        {'type': 'tool_result', 'tool_use_id': ident, 'is_error': error, 'content': 'ok'}]}}


def trace(*rows):
    return [{'type': 'system', 'subtype': 'init'}, *rows,
            {'type': 'result', 'result': '모든 검증 통과라고 주장'}]


class PeerTraceTests(unittest.TestCase):
    def run_cli(self, rows, expected_exit=0, malformed=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / 'events.jsonl'
            raw = malformed if malformed is not None else '\n'.join(json.dumps(r) for r in rows)
            events.write_text(raw)
            before = hashlib.sha256(events.read_bytes()).hexdigest()
            proc = subprocess.run([sys.executable, str(CLI), '--events', str(events),
                                   '--root', str(root), '--work-path', 'src/',
                                   '--provenance', 'synthetic'], capture_output=True, text=True)
            self.assertEqual(proc.returncode, expected_exit, proc.stderr)
            self.assertEqual(before, hashlib.sha256(events.read_bytes()).hexdigest())
            return json.loads(proc.stdout)

    def entry(self):
        return [call('Skill', 's', skill='oh-my-claudecode:execute'), result('s')]

    def test_normal_surface_and_final_claim_is_not_verification(self):
        data = self.run_cli(trace(*self.entry(), call('Edit', 'e', file_path='src/a.ts'), result('e')))
        self.assertEqual(data['entry']['status'], 'passed')
        self.assertEqual(data['write_order']['status'], 'passed')
        self.assertEqual(data['provenance'], 'synthetic')
        self.assertEqual(len(data['checks']), 5)
        self.assertTrue(all(v['status'] == 'unknown' for v in data['checks'].values()))
        self.assertIn('합성 기록', data['report'])
        self.assertIn('필수 검증: 확인 불가', data['report'])
        self.assertFalse(data['comparison_eligible'])

    def test_missing_entry_surface(self):
        data = self.run_cli(trace(call('Write', 'e', file_path='src/a.ts'), result('e')))
        self.assertEqual(data['entry']['status'], 'failed')
        self.assertEqual(data['write_order']['status'], 'failed')
        self.assertIn('진입: 미충족', data['report'])

    def test_failed_skill_result(self):
        data = self.run_cli(trace(call('Skill', 's', skill='oh-my-claudecode:execute'), result('s', True)))
        self.assertEqual(data['entry']['status'], 'failed')

    def test_hook_prelude_before_init_is_valid(self):
        data = self.run_cli([{'type': 'system', 'subtype': 'hook_started'},
                             *trace(call('Read', 'r', file_path='src/a.ts'), result('r'))])
        self.assertEqual(data['entry']['status'], 'failed')

    def test_calls_before_init_are_not_complete(self):
        data = self.run_cli([call('Read', 'r', file_path='a'), result('r'),
                             *trace(*self.entry())], expected_exit=2)
        self.assertEqual(data['entry']['status'], 'unknown')

    def test_edit_before_return(self):
        data = self.run_cli(trace(call('Skill', 's', skill='oh-my-claudecode:execute'),
                                  call('Edit', 'e', file_path='src/a.ts'), result('e'), result('s')))
        self.assertEqual(data['write_order']['status'], 'failed')

    def test_bash_write_before_entry(self):
        data = self.run_cli(trace(call('Bash', 'b', command='touch src/a.ts'), result('b'), *self.entry()))
        self.assertEqual(data['write_order']['status'], 'failed')

    def test_redirect_after_entry(self):
        data = self.run_cli(trace(*self.entry(), call('Bash', 'b', command='printf hello > src/a.ts'), result('b')))
        self.assertEqual(data['write_order']['status'], 'passed')

    def test_unknown_script_before_entry_surface(self):
        data = self.run_cli(trace(call('Bash', 'b', command='python3 patch.py'), result('b'),
                                  *self.entry(), call('Edit', 'e', file_path='src/a.ts'), result('e')))
        self.assertEqual(data['write_order']['status'], 'unknown')
        self.assertIn('수정 순서: 확인 불가', data['report'])

    def test_artifact_path_without_historical_identity_is_unknown(self):
        data = self.run_cli(trace(call('Write', 'a', file_path='.omc/note.json'), result('a'),
                                  *self.entry(), call('Write', 'e', file_path='src/a.ts'), result('e')))
        self.assertEqual(data['write_order']['status'], 'unknown')

    def test_truncated_and_malformed(self):
        data = self.run_cli(trace(*self.entry())[:-1], expected_exit=2)
        self.assertEqual(data['entry']['status'], 'unknown')
        self.assertEqual(data['write_order']['status'], 'unknown')
        data = self.run_cli([], expected_exit=2, malformed='{broken')
        self.assertEqual(data['entry']['status'], 'unknown')

    def test_duplicate_or_unmatched_ids_are_unknown(self):
        data = self.run_cli(trace(*self.entry(), result('s')), expected_exit=2)
        self.assertEqual(data['entry']['status'], 'unknown')

    def test_malformed_tool_shape_is_reported_unknown(self):
        broken_call = call([], 'x')
        data = self.run_cli(trace(broken_call, result('x')), expected_exit=2)
        self.assertEqual(data['entry']['status'], 'unknown')
        broken_return = result('s')
        del broken_return['message']['content'][0]['content']
        data = self.run_cli(trace(call('Skill', 's', skill='oh-my-claudecode:execute'), broken_return), expected_exit=2)
        self.assertEqual(data['entry']['status'], 'unknown')
        data = self.run_cli(trace(result('missing'), *self.entry()), expected_exit=2)
        self.assertEqual(data['entry']['status'], 'unknown')

    def test_dynamic_shell_and_failed_edit_are_unknown(self):
        for command in ['echo $(python patch.py) > src/a.ts', 'cd src && touch a.ts', 'git show HEAD:src/a.ts',
                        'sed -i x src/a.ts', 'env python patch.py']:
            data = self.run_cli(trace(call('Bash', 'b', command=command), result('b'), *self.entry()))
            self.assertEqual(data['write_order']['status'], 'unknown', command)
        data = self.run_cli(trace(*self.entry(), call('Edit', 'e', file_path='src/a.ts'), result('e', True)))
        self.assertEqual(data['write_order']['status'], 'unknown')

    def test_same_event_return_and_edit_not_ordered(self):
        row = result('s')
        row['message']['content'].extend(call('Edit', 'e', file_path='src/a.ts')['message']['content'])
        data = self.run_cli(trace(call('Skill', 's', skill='oh-my-claudecode:execute'), row, result('e')), expected_exit=2)
        self.assertEqual(data['entry']['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
