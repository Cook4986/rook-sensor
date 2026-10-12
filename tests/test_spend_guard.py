import copy
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
from spend_guard import SpendGuard, SpendError, BudgetExhausted, initialize_ledger, read_history

POLICY = {'endpoint': 'https://example.invalid/v1', 'verified_at': 'test only',
          'source': 'synthetic policy: no API calls',
          'models': {'test-model': {'input_usd_per_million': '1',
                     'output_usd_per_million': '2', 'max_input_tokens': 100,
                     'max_output_tokens': 100}}}


class SpendTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.jsonl'
        self.path.write_text('{"delta_usd":18.821498,"note":"historical"}\n')
        self.initial = self.path.read_bytes()

    def guard(self, ceiling='50', policy=None):
        return SpendGuard(self.path, policy or POLICY, POLICY['endpoint'], ['test-model'], ceiling)

    def test_reservations_survive_restart_and_usage_never_refunds(self):
        with self.guard() as budget:
            key = budget.reserve('test-model')
            budget.observe(key, {'prompt_tokens': 1, 'completion_tokens': 1})
            budget.reserve('test-model')  # simulates interrupted request
        with self.guard() as budget:
            self.assertEqual(budget.total, Decimal('18.822098'))
        self.assertTrue(self.path.read_bytes().startswith(self.initial))

    def test_cap_rejects_before_sending_and_cannot_be_raised(self):
        with self.guard('18.821797') as budget:
            with self.assertRaises(BudgetExhausted):
                budget.reserve('test-model')
        self.assertEqual(self.path.read_bytes(), self.initial)
        for limit in ('51', 'nan', '-1', 'Infinity', '0'):
            with self.subTest(limit=limit), self.assertRaises(SpendError):
                self.guard(limit)

    def test_missing_damaged_or_empty_history_fails_closed(self):
        for content in ('', '{"delta_usd":1}', 'garbled\n', '{"delta_usd":-1}\n', '{"delta_usd":"NaN"}\n'):
            self.path.write_text(content)
            with self.subTest(content=content), self.assertRaises(SpendError):
                with self.guard(): pass
        self.path.unlink()
        with self.assertRaises(OSError):
            with self.guard(): pass
        self.assertFalse(self.path.exists())

    def test_bad_model_pricing_and_endpoint_fail_closed(self):
        for change in ({'input_usd_per_million': 0}, {'max_input_tokens': 0},
                       {'output_usd_per_million': 'NaN'}, {'max_output_tokens': True}):
            policy = copy.deepcopy(POLICY)
            policy['models']['test-model'].update(change)
            with self.assertRaises(SpendError): self.guard(policy=policy)
        with self.assertRaises(SpendError):
            SpendGuard(self.path, POLICY, 'https://another.invalid/v1', ['test-model'])
        with self.assertRaises(SpendError):
            SpendGuard(self.path, POLICY, POLICY['endpoint'], ['missing-model'])

    def test_out_of_bound_usage_blocks_future_runs(self):
        with self.guard() as budget:
            key = budget.reserve('test-model')
            with self.assertRaises(SpendError):
                budget.observe(key, {'prompt_tokens': 1000, 'completion_tokens': 1})
            with self.assertRaises(SpendError):
                budget.reserve('test-model')
        with self.assertRaises(SpendError):
            with self.guard(): pass
        self.assertEqual(read_history(self.path)[0], Decimal('18.822500'))

    def test_unknown_usage_keeps_full_reservation(self):
        with self.guard() as budget:
            for usage in (None, {}, {'prompt_tokens': 1}, {'prompt_tokens': -1, 'completion_tokens': 0}):
                budget.observe(budget.reserve('test-model'), usage)
            self.assertEqual(budget.total, Decimal('18.822698'))

    def test_second_process_cannot_acquire_paid_runner(self):
        script = '''import json,sys
from spend_guard import SpendGuard
try:
 with SpendGuard(sys.argv[1], json.loads(sys.argv[2]), 'https://example.invalid/v1', ['test-model']):
  sys.exit(9)
except BlockingIOError:
 sys.exit(0)
'''
        with self.guard():
            result = subprocess.run([sys.executable, '-c', 'import sys;sys.path.insert(0,sys.argv[3]);'+script,
                                     str(self.path), json.dumps(POLICY), str(APP)], timeout=10)
        self.assertEqual(result.returncode, 0)

    def test_crashed_process_retains_reservation_and_releases_lock(self):
        script = '''import json,sys,os
from spend_guard import SpendGuard
with SpendGuard(sys.argv[1], json.loads(sys.argv[2]), 'https://example.invalid/v1', ['test-model']) as b:
 b.reserve('test-model')
 os._exit(17)
'''
        result = subprocess.run([sys.executable, '-c', 'import sys;sys.path.insert(0,sys.argv[3]);'+script,
                                 str(self.path), json.dumps(POLICY), str(APP)], timeout=10)
        self.assertEqual(result.returncode, 17)
        with self.guard() as b:
            self.assertEqual(b.total, Decimal('18.821798'))

    def test_import_preserves_history_and_cannot_reset(self):
        dest = self.path.with_name('imported.jsonl')
        initialize_ledger(self.path, dest)
        self.assertEqual(dest.read_bytes(), self.initial)
        with self.assertRaises(FileExistsError): initialize_ledger(self.path, dest)
        with self.assertRaises(SpendError):
            initialize_ledger(self.path, self.path.parent / 'Dropbox/accounting.jsonl')


