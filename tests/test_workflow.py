import copy
import csv
import hashlib
import json
import tempfile
import threading
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from star_preparation.engine import prepare, suggest_profile
from star_preparation.export import STAR_FIELDS, write_run
from star_preparation.reader import coerce_excel_date, read_table
from star_preparation.store import Store
from star_preparation.web import Application, make_server


def workbook(path, sheets, epoch=False):
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    with zipfile.ZipFile(path, 'w') as archive:
        names = ''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1))
        archive.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="{rel}"><workbookPr date1904="{int(epoch)}"/><sheets>{names}</sheets></workbook>')
        links = ''.join(f'<Relationship Id="rId{i}" Target="worksheets/custom{i}.xml"/>' for i in range(1, len(sheets)+1))
        archive.writestr('xl/_rels/workbook.xml.rels', f'<Relationships>{links}</Relationships>')
        for i, (_, rows) in enumerate(sheets, 1):
            xml = []
            for number, values in enumerate(rows, 1):
                cells = ''.join(f'<c r="{chr(65+j)}{number}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>' for j, value in enumerate(values))
                xml.append(f'<row r="{number}">{cells}</row>')
            archive.writestr(f'xl/worksheets/custom{i}.xml', f'<worksheet xmlns="{ns}"><sheetData>{"".join(xml)}</sheetData></worksheet>')


def profile():
    p = suggest_profile(['Issue ID', 'Created', 'Resolved', 'Status', 'Version', 'Component', 'Priority'])
    p['confirmations'] = {key: True for key in p['confirmations']}
    p['inclusion_rules']['version_policy']['aliases'] = {'V1 build 1': 'V1', 'V1 build 2': 'V1', 'V2 build 1': 'V2'}
    p['inclusion_rules']['duplicate_policy'] = 'issue_release'
    p['release_dates'] = {'V1': '2025-02-01'}
    return p


CSV = ('Issue ID,Created,Resolved,Status,Version,Component,Priority\n'
       'A,2025-01-03,,Open,V1 build 1,Core,High\n'
       'A,2025-01-04,,Open,V1 build 2,Core,High\n'
       'B,2025-01-03,2025-02-01,Closed,V1 build 2,UI,Low\n'
       'A,2025-02-05,,Open,V2 build 1,Core,High\n')


