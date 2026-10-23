import hashlib
import inspect
import json
import os
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlsplit

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .liara import LiaraError
from .models import Answer, HostingOption, InputValues, Plan, RunRequest, Step
from .presentation import change_review, operation_report
from .project import inspect_project, snapshot
from .security import redact, workspace_path
from .repair import deployment_files, stage_repair, validate_edits
from .store import Store, now
from .uploads import upload_root, upload_id, upload_modes
from .inputs import InputVault, allowed_name, discover_inputs, input_label, read_source_env
from .diagnostics import analyse
from .hosting import catalog, recommend
from .verification import discover_routes, collect_samples, verify_stability, compare_results


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class State(TypedDict, total=False):
    id: str
    request: dict
    supplied_plan: dict | None
    context: dict
    artifact: dict
    plan: dict
    plan_digest: str
    approved: bool
    status: str
    results: list
    verification: dict
    events: list
    error: str
    demo: bool
    env_fingerprints: dict
    review: dict
    repair: dict
    parent_id: str
    pending_release: dict | None
    control_generation: int
    model_usage: list
    build_progress: dict


ARGUMENTS = {
    'create_network': {'name'},
    'repair_deploy': {'files', 'platform', 'port'},
    'create_app': {'platform', 'plan_id'}, 'deploy': {'platform', 'port'}, 'restart': set(),
    'scale': {'scale'}, 'resize': {'plan_id'}, 'update_env': {'env_refs'},
    'attach_domain': {'domain'}, 'enable_ssl': {'domain'}, 'create_zone': {'zone'},
    'create_record': {'zone', 'record'}, 'redeploy': {'source_id', 'platform', 'port'},
}
PLATFORMS = {'node', 'next', 'nuxt', 'python', 'django', 'flask', 'php', 'laravel', 'docker', 'static', 'dotnet', 'go'}


def validate_plan(plan, context, artifact, request):
    if request['intent'] == 'deploy' and not plan.questions and not plan.inputs:
        if sum(step.action == 'deploy' for step in plan.steps) != 1:
            raise ValueError('طرح انتشار باید دقیقاً یک مرحلهٔ آپلود و ساخت نسخه داشته باشد.')
        if not context['exists'] and not any(step.action == 'create_app' for step in plan.steps):
            raise ValueError('طرح اولین انتشار باید ایجاد برنامه را هم پوشش دهد.')
    if request['intent'] == 'repair' and not plan.questions and not plan.inputs:
        if sum(step.action == 'repair_deploy' for step in plan.steps) != 1:
            raise ValueError('طرح اصلاح انتشار باید دقیقاً یک بستهٔ اصلاحی داشته باشد.')
        if any(step.action not in {'create_network','create_app', 'update_env', 'repair_deploy'} for step in plan.steps):
            raise ValueError('طرح اصلاح انتشار فقط ایجاد برنامه و انتشار بستهٔ اصلاحی را می‌پذیرد.')
    created, deployed, network_created = context['exists'], False, False
    for step in plan.steps:
        args = step.args
        expected=ARGUMENTS[step.action]
        if (set(args)-expected-({'bundle_plan_id','network'} if step.action=='create_app' else set()) or not expected<=set(args)):
            raise ValueError('پارامترهای اقدام معتبر نیست: ' + step.action)
        if step.action=='create_network':
            if created or network_created or args['name']!=request.get('network_name') or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}',args['name']):
                raise ValueError('ساخت شبکه فقط برای سایت تازه و نام بازبینی‌شده مجاز است.')
            network_created=True
            continue
        if step.action == 'create_app':
            if created or deployed or not request['plan_id'] or args['plan_id'] != request['plan_id'] or args['platform'] != context['platform']:
                raise ValueError('ایجاد برنامه به پلن صریح و نبود برنامه نیاز دارد.')
            created = True
            choice=context.get('hosting_choice')
            if context.get('hosting_required'):
                if not choice or choice.get('available') is False:
                    raise ValueError('گزینهٔ میزبانی قابل تأیید نیست؛ اطلاعات واقعی یا مسیر دستی اپراتور لازم است.')
                wanted={'bundle_plan_id':choice.get('bundle_plan_id',''),'network':choice.get('network') or ('@new-network' if choice.get('network_name') else '')}
                if not wanted['bundle_plan_id'] or not wanted['network'] or any(args.get(k)!=v for k,v in wanted.items()):
                    raise ValueError('بستهٔ امکانات یا اتصال خصوصی با انتخاب بازبینی‌شده یکسان نیست.')
                if wanted['network']=='@new-network' and not network_created:
                    raise ValueError('ساخت شبکه باید پیش از ایجاد میزبانی در طرح تأیید شود.')
        elif not created and step.action not in {'create_zone', 'create_record'}:
            raise ValueError('برنامه هنوز ایجاد نشده است.')
        if 'platform' in args and (not isinstance(args['platform'], str) or args['platform'] not in PLATFORMS):
            raise ValueError('پلتفرم انتخاب‌شده پشتیبانی نمی‌شود.')
        if 'port' in args and (type(args['port']) is not int or not 1 <= args['port'] <= 65535):
            raise ValueError('پورت نامعتبر است.')
        if step.action == 'repair_deploy':
            if request['intent'] != 'repair':
                raise ValueError('ویرایش فایل به درخواست صریح اصلاح انتشار نیاز دارد.')
            validate_edits(args['files'], context.get('deployment_files', {}))
        if step.action in {'deploy', 'repair_deploy'}:
            if not artifact or args['platform'] != context['platform'] or args['port'] != context['port']:
                raise ValueError('بسته، پلتفرم یا پورت با پروژهٔ بررسی‌شده تطابق ندارد.')
            deployed = True
        if step.action == 'scale' and (type(args['scale']) is not int or args['scale'] not in (0, 1)):
            raise ValueError('scale باید صفر یا یک باشد.')
        if 'plan_id' in args and (not isinstance(args['plan_id'], str) or not args['plan_id'].strip()):
            raise ValueError('شناسهٔ پلن لازم است؛ قیمت یا پلن حدس زده نمی‌شود.')
        if step.action=='resize' and context.get('hosting_catalog'):
            if not any(option['id']==args['plan_id'] and option.get('available') is not False for option in context.get('hosting_options',[])):
                raise ValueError('گزینهٔ تغییر ظرفیت در کاتالوگ حساب نیست؛ شناسه حدس زده نمی‌شود.')
        for key in ('domain', 'zone'):
            if key in args:
                value = args[key]
                if not isinstance(value, str) or not re.fullmatch(r'(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[A-Za-z]{2,63}', value):
                    raise ValueError('دامنه معتبر نیست؛ از نام ASCII یا punycode استفاده کنید.')
        if step.action == 'update_env':
            refs = args['env_refs']
            if not isinstance(refs, dict) or not refs or len(refs) > 100:
                raise ValueError('نام متغیرهای محیطی لازم است.')
            for key, ref in refs.items():
                if (not isinstance(ref, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key)
                        or not re.fullmatch(r'LYARA_DEPLOY_[A-Za-z0-9_]+', ref)):
                    raise ValueError('مقادیر محرمانه باید با ارجاع LYARA_DEPLOY_* مشخص شوند.')
        if step.action == 'create_record':
            record = args['record']
            if (not isinstance(record, dict) or set(record) - {'name', 'type', 'ttl', 'contents'}
                    or not {'name', 'type', 'contents'} <= set(record)
                    or not isinstance(record['type'], str)
                    or record['type'] not in {'A', 'AAAA', 'CNAME', 'ALIAS', 'TXT', 'MX', 'SRV', 'CAA'}
                    or not isinstance(record['name'], str) or not 1 <= len(record['name']) <= 253
                    or not isinstance(record['contents'], list) or not 1 <= len(record['contents']) <= 32):
                raise ValueError('رکورد DNS معتبر نیست.')
            if 'ttl' in record and (type(record['ttl']) is not int or record['ttl'] not in {120,180,300,600,900,1800,3600,7200,18000,43200,86400,172800,432000}):
                raise ValueError('TTL رکورد DNS معتبر نیست.')
            for content in record['contents']:
                if (not isinstance(content, dict) or not 1 <= len(content) <= 16
                        or any(not isinstance(k, str) or not 1 <= len(k) <= 63 or type(v) not in (str, int)
                               or len(str(v)) > 4096 for k, v in content.items())):
                    raise ValueError('محتوای رکورد DNS معتبر نیست.')
        if step.action == 'redeploy' and (not isinstance(args['source_id'], str) or not args['source_id']):
            raise ValueError('شناسهٔ سورس قبلی لازم است.')


