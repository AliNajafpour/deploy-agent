"""One fixed backend HTTP 500 scenario, with a real or scripted planner.

Cloud operations are simulated. Only an AST-restricted version of the test-owned
FastAPI fixture executes locally; this is never a general uploaded-code runner.
"""
import argparse
import ast
import hashlib
import io
import json
import math
import os
import tarfile
import tempfile
import traceback
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from lyara.demo import DemoCloud
from lyara.models import Plan, RunRequest
from lyara.providers import provider_from_env
from lyara.security import redact
from lyara.service import Assistant
from tests.test_backend_repair import FIXED_HANDLER, HANDLER, HANDLER_PATH


def checked_fixture(source):
    """Reject every change except a bounded pure guard/return in summary's body."""
    before, after = ast.parse(HANDLER), ast.parse(source)
    if len(after.body) != len(before.body) or not isinstance(after.body[-1], ast.FunctionDef):
        raise ValueError('The fixture module structure changed.')
    function = after.body[-1]
    body = function.body
    function.body = before.body[-1].body
    same_shell = ast.dump(before) == ast.dump(after)
    function.body = body
    if not same_shell:
        raise ValueError('Imports, setup, decorators and the handler signature must remain identical.')
    allowed = (ast.If, ast.Return, ast.Dict, ast.Constant, ast.Name, ast.Load, ast.Call,
        ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
        ast.UnaryOp, ast.Not, ast.USub, ast.UAdd, ast.BoolOp, ast.And, ast.Or,
        ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is,
        ast.IsNot, ast.IfExp)
    nodes = [node for statement in body for node in ast.walk(statement)]
    if len(nodes) > 120 or any(not isinstance(node, allowed) for node in nodes):
        raise ValueError('Only bounded scalar guards, arithmetic and returns may change.')

    def scalar(node):
        if isinstance(node, ast.Constant):
            return node.value is None or type(node.value) in (bool, int, float)
        if isinstance(node, ast.Call):
            return True  # The exact len(values)/sum(values) shape is checked below.
        if isinstance(node, ast.UnaryOp):
            return scalar(node.operand) or isinstance(node.op, ast.Not) and isinstance(node.operand, ast.Name) and node.operand.id == 'values'
        if isinstance(node, ast.BinOp):
            return scalar(node.left) and scalar(node.right)
        if isinstance(node, ast.BoolOp):
            return all(scalar(value) for value in node.values)
        if isinstance(node, ast.Compare):
            return scalar(node.left) and all(scalar(value) for value in node.comparators)
        if isinstance(node, ast.IfExp):
            return scalar(node.body) and scalar(node.orelse)
        return False

    for node in nodes:
        if isinstance(node, ast.Name) and node.id not in {'values', 'len', 'sum'}:
            raise ValueError('Unknown name in handler.')
        if isinstance(node, ast.Call) and not (
                isinstance(node.func, ast.Name) and node.func.id in {'len', 'sum'}
                and len(node.args) == 1 and isinstance(node.args[0], ast.Name)
                and node.args[0].id == 'values' and not node.keywords):
            raise ValueError('Only len(values) and sum(values) calls are permitted.')
        if isinstance(node, ast.Dict) and not (
                len(node.keys) == 1 and isinstance(node.keys[0], ast.Constant)
                and node.keys[0].value == 'average' and scalar(node.values[0])):
            raise ValueError('Only the existing average response field is permitted.')
        if isinstance(node, ast.BinOp) and not scalar(node):
            raise ValueError('Sequence arithmetic is forbidden.')
        if isinstance(node, ast.Return) and not isinstance(node.value, ast.Dict):
            raise ValueError('The handler must return its existing response object.')
        if isinstance(node, ast.Constant) and not (
                node.value is None or type(node.value) is bool or node.value == 'average'
                or type(node.value) in (int, float) and math.isfinite(node.value) and abs(node.value) <= 1000000):
            raise ValueError('Unexpected constant in handler.')
    return after


