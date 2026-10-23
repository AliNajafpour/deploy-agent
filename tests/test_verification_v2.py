import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from lyara.verification import (collect_samples, compare_results, discover_routes,
                                safe_path, verify_stability)


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Cloud:
    def __init__(self, statuses=(200,), current='new', state='READY', advance=None):
        self.statuses = iter(statuses)
        self.current, self.state, self.advance = current, state, advance
        self.urls, self.timeouts, self.release_calls = [], [], 0

    def health(self, url, timeout=10):
        self.urls.append(url)
        self.timeouts.append(timeout)
        if self.advance:
            self.advance.sleep(timeout)
        status = next(self.statuses, 200)
        if status is None:
            return {'available': False, 'probe_attempted': False}
        if status == 'network':
            return {'available': False, 'probe_attempted': True}
        return {'available': True, 'status': status, 'latency_ms': 20}

    def releases(self, project):
        self.release_calls += 1
        return {'currentRelease': self.current,
                'releases': [{'_id': 'new', 'state': self.state}]}


TARGETS = [{'path': '/', 'expected_statuses': [200], 'evidence': 'source:index.html'}]


class VerificationChecks(unittest.TestCase):
    def artifact(self, entries):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        archive_path = Path(temp.name) / 'source.tar.gz'
        with tarfile.open(archive_path, 'w:gz') as archive:
            for name, text in entries.items():
                raw = text.encode()
                info = tarfile.TarInfo(name)
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
        return {'path': str(archive_path)}

    def test_discovers_safe_get_source_routes_and_prioritizes_error_route(self):
        artifact = self.artifact({'main.py': '''raise RuntimeError("never execute")
@app.get('/')
def index(): pass
@app.post('/purchase')
def post(): pass
@app.get('/api/users/{id}')
def dynamic(): pass
@app.get('/checkout')
def dangerous(): pass
@app.get('/about')
def about(): pass
@app.get('/health')
def health(): pass
@app.get('/products')
def products(): pass
@app.get('/summary')
def summary(): pass
''', 'server.js': 'router.get("/api/status", (req,res) => res.json({ok:true})); app.post("/write", handler);',
            'public/contact.html': '<html/>', '.env': 'SECRET=/private', '../evil.py': '@app.get("/evil")'})
        targets = discover_routes(artifact, {'logs': 'GET /summary HTTP/1.1 500; GET /unknown 500'})
        paths = [row['path'] for row in targets]
        self.assertEqual(len(paths), 5)
        self.assertEqual(paths[:2], ['/', '/summary'])
        for path in ['/checkout', '/purchase', '/api/users/{id}', '/unknown', '/evil']:
            self.assertNotIn(path, paths)

    def test_js_static_and_expected_auth_status_are_explicit(self):
        artifact = self.artifact({'main.py': '@app.get("/restricted", status_code=401)\ndef restricted(): pass',
            'server.mjs': 'app.get("/api/status", (req,res) => res.json({ok:true}))', 'index.html': '<html/>',
            'public/about.html': '<html/>'})
        targets = discover_routes(artifact)
        auth = next(row for row in targets if row['path'] == '/restricted')
        self.assertEqual(auth['expected_statuses'], [401])
        self.assertTrue(auth['expectation_evidence'].startswith('source:main.py:'))
        self.assertIn('/api/status', [row['path'] for row in targets])
        self.assertIn('/about.html', [row['path'] for row in targets])
        self.assertEqual(collect_samples(Cloud([401] * 3), 'https://example.com', [auth])['summary']['success'], 3)

    def test_rejects_unsafe_urls_paths_and_unjustified_4xx(self):
        for path in ['//evil.com/', '/x?delete=true', '/%2e%2e/x', '/a/../x', '/logout', '/admin', '/checkout', '/a\\b', '/resetDatabase', '/.env']:
            self.assertIsNone(safe_path(path), path)
        for url in ['http://example.com', 'https://user:secret@example.com', 'https://example.com/?key=secret', 'https://example.com:444']:
            with self.assertRaises(ValueError):
                collect_samples(Cloud(), url, TARGETS)
        with self.assertRaises(ValueError):
            collect_samples(Cloud(), 'https://example.com', [{'path': '/', 'expected_statuses': [404]}])

    def test_non2xx_and_redirects_are_errors_missing_is_unknown(self):
        cloud = Cloud([404, 302, 'network', None, 200])
        result = collect_samples(cloud, 'https://example.com/path', TARGETS, samples=5)
        self.assertEqual(result['summary'], {'total': 5, 'success': 1, 'errors': 3, 'unknown': 1,
                                            'median_ms': 20, 'p95_ms': 20})
        self.assertEqual(cloud.urls, ['https://example.com/'] * 5)

    def test_timeout_respects_remaining_budget_and_no_extra_request(self):
        clock = Clock()
        cloud = Cloud(advance=clock)
        result = collect_samples(cloud, 'https://example.com', TARGETS, timeout=2, clock=clock, sleep=clock.sleep)
        self.assertFalse(result['complete'])
        self.assertEqual(cloud.timeouts, [2])
        self.assertEqual(clock(), 2)

    def test_old_release_and_unknown_identity_never_stable(self):
        for current in ['old', None]:
            cloud, clock = Cloud(current=current), Clock()
            result = verify_stability(cloud, 'site', 'new', 'https://example.com', TARGETS,
                                      clock=clock, sleep=clock.sleep)
            self.assertFalse(result['stable'])
            self.assertFalse(result['release_confirmed'])
            self.assertEqual(cloud.release_calls, 6)

    def test_minimum_three_rounds_and_failure_in_middle_blocks_fixed(self):
        clock, cloud = Clock(), Cloud([200, 500, 200])
        result = verify_stability(cloud, 'site', 'new', 'https://example.com', TARGETS,
                                  clock=clock, sleep=clock.sleep)
        self.assertTrue(result['release_confirmed'])
        self.assertFalse(result['stable'])
        self.assertEqual(result['summary']['errors'], 1)
        with self.assertRaises(ValueError):
            verify_stability(cloud, 'site', 'new', 'https://example.com', TARGETS, rounds=1)

    def test_release_switch_during_probe_is_not_verified(self):
        cloud = Cloud()
        original = cloud.health
        def switch(url, timeout=10):
            cloud.current = 'old'
            return original(url, timeout)
        cloud.health = switch
        clock = Clock()
        result = verify_stability(cloud, 'site', 'new', 'https://example.com', TARGETS,
                                  clock=clock, sleep=clock.sleep)
        self.assertFalse(result['release_confirmed'])

    def test_comparison_uses_computed_counts_and_requires_stability(self):
        before = collect_samples(Cloud([500] * 3), 'https://example.com', TARGETS)
        after = collect_samples(Cloud([200] * 3), 'https://example.com', TARGETS)
        self.assertEqual(compare_results(before, after)['outcome'], 'نامشخص')
        clock = Clock()
        stable = verify_stability(Cloud(), 'site', 'new', 'https://example.com', TARGETS,
                                  clock=clock, sleep=clock.sleep)
        self.assertEqual(compare_results(before, after, stable)['outcome'], 'رفع شد')
        partial = collect_samples(Cloud([500, 200, 200]), 'https://example.com', TARGETS)
        self.assertEqual(compare_results(before, partial, {'release_confirmed': True, 'stable': False})['outcome'], 'بهتر شد')
        self.assertEqual(compare_results(before, before, stable)['outcome'], 'هنوز مشکل دارد')
        missing = collect_samples(Cloud([None] * 3), 'https://example.com', TARGETS)
        self.assertEqual(compare_results(before, missing, stable)['outcome'], 'نامشخص')
        self.assertNotEqual(compare_results(after, after, stable)['outcome'], 'رفع شد')

    def test_visible_mutation_in_get_handler_is_not_exercised(self):
        artifact = self.artifact({'main.py': '''@app.get('/')
def index():
    database.delete()
@app.get('/clear')
def clear():
    database.commit()
@app.get('/health')
def health():
    return {'ok': True}
'''})
        self.assertEqual([target['path'] for target in discover_routes(artifact)], ['/health'])

    def test_stability_deadline_does_not_hide_partial_checks(self):
        clock = Clock()
        result = verify_stability(Cloud(advance=clock), 'site', 'new', 'https://example.com', TARGETS,
                                  timeout=2, clock=clock, sleep=clock.sleep)
        self.assertFalse(result['stable'])
        self.assertFalse(result['complete'])
        self.assertEqual(clock(), 2)

    def test_js_get_writes_and_unknown_handlers_are_not_automatic_probes(self):
        cases = ["(req,res)=>{counter++;res.send('ok')}",
                 "(req,res)=>{counter--;res.send('ok')}",
                 "(req,res)=>{db.insertOne({x:1});res.send('ok')}",
                 "(req,res)=>{data.count = 1;res.send('ok')}",
                 "(req,res)=>{fetch('https://example.com',{method:'POST'});res.send('ok')}",
                 "(req,res)=>{localStorage.setItem('x','y');res.send('ok')}",
                 "(req,res)=>{hiddenWriter();res.send('ok')}",
                 "(req,res)=>{hiddenWriter`text`;res.send('ok')}", "unknownHandler"]
        for handler in cases:
            with self.subTest(handler=handler):
                artifact = self.artifact({'server.js': "app.get('/'," + handler + "); app.get('/count'," + handler + ');',
                                          'index.html': '<html/>'})
                paths = [row['path'] for row in discover_routes(artifact)]
                self.assertNotIn('/', paths)
                self.assertNotIn('/count', paths)

    def test_python_helpers_are_followed_and_unknowns_or_cycles_fail_closed(self):
        artifact = self.artifact({'main.py': '''def helper():
    return deeper()
def deeper():
    db.insert_one({'x': 1})
def pure(values):
    return sum(values)
def recursive():
    return recursive()
@side_effect_wrapper
def wrapped():
    return 1
@app.get('/')
def index():
    return helper()
@app.get('/unknown')
def unknown():
    return imported_handler()
@app.get('/cycle')
def cycle():
    return recursive()
@app.get('/wrapped')
def decorated_helper():
    return wrapped()
@app.get('/health')
def health():
    return {'value': pure([1, 2])}
'''})
        self.assertEqual([row['path'] for row in discover_routes(artifact)], ['/health'])

    def test_unknown_source_has_no_implicit_root_probe(self):
        self.assertEqual(discover_routes(self.artifact({'README.md': 'No routes'})), [])
        self.assertEqual(discover_routes(self.artifact({'server.js': "app.get('/', (req,res)=>/regex/.test('x'));"})), [])

    def test_python_dependency_and_nonlocal_writes_are_omitted(self):
        artifact = self.artifact({'main.py': '''@app.get('/one')
def one():
    global counter
    counter += 1
@app.get('/two', dependencies=[Depends(write)])
def two():
    return {'ok': True}
@app.get('/three')
def three(value=Depends(write)):
    return {'ok': True}
@app.get('/four')
def four():
    values['counter'] = 1
'''})
        self.assertEqual(discover_routes(artifact), [])

    def test_shadowed_builtin_or_render_helper_is_not_assumed_pure(self):
        for prefix, expression in [('from custom import mutate as sum', 'sum([1])'),
                                   ('from custom import jsonify', "jsonify({'ok':True})"),
                                   ('import custom as json', "json.dumps({'ok':True})"),
                                   ('sum = custom_writer', 'sum([1])')]:
            artifact = self.artifact({'main.py': prefix + '\n@app.get("/")\ndef root():\n    return ' + expression})
            self.assertEqual(discover_routes(artifact), [], prefix)

    def test_faster_errors_do_not_count_as_improvement(self):
        before = collect_samples(Cloud([200] * 3), 'https://example.com', TARGETS)
        after = collect_samples(Cloud([500] * 3), 'https://example.com', TARGETS)
        before['summary']['median_ms'] = 1000
        after['summary']['median_ms'] = 10
        result = compare_results(before, after, {'release_confirmed': True, 'stable': False})
        self.assertEqual(result['outcome'], 'هنوز مشکل دارد')


if __name__ == '__main__':
    unittest.main()
