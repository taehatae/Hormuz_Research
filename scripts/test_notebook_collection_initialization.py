"""Run the actual notebook entry point against a tiny isolated offline project.

No production queue, SQLite database, HTML or result is opened for writing.
"""
import contextlib
import csv
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class NotebookInitializationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hormuz_notebook_offline_')
        self.root = Path(self.temp.name)
        (self.root/'scripts').mkdir()
        shutil.copyfile(ROOT/'scripts/body_pilot_200.py', self.root/'scripts/body_pilot_200.py')
        self.prior = self.root/'hormuz_2026_data/runs/body_pilot_200_20261107_01'
        self.prior.mkdir(parents=True)
        shutil.copyfile(ROOT/'hormuz_2026_data/runs/body_pilot_200_20261107_01/pilot_config.json', self.prior/'pilot_config.json')
        # An old pilot state is intentionally unusable by the new notebook.
        (self.prior/'pilot_state.csv').write_text('historical state: do not read or change', encoding='utf-8')
        self.old_state = (self.prior/'pilot_state.csv').read_bytes()
        self.out = self.root/'hormuz_2026_data/runs/notebook_body_collection_v2'
        candidate = self.root/'hormuz_2026_data/runs/candidate_selection_20261107_01'
        candidate.mkdir()
        (candidate/'validation.json').write_text(json.dumps(dict(passed=True, checks={'ready_nonpaid_English_metadata': True}, queue_rows=2)), encoding='utf-8')
        rows = [dict(article_id=f'{i:016x}', request_url=f'https://example.org/mock/{i}', domain='example.org', published_date=f'2026-0{i+1}-01', month=f'2026-0{i+1}') for i in range(2)]
        self.write_csv(candidate/'candidate_queue.csv', rows)
        self.write_csv(candidate/'candidate_screening.csv', [dict(article_id=r['article_id'],month=r['month'],domain=r['domain'],nc_country='unknown',raw_NA_country_evidence='false',selection_status='ready',nc_language='en',nc_paid_content='false') for r in rows])
        self.write_csv(self.root/'hormuz_2026_data/05_extraction_candidates_v2.csv', [dict(article_id=r['article_id'],title='Synthetic Strait of Hormuz report') for r in rows])
        notebook = json.loads((ROOT/'hormuz_textminig_v2.ipynb').read_text('utf-8'))
        source = ''.join(notebook['cells'][59]['source'])
        # Execute the original definitions, never the real-project bottom call.
        source = source.rsplit('notebook_collection_summary=run_notebook_body_collection(', 1)[0]
        self.namespace = {}
        exec(compile(source, 'notebook_collection_cell', 'exec'), self.namespace)
        self.namespace['_make_notebook_extractors'] = self.extractors
        self.saved_module = sys.modules.pop('body_pilot_200', None)
        self.saved_path = sys.path[:]
        sys.path.insert(0, str(self.root/'scripts'))
        self.saved_cwd = Path.cwd()
        os.chdir(self.root)
        self.calls = []
        self.reloads = 0
        self.pilot = importlib.import_module('body_pilot_200')
        self.poison_cached_module()

    @staticmethod
    def write_csv(path, rows):
        with path.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)

    def poison_cached_module(self):
        # Reproduce an obsolete in-memory loader which still needs the deleted CSV.
        def obsolete(*args, **kwargs):
            return self.pilot.csvread(self.prior/'domain_access_policies.csv')
        self.pilot.load_locked_pilot = obsolete
        self.pilot.Gateway = obsolete

    def configure_reloaded(self, module):
        self.reloads += 1
        module = self.real_reload(module)
        module._OFFLINE_PROCESS = True  # audit hook also forbids worker sockets
        calls = self.calls
        class Transport:
            def __init__(self, config): pass
            def get_validated(self, url, limit, target):
                calls.append(url)
                if url.endswith('/robots.txt'):
                    return module.Response(url, 200, {}, b'User-agent: *\nAllow: /\n')
                return module.Response(url, 200, {'content-type':'text/html'}, b'<html><body><article>Synthetic Strait of Hormuz report.</article></body></html>')
        base = module.Gateway
        class OfflineGateway(base):
            def __init__(self, *args, **kwargs):
                clock = [1000.]
                kwargs.update(now=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0]+seconds))
                super().__init__(*args, **kwargs)
                self.guard.resolver = lambda host, port: ['93.184.216.34']
        module.NativeTransport = Transport
        module.Gateway = OfflineGateway
        return module

    @staticmethod
    def extractors(module, dependency_dir):
        def extract(html, url, title, config):
            body = 'Synthetic English Strait of Hormuz report. '*25 + url
            quality = module.quality_metrics(body, title, config)
            quality.update(language='en', language_probability=.99, review_reasons=['offline_fixture_human_unconfirmed'])
            return dict(chosen_extractor='trafilatura', body=body, quality=quality, extractors={'trafilatura':dict(body=body)})
        return extract, {'registry_status':'offline_fixture'}

    def run_collection(self, resume):
        self.real_reload = importlib.reload
        with patch('importlib.reload', side_effect=self.configure_reloaded), patch.object(socket, 'getaddrinfo', side_effect=AssertionError('real DNS forbidden')), patch.object(socket, 'create_connection', side_effect=AssertionError('real HTTP forbidden')), contextlib.redirect_stdout(io.StringIO()):
            result = self.namespace['run_notebook_body_collection'](2, 2, resume, 2026)
        self.assertFalse((self.prior/'domain_access_policies.csv').exists())
        self.assertEqual((self.prior/'pilot_state.csv').read_bytes(), self.old_state)
        self.assertFalse((self.out/'.execution.lock').exists())
        return result

    def test_new_initialization_without_policy_csv_and_stale_module(self):
        summary = self.run_collection(False)
        self.assertEqual(self.reloads, 1)
        self.assertEqual(summary['nonempty_bodies'], 2)
        self.assertEqual(summary['article_HTTP_URLs_this_invocation'], 2)  # mocks only

    def test_resume_without_policy_csv_preserves_existing_bodies_and_attempts(self):
        self.run_collection(False)
        with contextlib.closing(sqlite3.connect(self.out/'collection.sqlite3')) as db:
            before = db.execute('SELECT * FROM state ORDER BY article_id').fetchall()
            bodies = db.execute('SELECT * FROM corpus ORDER BY article_id').fetchall()
        files = [self.out/'collection_priority.csv', self.out/'priority_manifest.json', *self.out.glob('html/*'), *self.out.glob('texts/*'), *self.out.glob('results/*')]
        hashes = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        self.calls.clear(); self.poison_cached_module()
        summary = self.run_collection(True)
        self.assertEqual(self.reloads, 2)
        self.assertEqual(self.calls, [])
        self.assertEqual(summary['HTTP_requests_this_invocation'], 0)
        self.assertEqual(summary['nonempty_bodies'], 2)
        with contextlib.closing(sqlite3.connect(self.out/'collection.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT * FROM state ORDER BY article_id').fetchall(), before)
            self.assertEqual(db.execute('SELECT * FROM corpus ORDER BY article_id').fetchall(), bodies)
        self.assertEqual({p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}, hashes)

    def tearDown(self):
        os.chdir(self.saved_cwd)
        sys.path[:] = self.saved_path
        sys.modules.pop('body_pilot_200', None)
        if self.saved_module is not None: sys.modules['body_pilot_200'] = self.saved_module
        self.temp.cleanup()


if __name__ == '__main__':
    unittest.main()