def check_allowlist():
    checked_fixture(FIXED_HANDLER)
    for source in (
            HANDLER.replace('return {"average": sum(values) / len(values)}', 'import os\n    return {"average": 0}'),
            HANDLER.replace('sum(values) / len(values)', '__import__("os").getcwd()'),
            HANDLER.replace('sum(values) / len(values)', 'values.__class__'),
            HANDLER.replace('sum(values) / len(values)', '[x for x in values]'),
            HANDLER.replace('sum(values) / len(values)', 'sum(values, 0)'),
            HANDLER.replace('sum(values) / len(values)', 'values * 1000000'),
            HANDLER.replace('sum(values) / len(values)', '"average" * 1000000'),
            HANDLER.replace('@app.post("/summary")', '@app.post("/different")'),
            HANDLER + '\nprint("extra setup")\n'):
        try:
            checked_fixture(source)
        except ValueError:
            continue
        raise AssertionError('AST guard accepted an unsafe fixture change.')


class BackendCloud(DemoCloud):
    def __init__(self, original):
        super().__init__()
        self.original, self.sources = original, {}
        self.uploaded_sha256 = self.installed_sha256 = None
        self.apps['demo-app']['type'] = 'python'
        self.install(HANDLER)
        self.baseline = self.health('https://demo-app.liara.run')
        assert self.baseline['status'] == 500, 'Baseline did not reproduce HTTP 500.'
        try:
            with TestClient(self.app, raise_server_exceptions=True) as client:
                client.post('/summary', json=[])
        except ZeroDivisionError:
            self.log = traceback.format_exc()[-6000:]
        else:
            raise AssertionError('Baseline did not raise the expected division error.')

    def install(self, source):
        tree = checked_fixture(source)
        namespace = {}
        # The module shell is the exact trusted fixture; the body passed the allowlist.
        exec(compile(tree, HANDLER_PATH, 'exec'), namespace)
        if hasattr(self, 'http'):
            self.http.close()
        self.app = namespace['app']
        self.http = TestClient(self.app, raise_server_exceptions=False)

    def close(self):
        self.http.close()

    def inspect_archive(self, raw):
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
            members = archive.getmembers()
            if len(members) != len(self.original) or any(not item.isfile() for item in members):
                raise ValueError('Unexpected files or archive members in candidate.')
            files = {item.name: archive.extractfile(item).read() for item in members}
        if set(files) != set(self.original):
            raise ValueError('The candidate added or removed fixture files.')
        if any(content != self.original[name] for name, content in files.items() if name != HANDLER_PATH):
            raise ValueError('The candidate changed an unrelated file.')
        source = files[HANDLER_PATH].decode('utf8')
        checked_fixture(source)
        return source

    def health(self, url):
        response = self.http.post('/summary', json=[])
        return {'available': True, 'healthy': response.status_code == 200,
            'status': response.status_code, 'url': url, 'local_lab': True,
            'probe': 'Real FastAPI TestClient POST /summary with []',
            'cloud_api': 'simulated'}

    def client(self):
        client = super().client()
        client.health = self.health
        client.local_lab = True
        return client

    def handle(self, request):
        path = request.url.path
        if path.endswith('/logs'):
            return httpx.Response(200, json=[{'message': self.log, 'type': 'stderr'}])
        if request.method == 'POST' and path.endswith('/sources'):
            packet = BytesParser(policy=default).parsebytes(('Content-Type: '
                + request.headers['content-type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + request.read())
            part = next(item for item in packet.iter_parts()
                if item.get_param('name', header='content-disposition') == 'file')
            raw = part.get_payload(decode=True)
            self.inspect_archive(raw)
            self.uploaded_sha256 = hashlib.sha256(raw).hexdigest()
            self.sources['backend-source'] = raw
            self.mutations.append(('POST', path))
            return httpx.Response(200, json={'sourceID': 'backend-source'})
        if request.method == 'POST' and path.endswith('/releases'):
            raw = self.sources[json.loads(request.content)['sourceID']]
            self.install(self.inspect_archive(raw))
            self.installed_sha256 = hashlib.sha256(raw).hexdigest()
            self.log = 'POST /summary with [] returned HTTP ' + str(self.health('')['status'])
        return super().handle(request)


class ScriptedPlanner:
    name = 'scripted-backend-test'

    def plan(self, request, context):
        return Plan(summary='اصلاح خطای سایت برای فهرست خالی', evidence=['before.logs: ZeroDivisionError'],
            steps=[{'action': 'repair_deploy', 'title': 'اصلاح و انتشار',
                'reason': 'برای فهرست خالی نتیجهٔ صفر لازم است.', 'args': {
                    'platform': 'python', 'port': 3000, 'files': [{'path': HANDLER_PATH,
                    'before_sha256': context['deployment_files'][HANDLER_PATH]['sha256'],
                    'content': FIXED_HANDLER}]}}])


def run(provider, report):
    check_allowlist()
    result = {'scenario': 'nested-backend-http500', 'passed': False, 'cloud_api': 'simulated',
        'execution': 'real local FastAPI TestClient, AST-restricted fixed fixture only',
        'provider': provider.name, 'model': getattr(provider, 'model', provider.name)}
    try:
        with tempfile.TemporaryDirectory(prefix='lyara-backend-check-') as temporary:
            root = Path(temporary)
            original = {HANDLER_PATH: HANDLER.encode(), 'requirements.txt': b'fastapi==0.115.0\n'}
            for name, content in original.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            cloud = BackendCloud(original)
            service = Assistant(root, root / '.lyara', cloud.client(), provider)
            try:
                result['baseline'] = cloud.baseline
                result['source_sha256_before'] = hashlib.sha256(original[HANDLER_PATH]).hexdigest()
                proposal = service.start(RunRequest(intent='repair', project='demo-app',
                    platform='python', port=3000, message=(
                        'The existing POST /summary handler returns HTTP 500 for an empty list. '
                        'Fix that backend defect. The intended average for [] is 0; [2,4] must '
                        'remain 3 and [0] must return 0. Preserve the endpoint, imports, '
                        'function signature and all unrelated files. No features or dependencies.')))
                result['proposal'] = proposal
                assert proposal['status'] == 'awaiting_approval', 'The planner did not produce a reviewable repair.'
                assert not cloud.mutations, 'Cloud changed before approval.'
                assert all((root / name).read_bytes() == content for name, content in original.items()), 'Original source changed.'
                raw = Path(proposal['artifact']['path']).read_bytes()
                source = cloud.inspect_archive(raw)
                result['source_sha256_after'] = hashlib.sha256(source.encode()).hexdigest()
                approved_hash = hashlib.sha256(raw).hexdigest()
                assert approved_hash == proposal['artifact']['sha256'], 'Candidate hash mismatch.'
                result['approved_artifact_sha256'] = approved_hash
                final = service.approve(proposal['id'], proposal['plan_digest'], True)
                result['final'] = service.report(final['id'])
                result['status'] = final['status']
                assert final['status'] == 'completed', 'Approved local repair did not complete.'
                assert cloud.uploaded_sha256 == cloud.installed_sha256 == approved_hash, 'Release bytes differ from approved candidate.'
                assert final['verification']['health']['status'] == 200, 'Final health did not observe actual HTTP 200.'
                samples = []
                for values, expected in (([], 0), ([2, 4], 3), ([0], 0)):
                    response = cloud.http.post('/summary', json=values)
                    body = response.json()
                    assert response.status_code == 200 and body == {'average': expected}, 'Handler returned the wrong result.'
                    assert type(body['average']) in (int, float), 'Average must be numeric.'
                    samples.append({'input': values, 'status': response.status_code, 'response': body})
                assert all((root / name).read_bytes() == content for name, content in original.items()), 'Original source changed.'
                result.update(passed=True, samples=samples, mutations=cloud.mutations,
                    uploaded_artifact_sha256=cloud.uploaded_sha256, originals_unchanged=True)
            finally:
                service.close()
                cloud.close()
    except Exception as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        report = Path(report)
        report.parent.mkdir(parents=True, exist_ok=True)
        safe = redact(result, [os.getenv('LYARA_LLM_API_KEY', '')])
        report.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding='utf8')
    return result['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=['api', 'scripted'], default='scripted')
    parser.add_argument('--report', type=Path, default=Path('.lyara/checks/backend-repair.json'))
    args = parser.parse_args()
    if args.provider == 'api':
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / '.env')
        planner = provider_from_env({**os.environ, 'LYARA_PROVIDER': 'api'})
    else:
        planner = ScriptedPlanner()
    passed = run(planner, args.report)
    print(('PASS' if passed else 'FAIL') + ': nested backend HTTP 500; report: ' + str(args.report))
    raise SystemExit(0 if passed else 1)