class RequestTest(unittest.TestCase):
    setUp = SpendTest.setUp
    guard = SpendTest.guard

    def test_default_cli_cannot_enable_paid_requests(self):
        import llm_autolabel as labeler
        with patch.object(sys, 'argv', ['llm_autolabel.py']), patch.object(labeler, 'run_pipeline') as run:
            labeler.main()
            self.assertIsNone(run.call_args.args[1])

    def test_paid_cli_fails_before_model_loading_without_policy(self):
        import llm_autolabel as labeler
        with patch.object(sys, 'argv', ['llm_autolabel.py', '--allow-paid']), patch.object(labeler, 'run_pipeline') as run:
            with self.assertRaises(SystemExit) as stopped:
                labeler.main()
            self.assertEqual(stopped.exception.code, 2)
            run.assert_not_called()
    def call(self, responses, budget):
        import httpx
        from llm_autolabel import _vlm_round_trip
        class Client:
            calls = 0
            def post(self, url, **kwargs):
                # Assert durable reservation precedes even the first network attempt.
                self.calls += 1
                records = read_history(budget.path)[1]
                assert records[-1]['event'] == 'reserve'
                item = responses.pop(0)
                if isinstance(item, Exception): raise item
                return httpx.Response(item[0], json=item[1], request=httpx.Request('POST', url))
        client = Client()
        with patch('llm_autolabel.time.sleep'):
            verdict, usage = _vlm_round_trip(client, b'synthetic image', 'test',
                                             lambda s: {'label': s} if s == 'ok' else None,
                                             'test-model', budget)
        return client.calls, verdict, usage

    def test_timeout_and_retry_both_reserve(self):
        import httpx
        body = {'choices': [{'message': {'content': 'ok'}}], 'usage': {'prompt_tokens': 3, 'completion_tokens': 2}}
        with self.guard() as b:
            calls, verdict, _ = self.call([httpx.ReadTimeout('timeout'), (200, body)], b)
            self.assertEqual(calls, 2)
            self.assertEqual(verdict, {'label': 'ok'})
            self.assertEqual(b.total, Decimal('18.822098'))

    def test_malformed_and_rate_limited_attempts_all_count(self):
        body = {'choices': [{'message': {'content': 'ok'}}], 'usage': {'prompt_tokens': 3, 'completion_tokens': 2}}
        with self.guard() as b:
            calls, verdict, usage = self.call([(429, {}), (200, {'usage': {'prompt_tokens': 5, 'completion_tokens': 1}}), (200, body)], b)
            self.assertEqual(calls, 3)
            self.assertEqual(usage, {'prompt_tokens': 8, 'completion_tokens': 3})
            self.assertIsNotNone(verdict)

    def test_retry_stops_if_remaining_budget_cannot_cover_it(self):
        with self.guard('18.821798') as b:
            calls, verdict, _ = self.call([(500, {})], b)
            self.assertEqual(calls, 1)
            self.assertIsNone(verdict)

    def test_reservation_write_failure_prevents_network(self):
        with self.guard() as b, patch.object(b, '_append', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.call([], b)

    def test_fsync_failure_prevents_network(self):
        with self.guard() as b, patch('spend_guard.os.fsync', side_effect=OSError('sync failed')):
            with self.assertRaises(OSError): self.call([], b)


if __name__ == '__main__': unittest.main()
