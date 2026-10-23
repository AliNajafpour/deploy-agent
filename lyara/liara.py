import time
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from .security import public_https, redact


class LiaraError(RuntimeError):
    def __init__(self, status, message, uncertain=False):
        super().__init__(message)
        self.status, self.uncertain = status, uncertain


def segment(value):
    return quote(str(value), safe='')


class LiaraClient:
    def __init__(self, token, team_id='', transport=None):
        if any(c in token for c in '\r\n'):
            raise ValueError('LIARA_API_TOKEN نامعتبر است.')
        self.token, self.team_id = token, team_id
        self.connected=bool(token)
        self.write_guard=threading.local()
        self.http = httpx.Client(headers={'Authorization': 'Bearer ' + token} if token else {},
                                 timeout=45, follow_redirects=False, transport=transport, trust_env=False)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.http.close()

    def _request(self, method, path, *, dns=False, params=None, **kwargs):
        if not self.connected:raise LiaraError(503,'برای انتشار، اتصال حساب میزبانی لازم است.')
        if method!='GET' and getattr(self.write_guard,'check',None):self.write_guard.check()
        query = dict(params or {})
        if self.team_id:
            query['teamID'] = self.team_id
        base = 'https://dns-service.liara.ir' if dns else 'https://api.liara.ir'
        try:
            response = self.http.request(method, base + path, params=query, **kwargs)
        except httpx.HTTPError as error:
            raise LiaraError(0, 'ارتباط با لیارا برقرار نشد؛ نتیجهٔ عملیات نوشتنی ممکن است نامشخص باشد.',
                             uncertain=method != 'GET') from error
        if not 200 <= response.status_code < 300:
            status = response.status_code
            messages = {401: 'کلید لیارا نامعتبر یا منقضی است.', 403: 'اجازهٔ دسترسی به این داده یا عملیات وجود ندارد.',
                        404: 'برنامه یا منبع پیدا نشد.', 409: 'منبع از قبل وجود دارد یا عملیات در تعارض است.',
                        429: 'محدودیت تعداد درخواست لیارا؛ کمی بعد دوباره بررسی کنید.'}
            raise LiaraError(status, messages.get(status, 'لیارا خطای HTTP ' + str(status) + ' برگرداند.'),
                             uncertain=method != 'GET' and status >= 500)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as error:
            raise LiaraError(502, 'پاسخ لیارا JSON معتبر نیست.', uncertain=method != 'GET') from error

    def projects(self):
        if not self.connected:return {'projects':[]}
        return redact(self._request('GET', '/v1/projects'), [self.token])

    def project(self, name, raw=False):
        data = self._request('GET', '/v1/projects/' + segment(name))
        return data if raw else redact(data, [self.token])

    def releases(self, name, timeout=45):
        return self._request('GET', '/v1/projects/' + segment(name) + '/releases', params={'page': 1, 'count': 30},timeout=timeout)

    def hosting_catalog(self):
        result={}
        for key,path in [('account','/v1/me'),('billing','/v1/billing'),('networks','/v1/networks')]:
            try:
                data=self._request('GET',path,timeout=12)
                if key=='account':
                    team=data.get('team') or {}
                    data={'plans':data.get('plans',{}),'team':{'balance':team.get('balance') if isinstance(team,dict) else None}}
                elif key=='billing':
                    data={'user':{'balance':data.get('user',{}).get('balance')}}
                elif key=='networks':
                    data={'networks':[{k:n.get(k) for k in ['_id','name']} for n in data.get('networks',[]) if isinstance(n,dict)]}
                result[key]={'available':True,'data':data}
            except (LiaraError,ValueError,TypeError,AttributeError) as error:
                result[key]={'available':False,'error':str(error) if isinstance(error,LiaraError) else 'پاسخ کاتالوگ معتبر نیست.'}
        return result

    def create_network(self,name):
        return self._request('POST','/v1/networks',json={'networkName':name})

    def build_logs(self,release_id,since=None,timeout=12):
        return redact(self._request('GET','/v2/releases/'+segment(release_id)+'/build-logs',
            params={'since':since} if since is not None else {},timeout=timeout),[self.token])

    def release_status(self,release_id,timeout=12):
        return self._request('GET','/v1/releases/'+segment(release_id),timeout=timeout)

    def observe(self, name):
        since = int(time.time()) - 900
        paths = {'project': ('', {}), 'logs': ('/logs', {'since': datetime.now(timezone.utc).__sub__(timedelta(minutes=15)).isoformat()}),
                 'events': ('/events', {'start': since, 'end': int(time.time())}),
                 'releases': ('/releases', {'page': 1, 'count': 10}),
                 'cpu': ('/metrics/cpu', {'since': since}), 'memory': ('/metrics/memory', {'since': since})}
        result = {}
        for key, (suffix, params) in paths.items():
            try:
                data = self._request('GET', '/v1/projects/' + segment(name) + suffix, params=params)
                result[key] = {'available': True, 'data': redact(data, [self.token])}
            except LiaraError as error:
                result[key] = {'available': False, 'status': error.status, 'error': str(error)}
        return result

    def create(self, name, platform, plan_id, bundle_plan_id='', network=''):
        body={'name':name,'platform':platform,'planID':plan_id}
        if bundle_plan_id:body['bundlePlanID']=bundle_plan_id
        if network:body['network']=network
        return self._request('POST', '/v1/projects',json=body)

    def upload(self, name, archive):
        with Path(archive).open('rb') as source:
            return self._request('POST', '/v2/projects/' + segment(name) + '/sources',
                                 files={'file': ('source.tar.gz', source, 'application/gzip')})

    def release(self, name, source_id, platform, port):
        return self._request('POST', '/v2/projects/' + segment(name) + '/releases',
                             json={'sourceID': source_id, 'type': platform, 'port': port})

    def wait_release(self, name, release_id, timeout=180, interval=3, progress=None):
        deadline = time.monotonic() + timeout
        while True:
            data = self.releases(name,timeout=min(12,max(0.1,deadline-time.monotonic())))
            release = next((r for r in data.get('releases', []) if r.get('_id') == release_id), None)
            state = str((release or {}).get('state', '')).upper()
            current = data.get('currentRelease')
            if progress:
                detail={}
                try:detail=self.release_status(release_id,timeout=min(12,max(0.1,deadline-time.monotonic()))).get('release',{})
                except LiaraError:pass
                try:
                    build=self.build_logs(release_id,timeout=min(12,max(0.1,deadline-time.monotonic())))
                except LiaraError as error:
                    build={'available':False,'error':str(error)}
                if isinstance(build.get('buildOutput'),list):
                    build['buildOutput']=[{k:str(row.get(k,''))[:2000] for k in ['line','stream','createdAt']} for row in build['buildOutput'][-100:] if isinstance(row,dict)]
                queue=detail.get('queue')
                progress({'release_id':release_id,'state':'QUEUED' if type(queue) is int and queue>0 else state or detail.get('state') or 'UNKNOWN',
                          'queue':queue if type(queue) is int else None,'status':detail.get('status'),
                          'active_release':current,'build':build})
            if state in {'READY', 'SUCCESS', 'SUCCEEDED', 'RUNNING'} and (not current or current == release_id):
                return {'verified': True, 'release': release, 'active_confirmed': current == release_id}
            if state in {'FAILED', 'ERROR', 'BUILD_FAILED', 'CRASHED', 'CANCELED', 'CANCELLED','TIMEDOUT','UNHEALTHY'}:
                raise LiaraError(422, 'ساخت یا اجرای نسخه شکست خورد؛ لاگ‌ها را بررسی کنید.')
            if time.monotonic() >= deadline:
                raise LiaraError(408, 'نسخه در زمان مقرر آماده نشد؛ وضعیت نامشخص را دوباره بررسی کنید.',uncertain=True)
            time.sleep(interval)

    def restart(self, name):
        return self._request('POST', '/v1/projects/' + segment(name) + '/actions/restart')

    def scale(self, name, scale):
        return self._request('POST', '/v1/projects/' + segment(name) + '/actions/scale', json={'scale': scale})

    def resize(self, name, plan_id):
        return self._request('POST', '/v1/projects/' + segment(name) + '/resize', json={'planID': plan_id})

    def update_env(self, name, changes):
        project = self.project(name, raw=True)['project']
        variables = {}
        for item in project.get('envs', []):
            if item.get('encrypted') and (not item.get('value') or item.get('value') in {'***', '********'}):
                raise ValueError('مقدار قبلی بعضی متغیرها مخفی است؛ برای جلوگیری از حذف یا خراب‌شدن، ویرایش متوقف شد.')
            variables[item['key']] = item['value']
        variables.update(changes)
        self._request('POST', '/v1/projects/update-envs', json={'project': name,
                      'variables': [{'key': k, 'value': v} for k, v in variables.items()]})
        return {'updated_keys': list(changes)}

    def domains(self, name):
        return self._request('GET', '/v1/domains', params={'project': name})

    def attach_domain(self, name, domain):
        return self._request('POST', '/v1/domains', json={'name': domain, 'project': name, 'type': 'PROJECT'})

    def domain_status(self, domain_id):
        return self._request('GET', '/v1/domains/' + segment(domain_id) + '/check')

    def enable_ssl(self, domain):
        return self._request('POST', '/v1/domains/provision-ssl-certs', json={'domain': domain})

    def zones(self):
        return self._request('GET', '/api/v1/zones', dns=True)

    def create_zone(self, name):
        return self._request('POST', '/api/v1/zones', dns=True, json={'name': name})

    def records(self, zone):
        return self._request('GET', '/api/v1/zones/' + segment(zone) + '/dns-records', dns=True)

    def create_record(self, zone, record):
        return self._request('POST', '/api/v1/zones/' + segment(zone) + '/dns-records', dns=True, json=record)

    def health(self, url, timeout=10):
        attempted = False
        try:
            destination = public_https(url)
            pinned = httpx.URL(url).copy_with(host=destination['ip'])
            # Never reuse the authenticated cloud client for website requests.
            with httpx.Client(timeout=max(0.01,min(10,timeout)), follow_redirects=False, trust_env=False) as client:
                started = time.monotonic()
                attempted = True
                with client.stream('GET', pinned, headers={'Host': httpx.URL(url).host},
                                   extensions={'sni_hostname': httpx.URL(url).host}) as response:
                    return {'available': True, 'probe_attempted': True, 'url': url, 'status': response.status_code,
                            'healthy': 200 <= response.status_code < 300,
                            'latency_ms': round((time.monotonic() - started) * 1000)}
        except (OSError, ValueError, httpx.HTTPError):
            return {'available': False, 'probe_attempted': attempted, 'url': url, 'healthy': False,
                    'error': 'اتصال HTTPS به سایت موفق نبود.' if attempted else 'مقصد HTTPS قابل بررسی یا مجاز نیست.'}