class ReaderTests(unittest.TestCase):
    def test_duplicate_headers_preserve_both_dates_and_sheet_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'legacy.xlsx'
            workbook(path, [('Notes', [['Instructions are source data']]), ('Defects', [
                ['Report title'], ['Issue ID', 'Issue', 'Issue'], ['A', '45000', '45010']])])
            table = read_table(path, sheet='Defects', header_row=2)
            self.assertEqual(table['columns'], ['Issue ID', 'Issue [2]', 'Issue [3]'])
            self.assertEqual(table['records'][0]['Issue [2]'], '45000')
            self.assertEqual(table['records'][0]['Issue [3]'], '45010')
            self.assertEqual(table['row_numbers'], [3])
            self.assertEqual(table['duplicate_headers'], ['Issue'])
            self.assertFalse(read_table(path, sheet='Defects')['valid_headers'])

    def test_1904_epoch_and_explicit_text_date_formats(self):
        self.assertEqual(coerce_excel_date('1', date_system='1904'), '1904-01-02')
        self.assertEqual(coerce_excel_date('03/04/2025', 'dmy'), '2025-04-03')
        self.assertEqual(coerce_excel_date('03/04/2025', 'mdy'), '2025-03-04')
        self.assertEqual(coerce_excel_date('2025-01-01T23:30:00Z'), '2025-01-01')
        for value in ('03/04/2025', '2025-02-30', 'nan', '9999999999999999999'):
            with self.assertRaises(ValueError):
                coerce_excel_date(value)

    def test_encoding_delimiter_empty_and_colliding_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'legacy.csv'
            path.write_bytes('ID;Title;Title;Title [2]\nA;Caf\xe9;two;three\n'.encode('cp1252'))
            result = read_table(path, encoding='cp1252', delimiter=';')
            self.assertEqual(len(set(result['columns'])), 4)
            self.assertEqual(list(result['records'][0].values()), ['A', 'Café', 'two', 'three'])
            with self.assertRaises(ValueError):
                read_table(path)
            path.write_text('')
            self.assertFalse(read_table(path)['valid_headers'])

    def test_source_formula_is_not_evaluated_or_trusted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'formula.xlsx'
            workbook(path, [('Data', [['ID', 'Created'], ['A', '45000']])])
            with zipfile.ZipFile(path) as archive:
                parts = {name: archive.read(name) for name in archive.namelist()}
            parts['xl/worksheets/custom1.xml'] = parts['xl/worksheets/custom1.xml'].replace(
                b'<c r="B2" t="inlineStr"><is><t>45000</t></is></c>', b'<c r="B2"><f>NOW()</f><v>45000</v></c>')
            with zipfile.ZipFile(path, 'w') as archive:
                for name, body in parts.items():
                    archive.writestr(name, body)
            self.assertEqual(read_table(path)['records'][0]['Created'], '#FORMULA!')


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'customer.csv'
        self.source.write_text(CSV)
        self.profile = profile()

    def tearDown(self):
        self.temp.cleanup()

    def test_release_counts_deduplication_same_day_and_star_contract(self):
        result = prepare(self.source, self.profile)
        self.assertTrue(result['validation_report']['approved'])
        self.assertEqual(result['validation_report']['rows_included'], 3)
        self.assertEqual(result['validation_report']['rows_excluded'], 1)
        self.assertEqual(result['series'], [
            {'release': 'V1', 'date': '2025-01-03', 'new_defects': 2, 'cumulative_defects': 2},
            {'release': 'V2', 'date': '2025-02-05', 'new_defects': 1, 'cumulative_defects': 1}])
        output = self.root / 'run'
        write_run(result, output)
        with (output / 'star_defects.csv').open() as file:
            reader = csv.DictReader(file)
            self.assertEqual(reader.fieldnames, list(STAR_FIELDS))
            records = list(reader)
        self.assertEqual(records[0]['defect_id'], 'A')
        self.assertEqual(records[0]['closure_date'], '')
        manifest = json.loads((output / 'release-manifest.json').read_text())
        self.assertEqual([(m['release'], m['rows']) for m in manifest], [('V1', 2), ('V2', 1)])
        for name, checksum in json.loads((output / 'checksums.json').read_text()).items():
            self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), checksum)
        self.assertEqual(json.loads((output / 'profile.json').read_text()), self.profile)
        with zipfile.ZipFile(output / 'preparation-bundle.zip') as bundle:
            self.assertIn('release-001-star.csv', bundle.namelist())
        with self.assertRaises(ValueError):
            write_run(result, output)

    def test_global_and_keep_all_duplicate_policies(self):
        for mode, expected in [('issue', 2), ('keep_all', 4), ('review', 2)]:
            with self.subTest(mode=mode):
                self.profile['inclusion_rules']['duplicate_policy'] = mode
                result = prepare(self.source, self.profile)
                self.assertEqual(result['validation_report']['rows_included'], expected)
                self.assertEqual(result['validation_report']['approved'], mode != 'review')

    def test_bad_dates_and_unknown_release_are_reviewed_then_corrected(self):
        self.source.write_text(CSV + 'C,03/04/2025,,Open,Unknown,API,Low\n')
        original = self.source.read_bytes()
        result = prepare(self.source, self.profile)
        self.assertFalse(result['validation_report']['approved'])
        self.assertIn('Invalid arrival_date', result['decisions'][-1]['reason'])
        corrections = {'6': {'action': 'edit', 'values': {'arrival_date': '2025-04-03', 'release': 'V2'}, 'reason': 'Confirmed in source system'}}
        result = prepare(self.source, self.profile, resolutions=corrections)
        self.assertTrue(result['validation_report']['approved'])
        self.assertEqual(result['decisions'][-1]['correction_reason'], 'Confirmed in source system')
        self.assertEqual(self.source.read_bytes(), original)
        with self.assertRaises(ValueError):
            prepare(self.source, self.profile, resolutions={'6': {'action': 'exclude'}})

    def test_missing_fields_chronology_and_formula_injection_do_not_silently_pass(self):
        for record in ['C,,,Open,V1 build 1,API,High',
                       'C,2025-03-01,2025-02-01,Open,V1 build 1,API,High',
                       '=CMD(),2025-03-01,,Open,V1 build 1,API,High']:
            self.source.write_text(CSV + record + '\n')
            result = prepare(self.source, self.profile)
            self.assertEqual(result['decisions'][-1]['decision'], 'review_required')
            self.assertFalse(result['validation_report']['approved'])

    def test_explicit_status_version_exclusions_and_no_empty_approval(self):
        self.profile['inclusion_rules']['statuses'] = ['Closed']
        result = prepare(self.source, self.profile)
        self.assertEqual(result['validation_report']['rows_included'], 1)
        self.profile['inclusion_rules']['version_policy']['excluded_values'] = ['V1 build 2']
        result = prepare(self.source, self.profile)
        self.assertFalse(result['validation_report']['approved'])
        self.assertEqual(result['validation_report']['rows_included'], 0)

    def test_unconfirmed_rules_block_even_if_every_row_is_excluded(self):
        self.profile['confirmations']['date'] = False
        self.profile['inclusion_rules']['statuses'] = ['Not present']
        result = prepare(self.source, self.profile)
        self.assertTrue(result['validation_report']['approval_required'])
        write_run(result, self.root / 'run')
        self.assertFalse((self.root / 'run' / 'star_defects.csv').exists())
        self.assertFalse(list((self.root / 'run').glob('release-*-star.csv')))

    def test_unsupported_policy_fails_closed(self):
        self.profile['inclusion_rules']['version_policy']['mode'] = 'typo'
        with self.assertRaises(ValueError):
            prepare(self.source, self.profile)


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Application(self.root)
        self.headers = {'Host': 'localhost:8080', 'Origin': 'http://localhost:8080'}

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, target, payload=None, headers=None, raw=None, app=None):
        body = raw if raw is not None else json.dumps(payload).encode() if payload is not None else b''
        status, response_headers, result = (app or self.app).response(method, target, headers or self.headers, body)
        return status, response_headers, json.loads(result) if response_headers.get('Content-Type', '').startswith('application/json') else result

    def make_run(self):
        status, _, upload = self.call('POST', '/api/uploads', raw=CSV.encode(), headers={**self.headers, 'X-Filename': 'customer%20legacy.csv'})
        self.assertEqual(status, 201)
        status, _, run = self.call('POST', '/api/prepare', {'upload_id': upload['upload_id'], 'profile': profile()})
        self.assertEqual(status, 201)
        return upload, run

    def test_upload_inspection_history_revision_filtered_records_and_download(self):
        upload, run = self.make_run()
        status, _, info = self.call('POST', '/api/inspect', {'upload_id': upload['upload_id']})
        self.assertEqual(info['report']['rows_read'], 4)
        status, _, page = self.call('GET', f'/api/runs/{run["id"]}/records?release=V1&decision=included&limit=1')
        self.assertEqual(page['total'], 2)
        self.assertEqual(len(page['rows']), 1)
        _, _, history = self.call('GET', '/api/runs')
        self.assertEqual(history['runs'][0]['filename'], 'customer legacy.csv')
        _, headers, data = self.call('GET', f'/runs/{run["id"]}/release-001-star.csv')
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertTrue(data.startswith(b'defect_id,'))
        changed = copy.deepcopy(profile())
        changed['inclusion_rules']['statuses'] = ['Closed']
        _, _, revised = self.call('POST', '/api/prepare', {'upload_id': upload['upload_id'], 'profile': changed, 'parent_id': run['id']})
        self.assertEqual(revised['parent_id'], run['id'])
        self.assertEqual(self.app.store.run(run['id'])['validation']['rows_included'], 3)
        self.assertEqual(revised['validation']['rows_included'], 1)
        with zipfile.ZipFile(self.app.store.artifact(run['id'], 'preparation-bundle.zip')) as bundle:
            self.assertEqual(bundle.read('source.csv'), CSV.encode())

    def test_versioned_profiles_and_concurrent_edit_detection(self):
        payload = {'profile': profile(), 'base_revision': 0}
        status, _, result = self.call('POST', '/api/profiles', payload)
        self.assertEqual(status, 201)
        self.assertEqual(result['revision'], 1)
        status, _, _ = self.call('POST', '/api/profiles', payload)
        self.assertEqual(status, 400)
        payload['base_revision'] = 1
        self.assertEqual(self.call('POST', '/api/profiles', payload)[2]['revision'], 2)
        old = self.call('GET', '/api/profiles/customer?revision=1')[2]
        self.assertEqual(old['revision'], 1)
        self.assertEqual(len(old['history']), 2)

    def test_csrf_host_path_traversal_and_shared_startup_checks(self):
        status, _, _ = self.call('POST', '/api/uploads', raw=b'A', headers={'Host': 'localhost:8080', 'Origin': 'https://evil.example'})
        self.assertEqual(status, 403)
        self.assertEqual(self.call('GET', '/api/runs', headers={'Host': 'evil.example'})[0], 403)
        _, run = self.make_run()
        self.assertEqual(self.call('GET', f'/runs/{run["id"]}/..%2f..%2fmetadata.sqlite3')[0], 404)
        with self.assertRaises(ValueError):
            Application(self.root, shared=True)
        with self.assertRaises(ValueError):
            make_server('0.0.0.0', 0, application=self.app)

    def test_shared_authentication_authorization_logout_and_private_download(self):
        self.app.store.add_user('viewer', 'a-long-test-password', 'viewer')
        self.app.store.add_user('engineer', 'another-test-password', 'preparer')
        shared = Application(self.root, shared=True, secure_cookies=True)
        upload, run = self.make_run()
        self.assertEqual(self.call('GET', '/api/runs', app=shared)[0], 401)
        self.assertEqual(self.call('GET', f'/runs/{run["id"]}/star_defects.csv', app=shared)[0], 401)
        status, headers, result = self.call('POST', '/api/login', {'username': 'viewer', 'password': 'a-long-test-password'}, app=shared)
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('Secure', headers['Set-Cookie'])
        signed = {**self.headers, 'Cookie': headers['Set-Cookie'].split(';')[0]}
        self.assertEqual(self.call('GET', '/api/runs', headers=signed, app=shared)[0], 200)
        self.assertEqual(self.call('POST', '/api/prepare', {}, headers=signed, app=shared)[0], 403)
        self.assertEqual(self.call('GET', f'/runs/{run["id"]}/star_defects.csv', headers=signed, app=shared)[0], 200)
        self.call('POST', '/api/logout', {}, headers=signed, app=shared)
        self.assertEqual(self.call('GET', '/api/runs', headers=signed, app=shared)[0], 401)

    def test_session_revocation_and_failed_login_throttling(self):
        self.app.store.add_user('engineer', 'another-test-password', 'preparer')
        token = self.app.store.login('engineer', 'another-test-password')
        self.app.store.add_user('engineer', 'changed-test-password', 'viewer')
        self.assertIsNone(self.app.store.user(token))
        with self.app.store.db() as db:
            db.execute('INSERT INTO attempts VALUES (?,?,?)', ('engineer', 10, time.time()+100))
        with self.assertRaisesRegex(PermissionError, 'Too many'):
            self.app.store.login('engineer', 'changed-test-password')

    def test_persistence_and_retention_preview(self):
        upload, run = self.make_run()
        store = Store(self.root)
        self.assertEqual(store.run(run['id'])['id'], run['id'])
        with store.db() as db:
            db.execute('UPDATE runs SET created=0')
            db.execute('UPDATE uploads SET created=0')
        self.assertEqual(store.purge(30), {'runs': 1, 'uploads': 1, 'deleted': False})
        self.assertEqual(len(store.history()), 1)
        self.assertTrue(store.purge(30, execute=True)['deleted'])
        self.assertEqual(store.history(), [])
        self.assertEqual(list((self.root/'uploads').iterdir()), [])

    def test_real_http_upload_finishes_without_waiting_for_connection_close(self):
        server = make_server('127.0.0.1', 0, application=self.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f'http://127.0.0.1:{server.server_address[1]}'
            request = urllib.request.Request(origin+'/api/uploads', CSV.encode(),
                headers={'Origin': origin, 'X-Filename': 'legacy.csv'}, method='POST')
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 201)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