class Assistant:
    def __init__(self, workspace, data_dir, cloud, planner):
        self.workspace, self.data_dir = Path(workspace).resolve(), Path(data_dir).resolve()
        if self.workspace.is_relative_to(self.data_dir):
            raise ValueError('پوشهٔ داده نباید خود فضای کاری یا والد آن باشد.')
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cloud, self.planner = cloud, planner
        self.store = Store(self.data_dir / 'operations.sqlite')
        if getattr(cloud,'sandbox',False) or getattr(cloud,'local_lab',False):
            # Explicit test transports only; production accounts are never enrolled automatically.
            for item in cloud.projects().get('projects',[]):
                name=item.get('project_id') or item.get('name')
                if name and name not in self.store.controls()['managed_projects']:
                    self.store.set_managed(name,True)
        self.vault = InputVault(self.data_dir)
        self.observed_values = {}
        self.connection = sqlite3.connect(self.data_dir / 'checkpoints.sqlite', check_same_thread=False)
        self.checkpointer = SqliteSaver(self.connection)
        # ponytail: one local worker serializes runs; use per-run workers and PostgreSQL for multi-tenant hosting.
        self.lock = threading.RLock()
        builder = StateGraph(State)
        for name in ['prepare', 'plan', 'approve', 'execute', 'verify']:
            builder.add_node(name, getattr(self, '_' + name))
        builder.add_edge(START, 'prepare')
        builder.add_edge('prepare', 'plan')
        builder.add_conditional_edges('plan', lambda s: 'approve' if s['status'] == 'awaiting_approval' else 'verify',
                                      {'approve': 'approve', 'verify': 'verify'})
        builder.add_conditional_edges('approve', lambda s: 'execute' if s.get('approved') else END,
                                      {'execute': 'execute', END: END})
        builder.add_edge('execute', 'verify')
        builder.add_edge('verify', END)
        self.graph = builder.compile(checkpointer=self.checkpointer)

    def close(self):
        self.cloud.http.close()
        self.connection.close()

    def _save(self, state, phase, text, **updates):
        state = dict(state, **updates)
        state['events'] = [*state.get('events', []), {'at': now(), 'phase': phase, 'text': text}]
        state = redact(state, self._secret_values(state['request']['project']))
        self.store.save(state['id'], state)
        return state

    def source_path(self,source):
        return upload_root(self.data_dir,source) if source.startswith('upload:') else workspace_path(self.workspace,source)

    def source_exclusions(self,source):
        return () if source.startswith('upload:') else (self.data_dir,)

    def source_modes(self,source):
        return upload_modes(self.data_dir,source) if source.startswith('upload:') else None

    def _secret_values(self,project=None):
        return [self.cloud.token,getattr(self.planner,'key',''),*self.vault.secrets(project),*(self.observed_values.get(project,[]) if project is not None else [value for values in self.observed_values.values() for value in values])]

    def observe(self,project):
        available=[]
        try:
            entries=self.cloud.project(project,raw=True).get('project',{}).get('envs',[])
            available=[entry['key'] for entry in entries if entry.get('key') and entry.get('value')]
            known=[entry['value'] for entry in entries if isinstance(entry.get('value'),str) and entry['value']]
            self.observed_values[project]=list(set([*self.observed_values.get(project,[]),*known]))
        except LiaraError:
            pass
        observations=self.cloud.observe(project)
        observations['project']['configured_env']=available
        return redact(observations,self._secret_values(project))

    def _env_value(self,project,reference):
        return self.vault.resolve(project,reference)

    def hosting_options(self):
        if getattr(self.cloud, 'local_lab', False):
            return [{'id':'local-plan','title':'آزمایش روی همین دستگاه','cost':'بدون هزینهٔ میزبانی ابری'}]
        if getattr(self.planner, 'name', '') == 'offline-demo':
            return [{'id':'demo-plan','title':'نمونهٔ نمایشی','cost':'بدون خرید یا تغییر حساب'}]
        try:
            raw = os.getenv('LYARA_HOSTING_OPTIONS', '[]')
            if len(raw) > 20000:
                raise ValueError()
            values = json.loads(raw)
            if not isinstance(values, list) or len(values) > 20:
                raise ValueError()
            options = [HostingOption.model_validate(item).model_dump(exclude_defaults=True) for item in values]
            if len({(item['id'],item.get('bundle_plan_id',''),item.get('network') or item.get('network_id',''),item.get('network_name','')) for item in options}) != len(options):
                raise ValueError()
            return options
        except (ValueError, TypeError) as error:
            raise ValueError('گزینه‌های هزینهٔ میزبانی در تنظیمات اتصال معتبر نیستند.') from error

    def controls(self):
        return self.store.controls()

    def emergency(self,enabled,expected_generation):
        return self.store.set_emergency(enabled,expected_generation=expected_generation)

    def manage(self,project,enabled,expected_generation):
        return self.store.set_managed(project,enabled,expected_generation=expected_generation)

    def hosting_catalog(self,inspection=None,project=''):
        current=catalog(self.cloud,self.hosting_options())
        options=recommend(current,inspection or {})
        expanded=[]
        for position,option in enumerate(options):
            networks=[{'id':option.get('network_id') or option.get('network'),'name':''}] if option.get('network_id') or option.get('network') else current.get('networks',[])
            choices=networks or [{'id':'','name':''}]
            for network in choices:
                item=dict(option,network=network['id'])
                if option.get('origin')=='liara-api':
                    item['title']='پیشنهاد برای شروع' if option.get('recommended') else 'گزینهٔ '+str(position+1)
                if not network['id'] and current.get('networks_available') and not option.get('network_name'):
                    item['network_name']='lyara-'+project[:48] if project else ''
                if network.get('name') and len(networks)>1:item['title']+=' · '+network['name']
                item['choice_id']=digest({k:item.get(k,'') for k in ['id','bundle_plan_id','network','network_name']})
                expanded.append(item)
        return dict(current,options=expanded)

    def _select_hosting(self,request,choice_id,context):
        if not choice_id:return request
        choice=next((item for item in context.get('hosting_options',[]) if item.get('choice_id')==choice_id),None)
        if not choice or choice.get('available') is False:
            raise ValueError('گزینهٔ انتخابی در پیشنهاد فعلی نیست؛ بررسی تازه لازم است.')
        return dict(request,plan_id=choice['id'],bundle_plan_id=choice.get('bundle_plan_id',''),
                    network=choice.get('network',''),network_name=choice.get('network_name',''))

    def handoff(self,run_id):
        from .handoff import coding_handoff
        state=self.get(run_id)
        return coding_handoff(state,self._secret_values(state['request']['project']))

    def _permission(self,state,creating=False):
        self.store.assert_mutation(state['request']['project'],state.get('control_generation',-1),creating=creating)

    def _targets(self,project,observations,artifact=None,url=''):
        if not artifact:
            current=observations.get('releases',{}).get('data',{}).get('currentRelease')
            previous=self.store.verified_run(project,current)
            artifact=(previous or {}).get('artifact')
        if artifact and Path(artifact.get('path','')).is_file():
            targets=discover_routes(artifact,observations,base_url=url)
            if targets or not (getattr(self.cloud,'sandbox',False) or getattr(self.cloud,'local_lab',False)):
                return targets
        if getattr(self.cloud,'sandbox',False) or getattr(self.cloud,'local_lab',False):
            return [{'path':'/','expected_statuses':list(range(200,300)),'evidence':'Explicit local test transport'}]
        return []

    def monitor_probe(self,project,observations,health_url=''):
        url=health_url or 'https://'+project+'.liara.run'
        targets=self._targets(project,observations,url=url)
        return self._probe(url,targets)

    def _probe(self,url,targets):
        if not targets:
            return {'available':False,'healthy':None,'probe_attempted':False,'url':url,
                    'error':'مسیر GET قابل بررسی از سورس مشخص نیست؛ درخواست خودکار ارسال نشد.'}
        parsed=urlsplit(url)
        return self.cloud.health(parsed.scheme+'://'+parsed.netloc+(targets[0]['path'] if targets[0]['path']!='/' else ''))

    def _prepare(self, state):
        request = state['request']
        observations = self.observe(request['project'])
        for name, observation in observations.items():
            observation.update(id='before.' + name, collected_at=now(), source='Liara API', sha256=digest(observation))
        item = observations['project']
        exists = item['available']
        questions, inspection, artifact = [], {}, {}
        connected=getattr(self.cloud,'connected',True)
        missing_inputs, available_inputs, env_refs, candidates = [], [], {}, []
        if not connected:
            questions.append('برای انتشار، اتصال حساب میزبانی لازم است.')
        elif not exists and item.get('status') != 404:
            questions.append('اطلاعات برنامه قابل دریافت نیست؛ ابتدا دسترسی یا ارتباط لیارا را اصلاح کنید.')
        platform, port = request.get('platform'), request.get('port')
        if request['intent'] in {'deploy', 'repair'}:
            root = self.source_path(request['source'])
            try:
                inspection = inspect_project(root)
            except ValueError:
                if request['intent'] != 'repair':
                    raise
                inspection = {'platform': '', 'port': None, 'hints': [
                    'manifest نامعتبر است؛ فایل خام محدود برای اصلاح بررسی می‌شود.']}
            project_data = item.get('data', {}).get('project', {})
            platform = platform or project_data.get('type') or inspection['platform']
            port = port or inspection['port']
            if connected and not exists and not request['plan_id']:
                questions.append('برای سایت جدید باید هزینهٔ میزبانی را انتخاب کنی؛ تا انتخاب و تأیید تو هیچ خریدی انجام نمی‌شود.')
            source_values = read_source_env(root)
            if request['source'].startswith('upload:'):
                uploaded=self.vault.values('upload-'+upload_id(request['source']))
                source_values={**{name:item['value'] for name,item in uploaded.items()},**source_values}
            if source_values:
                self.vault.put(request['project'],source_values,'env-file')
            saved = self.vault.values(request['project'])
            declared,candidates = discover_inputs(root,self._secret_values(request['project']),excluded_roots=self.source_exclusions(request['source']))
            host_names = set(item.get('configured_env',[]))
            available_inputs = [{'name':name,'source':item['source']} for name,item in saved.items()]
            available_inputs += [{'name':name,'source':'host'} for name in sorted(host_names-set(saved))]
            missing_inputs = [input_label(name) for name in declared if name not in saved and name not in host_names]
            env_refs = {name:self.vault.reference(request['project'],name) for name in saved}
            artifact = snapshot(root, self.data_dir / 'artifacts', excluded_roots=self.source_exclusions(request['source']),secrets=self._secret_values(request['project']),file_modes=self.source_modes(request['source']))
        elif not exists:
            questions.append('برای این عملیات ابتدا برنامه‌ای موجود را انتخاب کنید.')
        if request['intent'] not in {'deploy','repair'}:
            current=observations.get('releases',{}).get('data',{}).get('currentRelease')
            previous=self.store.verified_run(request['project'],current)
            artifact=(previous or {}).get('artifact',{})
            if not artifact and request['source']!='.':
                try:
                    root=self.source_path(request['source'])
                    if request['source'].startswith('upload:'):
                        uploaded=self.vault.values('upload-'+upload_id(request['source']))
                        if uploaded:self.vault.put(request['project'],{name:item['value'] for name,item in uploaded.items()},'env-file')
                    artifact=snapshot(root,self.data_dir/'artifacts',excluded_roots=self.source_exclusions(request['source']),secrets=self._secret_values(request['project']),file_modes=self.source_modes(request['source']))
                except (OSError,ValueError):
                    artifact={}
        controls=self.controls()
        if request['intent'] in {'deploy','repair','operate'}:
            if controls['stopped']:
                questions.append('تغییرات متوقف‌اند؛ پس از فعال‌کردن، پیشنهاد تازه بساز.')
            elif exists and request['project'] not in controls['managed_projects']:
                questions.append('این سایت فقط خواندنی است؛ برای تغییر، اجازهٔ مدیریت همین سایت را فعال کن و بررسی تازه بگیر.')
        context = {'observations': observations, 'exists': exists, 'inspection': inspection,
                   'connection_required':not connected,
                   'platform': platform, 'port': port, 'questions': questions,
                   'input_requests':missing_inputs,'available_inputs':available_inputs,
                   'env_refs':env_refs,'configuration_candidates':candidates}
        context['controls']=controls
        context['diagnostics']=analyse(observations,secrets=self._secret_values(request['project']))
        context['hosting_options']=[]
        if request['intent'] in {'deploy','repair','operate'}:
            sandbox=getattr(self.cloud,'sandbox',False) or getattr(self.cloud,'local_lab',False)
            if sandbox:
                context['hosting_options']=self.hosting_options()
            else:
                hosting=self.hosting_catalog(inspection,request['project'])
                context['hosting_catalog']=hosting
                context['hosting_options']=hosting['options']
                context['hosting_required']=not exists
                matches=[option for option in hosting['options'] if option.get('available') is not False and option['id']==request['plan_id']
                    and (not request.get('bundle_plan_id') or option.get('bundle_plan_id')==request['bundle_plan_id'])
                    and (not request.get('network') or option.get('network')==request['network'])
                    and (not request.get('network_name') or option.get('network_name')==request['network_name'])]
                if len(matches)==1:
                    choice=matches[0]
                    context['hosting_choice']=choice
                    request=dict(request,bundle_plan_id=choice.get('bundle_plan_id',''),network=choice.get('network',''),network_name=choice.get('network_name',''))
                elif not exists and request['plan_id']:
                    context['questions'].append('هزینه و اتصال انتخاب‌شده قطعی نیست؛ یکی از گزینه‌های بررسی را انتخاب کن.')
                context['hosting_selection_required']=not exists and (not request['plan_id'] or len(matches)!=1)
                if not exists and len(matches)==1 and (not choice.get('bundle_plan_id') or not (choice.get('network') or choice.get('network_name')) or choice.get('available') is False):
                    context['questions'].append('اطلاعات میزبانی کامل یا در دسترس نیست؛ اتصال حساب یا تنظیم جایگزین اپراتور باید اصلاح شود.')
        if artifact:
            context['deployment_files'], context['omitted_files'] = deployment_files(artifact, self._secret_values(request['project']),include_backend=request['intent']=='repair',observations=observations)
            context['repair_scope'] = 'Repair existing backend defects evidenced by logs/source, or deployment blockers. No unrelated features, frontend changes, schema migrations or weakened authentication. Dependencies install during build, never on this host.'
            context['setup_policy'] = 'Resolve runtime from these files. When PORT is freely configurable, choose an appropriate value and label it a configuration decision. Never ask the person for ports, frameworks or internal identifiers.'
        active=observations.get('releases',{}).get('data',{}).get('currentRelease')
        context['previous_release']=self.store.known_release(request['project'],active)
        url=request.get('health_url') or 'https://'+(request.get('domain') or request['project']+'.liara.run')
        context['probe_targets']=self._targets(request['project'],observations,artifact,url)
        context['health']=self._probe(url,context['probe_targets']) if exists else {'available':False,'healthy':None}
        context['health'].update(id='before.health',collected_at=now(),source='HTTP health probe')
        context['before_samples']=collect_samples(self.cloud,url,context['probe_targets'],samples=3,timeout=30) if exists and context['probe_targets'] else {'samples':[],'summary':{},'complete':False}
        if state.get('parent_id'):
            previous=self.store.get(state['parent_id'])
            if previous.get('build_progress'):
                context['previous_build']=previous['build_progress']
                observations['build_logs']={'available':True,'data':previous['build_progress'].get('build',{}),'id':'before.build_logs'}
                context['diagnostics']=analyse(observations,secrets=self._secret_values(request['project']))
        if request['source'].startswith('upload:'):
            try:
                metadata=self.data_dir/'uploads'/upload_id(request['source'])/'upload.json'
                provenance=json.loads(metadata.read_text(encoding='utf8')).get('github')
                if provenance:context['github']=provenance
            except (OSError,ValueError,TypeError):
                context['source_limitations']=['نسخهٔ محلی سورس در دسترس نیست؛ برای اصلاح فایل‌ها دوباره آن را بفرست.']
        return self._save(state, 'inspect', 'پروژه و شواهد اولیه بررسی شد.', context=context, artifact=artifact,request=request)

    def _plan(self, state):
        if state['context']['questions'] or state['context'].get('input_requests'):
            plan = Plan(summary='این اطلاعات برای اجرای سایت لازم است.', questions=state['context']['questions'],inputs=state['context'].get('input_requests',[]))
        elif state.get('supplied_plan') is not None:
            plan = Plan.model_validate(state['supplied_plan'])
        elif self.planner is None:
            plan = Plan(summary='شواهد جمع‌آوری شد. برای اقدام، یک طرح مشخص ارائه دهید.',
                        limitations=['مدل داخلی تنظیم نشده؛ ابزار MCP می‌تواند طرح ارائه دهد.'])
        else:
            try:
                plan = self.planner.plan(state['request'], state['context'])
            finally:
                if hasattr(self.planner,'drain_usage'):
                    usage=self.planner.drain_usage()
                    state=self._save(state,'model','مصرف مدل ثبت شد؛ هزینهٔ نامعلوم صفر محسوب نمی‌شود.',model_usage=[*state.get('model_usage',[]),*usage])
        if not plan.questions and not plan.inputs and state['context'].get('hosting_required'):
            choice=state['context'].get('hosting_choice') or {}
            steps=[]
            for step in plan.steps:
                if step.action=='create_app':
                    args=dict(step.args,bundle_plan_id=choice.get('bundle_plan_id',''),network=choice.get('network') or '@new-network')
                    if choice.get('network_name') and not choice.get('network') and not any(s.action=='create_network' for s in plan.steps):
                        steps.append(Step(action='create_network',title='ساخت اتصال خصوصی سایت',reason='شبکهٔ موجودی انتخاب نشده؛ هزینهٔ مستقل شبکه در API اعلام نشده است.',args={'name':choice['network_name']}))
                    step=step.model_copy(update={'args':args})
                steps.append(step)
            plan=plan.model_copy(update={'steps':steps})
        if plan.inputs:
            if plan.steps or any(not allowed_name(item.name) for item in plan.inputs):
                raise ValueError('درخواست اطلاعات سایت معتبر نیست.')
            available = {item['name'] for item in state['context'].get('available_inputs',[])}
            inputs = [item.model_dump() for item in plan.inputs if item.name not in available]
            if not inputs:
                raise ValueError('اطلاعات خواسته‌شده از قبل موجود است؛ بررسی تازه لازم است.')
            state = dict(state,context=dict(state['context'],input_requests=inputs))
        if not plan.questions and not plan.inputs:
            refs=state['context'].get('env_refs',{})
            if refs and state['request']['intent'] in {'deploy','repair'}:
                # Settings are part of the same reviewed plan, applied before the build.
                steps=[step for step in plan.steps if step.action!='update_env']
                explicit={key:value for step in plan.steps if step.action=='update_env' for key,value in step.args.get('env_refs',{}).items()}
                insertion=next((i+1 for i,step in enumerate(steps) if step.action=='create_app'),0)
                steps.insert(insertion,Step(action='update_env',title='تنظیم اطلاعات لازم سایت',reason='استفاده از اطلاعات موجود یا واردشده برای همین سایت.',args={'env_refs':{**refs,**explicit}}))
                plan=plan.model_copy(update={'steps':steps})
        if not plan.questions and not plan.inputs:
            deployment = next((step for step in plan.steps if step.action in {'deploy','repair_deploy'}), None)
            if deployment:
                context = dict(state['context'])
                for field in ['platform','port']:
                    if not context.get(field):
                        context[field] = deployment.args.get(field)
                state = dict(state, context=context)
        validate_plan(plan, state['context'], state['artifact'], state['request'])
        repair = {}
        if not plan.questions and not plan.inputs:
            step = next((step for step in plan.steps if step.action == 'repair_deploy'), None)
            if step:
                artifact, repair = stage_repair(state['artifact'], step.args['files'],
                    state['context']['deployment_files'], self.data_dir / 'artifacts', self._secret_values(state['request']['project']))
                state = dict(state, artifact=artifact)
                state['context']=dict(state['context'],probe_targets=self._targets(state['request']['project'],state['context']['observations'],artifact))
        if plan.questions or plan.inputs:
            status = 'needs_input'
        else:
            status = 'awaiting_approval' if plan.steps else 'observed'
        quotes={}
        if state['context'].get('hosting_catalog'):
            current_bundle=state['context']['observations']['project'].get('data',{}).get('project',{}).get('bundlePlanID')
            for step in plan.steps:
                if step.action=='resize':
                    choices=[item for item in state['context']['hosting_options'] if item['id']==step.args['plan_id'] and item.get('bundle_plan_id')==current_bundle and item.get('available') is not False]
                    if not choices:raise ValueError('هزینهٔ ظرفیت تازه با بستهٔ امکانات فعلی قابل تطبیق نیست؛ پیشنهاد تازه لازم است.')
                    quotes[step.args['plan_id']]=choices[0]
            state['context']=dict(state['context'],billing_quotes=quotes)
        serialized = redact(plan.model_dump(), self._secret_values(state['request']['project']))
        env_fingerprints = {}
        for step in plan.steps:
            if step.action == 'update_env':
                for reference in step.args['env_refs'].values():
                    value=self._env_value(state['request']['project'],reference)
                    env_fingerprints[reference] = hashlib.sha256(value.encode()).hexdigest()
        fingerprint = digest({'request': state['request'], 'plan': serialized, 'artifact': state['artifact'],
                              'env_fingerprints': env_fingerprints,'control_generation':state.get('control_generation')})
        return self._save(state, 'plan', 'طرح و پیامدهای عملیات آماده است.', plan=serialized,
                          plan_digest=fingerprint, env_fingerprints=env_fingerprints, status=status,
                          context=state['context'], artifact=state['artifact'], repair=repair, review=change_review(state['request'],plan,state['context']))

    def _approve(self, state):
        decision = interrupt({'run_id': state['id'], 'plan_digest': state['plan_digest'], 'plan': state['plan']})
        approved = (isinstance(decision, dict) and decision.get('approved') is True
                    and decision.get('plan_digest') == state['plan_digest'])
        return self._save(state, 'approval', 'طرح تأیید شد.' if approved else 'طرح رد شد؛ اقدامی اجرا نشد.',
                          approved=approved, status='running' if approved else 'rejected')

    def _execute(self, state):
        if not state.get('approved'):
            raise ValueError('اقدام بدون تأیید مجاز نیست.')
        plan = Plan.model_validate(state['plan'])
        validate_plan(plan, state['context'], state['artifact'], state['request'])
        self._permission(state,creating=not state['context']['exists'])
        for reference,fingerprint in state.get('env_fingerprints',{}).items():
            if hashlib.sha256(self._env_value(state['request']['project'],reference).encode()).hexdigest()!=fingerprint:
                raise ValueError('اطلاعات سایت پس از پیشنهاد تغییر کرده است؛ پیشنهاد تازه بساز.')
        results = []
        for index, step in enumerate(plan.steps):
            try:
                result = self._step(state, index, step)
                results.append(redact(result, self._secret_values(state['request']['project'])))
                state = self._save(state, 'execute', step.title + ' انجام شد.', results=results)
            except (LiaraError, ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
                message = str(error) if not isinstance(error, (KeyError, TypeError)) else 'پاسخ لیارا با قرارداد مورد انتظار مطابقت ندارد؛ وضعیت را پیش از تکرار بررسی کنید.'
                status = 'unknown' if getattr(error, 'uncertain', False) else 'failed'
                return self._save(state, 'error', message, results=results, status=status, error=message)
        return dict(state, results=results)

    def _step(self, state, index, step):
        name, args = state['request']['project'], step.args
        key = state['id'] + ':' + str(index)
        permission={'project':name,'generation':state.get('control_generation',-1),
                    'creating':not state['context']['exists'] and step.action in {'create_app','create_network'}}
        def once(suffix,fn):
            def guarded():
                self.cloud.write_guard.check=lambda:self.store.assert_mutation(**permission)
                try:return redact(fn(),self._secret_values(name))
                finally:self.cloud.write_guard.check=None
            return self.store.write_once(key+':'+suffix,guarded,permission=permission)
        if step.action=='create_network':
            result=once('network',lambda:self.cloud.create_network(args['name']))
            networks=self.cloud.hosting_catalog().get('networks',{}).get('data',{}).get('networks',[])
            found=[n for n in networks if n.get('name')==args['name'] and n.get('_id')]
            if len(found)!=1:raise ValueError('شبکه درخواست شد اما شناسهٔ یکتای آن قابل بررسی نیست؛ ساخت خودکار تکرار نمی‌شود.')
            return {'network_id':found[0]['_id'],'response':result}
        if step.action == 'create_app':
            network=args.get('network','')
            if network=='@new-network':
                network=next((r['network_id'] for r in reversed(state.get('results',[])) if r.get('network_id')),None)
                if not network:raise ValueError('شبکهٔ تأییدشده هنوز شناسایی نشده است.')
            def create():
                if state['context'].get('hosting_required'):
                    current=self.hosting_catalog(state['context'].get('inspection'),name)
                    previous=state['context']['hosting_choice']
                    choice=next((item for item in current['options'] if item['id']==args['plan_id'] and item.get('bundle_plan_id')==args.get('bundle_plan_id')),None)
                    if not choice or choice.get('available') is False or choice.get('price')!=previous.get('price') or choice.get('cost')!=previous.get('cost'):
                        raise ValueError('هزینه یا موجودی میزبانی تغییر کرده یا قابل بررسی نیست؛ پیشنهاد تازه لازم است.')
                    return self.cloud.create(name,args['platform'],args['plan_id'],args['bundle_plan_id'],network)
                return self.cloud.create(name,args['platform'],args['plan_id'])
            result=once('create',create)
            self.store.register_created(name,state['control_generation'])
            return result
        if step.action in {'deploy', 'redeploy', 'repair_deploy'}:
            if step.action in {'deploy', 'repair_deploy'}:
                artifact = state['artifact']
                archive = Path(artifact['path'])
                if hashlib.sha256(archive.read_bytes()).hexdigest() != artifact['sha256']:
                    raise ValueError('بستهٔ پروژه پس از بازبینی تغییر کرده است؛ طرح جدید بسازید.')
                state.update(self._save(state,'upload','در حال ارسال فایل‌های تأییدشده.'))
                source = once('upload', lambda: self.cloud.upload(name, archive))
                source_id = source['sourceID']
                state.update(self._save(state,'upload','ارسال فایل‌ها تمام شد؛ ساخت نسخه هنوز تأیید نشده است.'))
            else:
                source_id = args['source_id']
            release = once('release', lambda: self.cloud.release(name, source_id, args['platform'], args['port']))
            state['pending_release']={'sourceID':source_id,'releaseID':release['releaseID'],'step_index':index}
            self._save(state,'release','شناسهٔ نسخه ثبت شد؛ آماده‌شدن آن جدا بررسی می‌شود.',pending_release=state['pending_release'])
            def progress(value):
                clean=redact(value,self._secret_values(name))
                phase=clean.get('state','UNKNOWN')
                labels={'READY':'نسخه آماده است؛ پایداری و مسیرهای سایت بررسی می‌شوند.','DEPLOYING':'نسخه در حال راه‌اندازی است.','BUILDING':'نسخه در حال ساخت است.','PENDING':'نسخه در صف آماده‌سازی است.'}
                state.update(self._save(state,'build',labels.get(phase,'وضعیت ساخت نسخه دریافت شد.'),build_progress=clean))
            kwargs={'progress':progress} if 'progress' in inspect.signature(self.cloud.wait_release).parameters else {}
            verified = self.cloud.wait_release(name, release['releaseID'],**kwargs)
            state['pending_release']=None
            return {'sourceID': source_id, 'releaseID': release['releaseID'], 'release': verified}
        if step.action == 'restart':
            return once('restart', lambda: self.cloud.restart(name))
        if step.action == 'scale':
            return once('scale', lambda: self.cloud.scale(name, args['scale']))
        if step.action == 'resize':
            def resize():
                quote=state['context'].get('billing_quotes',{}).get(args['plan_id'])
                if state['context'].get('hosting_catalog'):
                    if not quote:raise ValueError('هزینهٔ بازبینی‌شده برای این تغییر موجود نیست.')
                    current=self.hosting_catalog(state['context'].get('inspection'),name)
                    choice=next((item for item in current['options'] if item['id']==args['plan_id'] and item.get('bundle_plan_id')==quote.get('bundle_plan_id')),None)
                    if not choice or choice.get('available') is False or choice.get('price')!=quote.get('price') or choice.get('cost')!=quote.get('cost'):
                        raise ValueError('هزینه یا موجودی تغییر کرده است؛ تغییر ظرفیت ارسال نشد و پیشنهاد تازه لازم است.')
                return self.cloud.resize(name,args['plan_id'])
            return once('resize',resize)
        if step.action == 'update_env':
            changes = {}
            for variable, reference in args['env_refs'].items():
                value=self._env_value(name,reference)
                if hashlib.sha256(value.encode()).hexdigest() != state.get('env_fingerprints', {}).get(reference):
                    raise ValueError('مقدار محرمانه پس از آماده‌شدن طرح تغییر کرده است؛ طرح جدید بسازید.')
                changes[variable] = value
            return once('env', lambda: self.cloud.update_env(name, changes))
        if step.action == 'attach_domain':
            result = once('domain', lambda: self.cloud.attach_domain(name, args['domain']))
            return {'attachment': result, 'status': self.cloud.domain_status(result['domain']['_id']),
                    'next_step': 'رکورد DNS اعلام‌شده را بررسی کنید؛ تغییر NS در ثبت‌کننده ممکن است دستی باشد.'}
        if step.action == 'enable_ssl':
            return once('ssl', lambda: self.cloud.enable_ssl(args['domain']))
        if step.action == 'create_zone':
            return once('zone', lambda: self.cloud.create_zone(args['zone']))
        if step.action == 'create_record':
            return once('record', lambda: self.cloud.create_record(args['zone'], args['record']))
        raise ValueError('اقدام پشتیبانی نمی‌شود.')

    def _verify(self, state):
        if state['status'] == 'needs_input':
            return state
        after = self.observe(state['request']['project'])
        for key, observation in after.items():
            observation.update(id='after.' + key, collected_at=now(), source='Liara API', sha256=digest(observation))
        steps = state.get('plan', {}).get('steps', [])
        domain_step = next((step['args']['domain'] for step in reversed(steps)
                            if step['action'] in {'attach_domain', 'enable_ssl'}), '')
        host = state['request'].get('domain') or domain_step or state['request']['project'] + '.liara.run'
        url = state['request'].get('health_url') or ('https://' + host)
        targets=state.get('context',{}).get('probe_targets',[])
        health = self._probe(url,targets)
        deployment=next((result for result in reversed(state.get('results',[])) if result.get('releaseID')),None)
        stability=None
        if deployment and targets:
            stability=verify_stability(self.cloud,state['request']['project'],deployment['releaseID'],url,targets,
                timeout=45,interval=0 if getattr(self.cloud,'sandbox',False) or getattr(self.cloud,'local_lab',False) else 2)
            samples=stability
        elif targets:
            samples=collect_samples(self.cloud,url,targets,samples=3,timeout=30)
        else:
            samples={'samples':[],'summary':{},'complete':False}
        comparison=compare_results(state.get('context',{}).get('before_samples',{}),samples,stability)
        checks = []
        project = after['project'].get('data', {}).get('project', {})
        for action, field, argument in [('resize', 'planID', 'plan_id'), ('scale', 'scale', 'scale')]:
            last = next((step for step in reversed(steps) if step['action'] == action), None)
            if last:
                checks.append({'action': action, 'expected': last['args'][argument], 'observed': project.get(field),
                               'matched': project.get(field) == last['args'][argument]})
        env_refs = {}
        for step in steps:
            if step['action'] == 'update_env':
                env_refs.update(step['args']['env_refs'])
        if env_refs:
            try:
                raw = self.cloud.project(state['request']['project'], raw=True)['project']
                values = {item['key']: item.get('value') for item in raw.get('envs', [])}
                for variable, reference in env_refs.items():
                    actual = values.get(variable)
                    matched = (isinstance(actual, str) and hashlib.sha256(actual.encode()).hexdigest()
                               == state['env_fingerprints'][reference])
                    checks.append({'action': 'update_env', 'variable': variable, 'matched': matched})
            except (LiaraError, KeyError, TypeError):
                checks.append({'action': 'update_env', 'matched': False, 'note': 'مقدار نهایی قابل بررسی نبود.'})
        configuration_ok = all(check['matched'] for check in checks)
        action_checks=[]
        results=state.get('results',[])
        for index,step in enumerate(steps):
            action,args=step['action'],step['args']
            if action in {'resize','scale','update_env'} and index<len(results):
                continue
            matched=None
            note='API پذیرش درخواست را گزارش کرده است؛ اثر این اقدام از داده‌های موجود قابل اثبات نیست.'
            observed={}
            if index>=len(results):
                matched=False
                note='اجرای این مرحله تأیید نشده است؛ خودکار تکرار نمی‌شود.'
            elif action=='create_app':
                matched=project.get('type')==args['platform'] and project.get('planID')==args['plan_id']
                if state['context'].get('hosting_required'):
                    network=args.get('network')
                    if network=='@new-network':network=next((r['network_id'] for r in results if r.get('network_id')),None)
                    observed_network=project.get('network')
                    if isinstance(observed_network,dict):observed_network=observed_network.get('_id')
                    matched=matched and project.get('bundlePlanID')==args.get('bundle_plan_id') and observed_network==network
                note='پلتفرم و پلن برنامه از وضعیت نهایی خوانده شد.'
            elif action=='create_network':
                observed=self.cloud.hosting_catalog().get('networks',{}).get('data',{}).get('networks',[])
                matched=any(item.get('_id')==results[index].get('network_id') and item.get('name')==args['name'] for item in observed)
                note='نام و شناسهٔ شبکه از فهرست حساب بررسی شد.'
            elif action in {'deploy','redeploy','repair_deploy'}:
                current=after['releases'].get('data',{}).get('currentRelease')
                matched=current==results[index].get('releaseID') if current else None
                observed={'currentRelease':current,'requestedRelease':results[index].get('releaseID')}
                note='نسخهٔ فعال باید با شناسهٔ نسخهٔ همین عملیات یکسان باشد.'
            elif action in {'create_zone','create_record','attach_domain','enable_ssl'}:
                try:
                    if action=='create_zone':
                        observed=self.cloud.zones()
                        matched=any(item.get('name')==args['zone'] for item in observed.get('data',[]))
                        note='وجود ناحیه از API خوانده شد؛ فعال‌بودن nameserver در ثبت‌کننده جداست.'
                    elif action=='create_record':
                        observed=self.cloud.records(args['zone'])
                        matched=any(all(item.get(key)==value for key,value in args['record'].items()) for item in observed.get('data',[]))
                        note='رکورد از API خوانده شد؛ انتشار عمومی DNS ممکن است زمان ببرد.'
                    else:
                        observed=self.cloud.domains(state['request']['project'])
                        domain=next((item for item in observed.get('domains',[]) if item.get('name')==args['domain']),None)
                        matched=bool(domain and domain.get('project',{}).get('project_id')==state['request']['project'])
                        note='اتصال دامنه از API بررسی شد؛ نیاز DNS ثبت‌کننده را در نتیجهٔ اقدام ببینید.'
                        if action=='enable_ssl':
                            matched=bool(matched and health.get('healthy') and urlsplit(health.get('url','')).hostname==args['domain'])
                            note='اتصال دامنه و پاسخ HTTPS همان دامنه بررسی شد؛ صرف پذیرش صدور گواهی کافی نیست.'
                except (LiaraError,ValueError,TypeError,KeyError):
                    matched=None
                    note='خواندن وضعیت نهایی این اقدام ممکن نبود؛ نتیجه قطعی نیست.'
            action_checks.append({'index':index,'action':action,'matched':matched,'note':note,'observed':redact(observed)})
        configuration_ok=configuration_ok and all(item['matched'] is True for item in action_checks)
        sampled_ok=bool(samples.get('complete') and samples.get('summary',{}).get('success')==samples.get('summary',{}).get('total') and samples.get('summary',{}).get('total',0)>0)
        if deployment:configuration_ok=configuration_ok and bool(stability and stability['stable'])
        verification = {'observations': after, 'health': health,
                        'samples':samples,'stability':stability,'comparison':comparison,
                        'diagnostics':analyse(after,secrets=self._secret_values(state['request']['project'])),
                        'configuration_checks': checks,'action_checks':action_checks,
                        'outcome': 'healthy' if health.get('healthy') and sampled_ok and configuration_ok else 'inconclusive',
                        'note': 'پاسخ HTTP به‌تنهایی رفع علت اصلی خطا را ثابت نمی‌کند.'}
        stopped = bool(steps and steps[-1]['action'] == 'scale' and steps[-1]['args']['scale'] == 0
                       and after['project'].get('data', {}).get('project', {}).get('scale') == 0)
        if stopped:
            verification.update(outcome='stopped', note='خاموش‌شدن در وضعیت برنامه تأیید شد؛ در دسترس نبودن HTTP در این حالت انتظار می‌رود.')
        if state['status'] in {'failed', 'unknown'}:
            status = state['status']
        elif state.get('approved'):
            status = 'completed' if ((health.get('healthy') and sampled_ok) or stopped) and configuration_ok else 'needs_attention'
        else:
            status = 'observed'
        return self._save(state, 'verify', 'وضعیت پس از بررسی ثبت شد.', verification=verification, status=status)

    def _invoke(self, run_id, value):
        try:
            self.graph.invoke(value, {'configurable': {'thread_id': run_id}})
        except Exception as error:
            current = self.store.get(run_id)
            message = str(error) if isinstance(error, (LiaraError, ValueError, RuntimeError, OSError)) else 'پردازش درخواست متوقف شد؛ نوع خطا: ' + type(error).__name__
            self._save(current, 'error', message, status='failed', error=message)
        return self.store.get(run_id)

    def new(self, request, plan=None):
        request = RunRequest.model_validate(request)
        run_id = str(uuid.uuid4())
        state = {'id': run_id, 'request': request.model_dump(), 'supplied_plan': plan.model_dump() if plan else None,
                 'status': 'preparing', 'events': [], 'results': [],
                 'control_generation':self.controls()['generation'],'model_usage':[],
                 'demo': getattr(self.planner, 'name', '') == 'offline-demo' or bool(getattr(self.cloud,'local_lab',False))}
        state = redact(state, self._secret_values(request.project))
        self.store.save(run_id, state)
        return state

    def process(self, run_id):
        with self.lock:
            return self._invoke(run_id, self.store.get(run_id))

    def start(self, request, plan=None):
        return self.process(self.new(request, plan)['id'])

    def approve(self, run_id, plan_digest, approved):
        if type(approved) is not bool:
            raise ValueError('تصمیم تأیید باید بله یا خیر باشد.')
        with self.lock:
            state = self.store.get(run_id)
            if state.get('plan_digest') != plan_digest:
                raise ValueError('شناسهٔ طرح با طرح بازبینی‌شده مطابقت ندارد.')
            if state['status'] != 'awaiting_approval':
                return state
            if approved:
                try:self._permission(state,creating=not state.get('context',{}).get('exists',True))
                except PermissionError as error:return self._save(state,'control',str(error),status='invalidated',approved=False,error=str(error))
            return self._invoke(run_id, Command(resume={'approved': approved, 'plan_digest': plan_digest}))

    def resume(self, run_id):
        with self.lock:
            state = self.store.get(run_id)
            checkpoint = self.graph.get_state({'configurable': {'thread_id': run_id}})
            if state.get('plan',{}).get('steps') and (checkpoint.next or state['status'] in {'preparing','running','awaiting_approval'}):
                try:self._permission(state,creating=not state.get('context',{}).get('exists',True))
                except PermissionError as error:return self._save(state,'control',str(error),status='invalidated',approved=False,error=str(error))
            if state['status'] == 'awaiting_approval':
                return state
            if checkpoint.next:
                return self._invoke(run_id, None)
            if not checkpoint.values and state['status'] == 'preparing':
                return self._invoke(run_id, state)
            return state

    def get(self, run_id):
        state=self.store.get(run_id)
        if state.get('plan',{}).get('steps') and state['status'] in {'awaiting_approval','running','preparing'}:
            if state.get('control_generation')!=self.controls()['generation']:
                return dict(state,status='invalidated',approved=False,error='مجوز این پیشنهاد تغییر کرده؛ درخواست تازه لازم است. اقدام قبلاً ارسال‌شده ممکن است ادامه داشته باشد.')
        return state

    def recheck(self,run_id):
        with self.lock:
            state=self.get(run_id)
            if state['status'] not in {'unknown','failed','needs_attention','completed','observed'}:
                raise ValueError('بررسی دوباره فقط پس از توقف یا پایان بررسی مجاز است.')
            pending=state.get('pending_release')
            if pending:
                try:
                    verified=self.cloud.wait_release(state['request']['project'],pending['releaseID'],timeout=0)
                except LiaraError as error:
                    return self._save(state,'recheck',str(error),status='unknown' if error.uncertain else 'failed',error=str(error))
                if len(state['results'])!=pending['step_index']:
                    raise ValueError('ترتیب ثبت اقدامات با نسخهٔ در انتظار تطابق ندارد؛ تغییر خودکار متوقف شد.')
                state=dict(state,status='running',pending_release=None,error=None,
                    results=[*state['results'],{'sourceID':pending['sourceID'],'releaseID':pending['releaseID'],'release':verified}])
            state=self._verify(state)
            return self._save(state,'recheck','فقط وضعیت دوباره خوانده شد؛ هیچ اقدام نوشتنی تکرار نشد.')

    def new_inputs(self,run_id,submission):
        submission=InputValues.model_validate(submission)
        with self.lock:
            previous=self.get(run_id)
            if previous['status']!='needs_input':
                raise ValueError('این درخواست منتظر اطلاعات نیست.')
            requested={item['name'] for item in previous['context'].get('input_requests',[])}
            if not requested or set(submission.values)!=requested:
                raise ValueError('فقط اطلاعات خواسته‌شده را کامل وارد کن.')
            self.vault.put(previous['request']['project'],submission.values,'saved')
            request=dict(previous['request'])
            if submission.plan_id is not None:request['plan_id']=submission.plan_id
            request=self._select_hosting(request,submission.hosting_choice,previous['context'])
            if submission.message:request['message']+='\n'+submission.message
            state=self.new(RunRequest.model_validate(request))
            return self._save(state,'inputs','اطلاعات سایت ذخیره شد؛ پیشنهاد تازه آماده می‌شود.',parent_id=run_id)

    def new_answer(self,run_id,answer):
        answer=Answer.model_validate(answer)
        previous=self.get(run_id)
        if previous['status']!='needs_input':
            raise ValueError('فقط به عملیاتی که منتظر اطلاعات است می‌توان پاسخ داد؛ برای تغییر طرح درخواست تازه بسازید.')
        request=dict(previous['request'])
        request.update(answer.model_dump(exclude_none=True,exclude={'message','hosting_choice'}))
        request=self._select_hosting(request,answer.hosting_choice,previous['context'])
        questions='\n'.join(previous['plan'].get('questions',[]))
        request['message']+='\n\nپرسش‌های دستیار:\n'+questions+'\n\nپاسخ کاربر:\n'+answer.message
        state=self.new(RunRequest.model_validate(request))
        return self._save(state,'answer','پاسخ شما با حفظ درخواست قبلی ثبت شد؛ طرح تازه به تأیید جدا نیاز دارد.',parent_id=run_id)

    def answer(self,run_id,answer):
        return self.process(self.new_answer(run_id,answer)['id'])

    def report(self,run_id):
        return redact(operation_report(self.get(run_id),self.store.actions(run_id)),self._secret_values(self.get(run_id)['request']['project']))

    def new_repair(self, run_id):
        previous = self.get(run_id)
        if previous['status'] not in {'failed', 'needs_attention', 'observed', 'completed'}:
            raise ValueError('ابتدا نتیجهٔ بررسی یا انتشار فعلی را دریافت کنید.')
        request = dict(previous['request'], intent='repair')
        request['message'] = ('باگ موجود بک‌اند یا مانع انتشار را بر اساس شواهد اصلاح کن؛ قابلیت تازه، تغییر رابط و تغییر ساختار داده نده.\n'
            + previous['request']['message'] + '\nخطای بررسی قبلی: ' + str(previous.get('error', '')))[-6000:]
        state = self.new(RunRequest.model_validate(request))
        return self._save(state, 'repair', 'بررسی اصلاح انتشار شروع شد؛ تغییرات فایل پیش از انتشار به تأیید تازه نیاز دارد.', parent_id=run_id)

    def repair(self, run_id):
        return self.process(self.new_repair(run_id)['id'])

    def new_recovery(self,run_id):
        before=self.get(run_id)
        previous=before.get('review',{}).get('previous_source')
        if (not previous or before['status'] not in {'completed','needs_attention','failed'}
                or not any(step['action'] in {'deploy','redeploy','repair_deploy'} for step in before.get('plan',{}).get('steps',[]))):
            raise ValueError('بازگشت خودکار قابل پیشنهاد نیست؛ سورس فعال قبلی ثبت نشده یا نتیجهٔ عملیات هنوز نامشخص است.')
        request=dict(before['request'],intent='operate',platform=previous['platform'],port=previous['port'],
                     message='طرح بازگشت فقط سورس برنامه به نسخهٔ پیشین را برای تأیید تازه آماده کن. داده، env، پلن و DNS تغییر نکنند.')
        plan=Plan(summary='بازگشت فقط سورس به نسخهٔ ثبت‌شدهٔ پیش از عملیات. داده‌ها و تنظیمات برنمی‌گردند؛ سالم‌بودن نسخهٔ قبلی تضمین نمی‌شود.',
            steps=[{'action':'redeploy','title':'انتشار دوبارهٔ سورس پیشین','reason':'درخواست صریح بازگشت سورس با تأیید جدید.',
                    'args':{key:previous[key] for key in ['source_id','platform','port']}}])
        state=self.new(RunRequest.model_validate(request),plan)
        return self._save(state,'recovery','طرح بازگشت سورس با تأیید مستقل آماده می‌شود.',parent_id=run_id)

    def recovery(self,run_id):
        return self.process(self.new_recovery(run_id)['id'])

    def runs(self):
        return [self.get(state['id']) for state in self.store.list()]
