"""Explicitly offline demonstration; production graph and HTTP contracts are real."""
import json

import httpx

from .liara import LiaraClient
from .models import Plan


class DemoCloud:
    def __init__(self):
        self.apps = {'demo-app': {'_id': 'demo-id', 'project_id': 'demo-app', 'type': 'node',
                     'status': 'Running', 'scale': 1, 'planID': 'demo-plan', 'isDeployed': True,
                     'envs': [{'key': 'DB_PASSWORD', 'value': 'demo-db-password'}]}}
        self.releases_by_app = {}
        self.mutations, self.fail_release, self.denied = [], False, False

    def client(self):
        client = LiaraClient('offline-demo-token', transport=httpx.MockTransport(self.handle))
        client.sandbox=True
        client.health = lambda url: {'available': True, 'healthy': not self.fail_release,
                                    'status': 500 if self.fail_release else 200, 'latency_ms': 42,
                                    'url': url, 'simulated': True}
        return client

    def handle(self, request):
        path, method = request.url.path, request.method
        if self.denied:
            return httpx.Response(403, json={})
        if method != 'GET':
            self.mutations.append((method, path))
        if path == '/v1/projects/update-envs':
            body = json.loads(request.content)
            self.apps[body['project']]['envs'] = body['variables']
            return httpx.Response(200, json={})
        if path == '/v1/projects':
            if method == 'POST':
                body = json.loads(request.content)
                if body['name'] in self.apps:
                    return httpx.Response(409)
                self.apps[body['name']] = {'_id': body['name'], 'project_id': body['name'],
                    'type': body['platform'], 'planID': body['planID'], 'status': 'Created', 'envs': [], 'scale': 1}
                return httpx.Response(201, json={'project': self.apps[body['name']]})
            return httpx.Response(200, json={'projects': list(self.apps.values())})
        if path == '/v1/domains':
            return httpx.Response(201 if method == 'POST' else 200,
                json={'domain': {'_id': 'domain-demo'}, 'domains': []})
        parts = path.strip('/').split('/')
        if len(parts) >= 3 and parts[1] == 'projects':
            name = parts[2]
            if name not in self.apps:
                return httpx.Response(404)
            tail = '/'.join(parts[3:])
            if not tail:
                return httpx.Response(200, json={'project': self.apps[name]})
            if tail == 'sources':
                return httpx.Response(200, json={'sourceID': 'demo-source-' + name})
            if tail == 'releases':
                if method == 'POST':
                    self.releases_by_app[name] = [{'_id': 'demo-release-' + name,
                        'state': 'FAILED' if self.fail_release else 'READY', 'port': 3000}]
                    self.apps[name]['status'] = 'Running'
                    return httpx.Response(200, json={'releaseID': 'demo-release-' + name})
                return httpx.Response(200, json={'releases': self.releases_by_app.get(name, []),
                    'currentRelease': self.releases_by_app.get(name, [{'_id':'previous-release'}])[0]['_id']})
            if tail == 'logs':
                return httpx.Response(200, json=[{'type': 'stderr', 'datetime': '2026-10-23T09:15:00Z',
                    'message': 'Database connection timeout; password=demo-db-password'}])
            if tail == 'events':
                return httpx.Response(200, json={'events': [{'reason': 'Started', 'message': 'Container started'}]})
            if tail.startswith('metrics/'):
                return httpx.Response(200, json={'end': 1792747200,
                    'result': [{'applet': 'demo-1', 'values': [[1792747140, '0.42'], [1792747200, '0.55']]}]})
            if tail == 'actions/scale':
                scale = json.loads(request.content)['scale']
                self.apps[name]['scale'] = scale
                self.apps[name]['status'] = 'Running' if scale else 'Stopped'
                return httpx.Response(200, json={})
            return httpx.Response(200, json={})
        if path.startswith('/api/v1/zones'):
            return httpx.Response(200, json={'status': 'success', 'data': []})
        return httpx.Response(200, json={})


class DemoPlanner:
    name = 'offline-demo'

    def plan(self, request, context):
        if request['intent'] == 'deploy':
            steps = []
            if not context['exists']:
                steps.append({'action': 'create_app', 'title': 'ایجاد برنامه',
                    'reason': 'برنامه در حساب وجود ندارد؛ پلن انتخاب‌شده توسط شما استفاده می‌شود.',
                    'args': {'platform': context['platform'], 'plan_id': request['plan_id']}})
            steps.append({'action': 'deploy', 'title': 'انتشار نسخهٔ جدید',
                'reason': 'بستهٔ بازبینی‌شده آپلود و نسخه ساخته و بررسی می‌شود.',
                'args': {'platform': context['platform'], 'port': context['port'] or (80 if context['platform']=='static' else 3000)}})
            if request.get('domain'):
                steps.append({'action': 'attach_domain', 'title': 'اتصال دامنه',
                    'reason': 'پس از آماده‌شدن نسخه، دامنهٔ انتخابی اضافه می‌شود.', 'args': {'domain': request['domain']}})
            return Plan(summary='پروژه برای انتشار بررسی شد. این اجرا در محیط نمایشی آفلاین است.',
                evidence=['inspection: پلتفرم از فایل‌های پروژه تشخیص داده شد.'],
                limitations=['داده‌ها و پاسخ سلامت در حالت دمو شبیه‌سازی شده‌اند.'], steps=steps)
        return Plan(summary='در نمونهٔ نمایشی، لاگ به timeout اتصال پایگاه داده اشاره می‌کند.',
            evidence=['logs: Database connection timeout'],
            limitations=['علت قطعی از این داده‌ها مشخص نیست.', 'واحد دادهٔ مصرف منابع از پاسخ نمونه قابل اثبات نیست.'],
            questions=['دسترسی شبکه و وضعیت پایگاه داده را بررسی کنید؛ ری‌استارت بدون شواهد بیشتر پیشنهاد نمی‌شود.'])
