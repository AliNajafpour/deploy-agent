import hmac
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .liara import LiaraError
from .models import Answer, InputValues, RunRequest, StrictModel
from .presentation import report_markdown
from .uploads import MAX_UPLOAD, import_zip, list_uploads
from .monitoring import Monitoring


class Approval(StrictModel):
    approved: bool
    plan_digest: str = Field(min_length=64, max_length=64)


class WatchSettings(StrictModel):
    enabled: bool
    interval: int = Field(default=60,ge=60,le=86400)
    health_url: str = Field(default='',max_length=1000)


class ControlChange(StrictModel):
    enabled: bool = Field(strict=True)
    expected_generation: int = Field(ge=0,strict=True)


class GitHubSource(StrictModel):
    url: str = Field(min_length=1,max_length=1000)


def create_app(assistant, access_token='', allowed_hosts=None):
    allowed_hosts = allowed_hosts or {'127.0.0.1', 'localhost', '::1'}
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='lyara')
    monitoring = Monitoring(assistant)

    @asynccontextmanager
    async def lifespan(app):
        monitoring.start()
        try:
            yield
        finally:
            monitoring.close()
            executor.shutdown(wait=True)

    app = FastAPI(title='Lyara', docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.monitoring=monitoring
    static = Path(__file__).parent / 'static'

    @app.middleware('http')
    async def security(request: Request, call_next):
        host = request.url.hostname
        if host not in allowed_hosts:
            return JSONResponse({'detail': 'میزبان این درخواست مجاز نیست.'}, status_code=403)
        if request.url.path.startswith('/api/'):
            if access_token and not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + access_token):
                return JSONResponse({'detail': 'دسترسی این نشست تأیید نشده است.'}, status_code=401)
            origin = request.headers.get('origin')
            if origin and urlsplit(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail': 'درخواست از سایت دیگر مجاز نیست.'}, status_code=403)
        response = await call_next(request)
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_body(request,error):
        # Validation errors must never echo submitted credentials.
        return JSONResponse({'detail':'اطلاعات واردشده کامل یا معتبر نیست.'},status_code=422)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({'detail': str(error)}, status_code=400)

    @app.exception_handler(LiaraError)
    async def cloud_error(request, error):
        return JSONResponse({'detail': str(error)}, status_code=502)

    @app.exception_handler(PermissionError)
    async def denied(request,error):
        return JSONResponse({'detail':str(error)},status_code=403)

    @app.get('/')
    def index():
        return FileResponse(static / 'index.html')

    @app.get('/api/config')
    def config():
        return {'provider': getattr(assistant.planner, 'name', 'external-mcp'),
                'model': getattr(assistant.planner, 'model', ''), 'workspace': str(assistant.workspace),
                'hosting_connected': bool(getattr(assistant.cloud,'connected',True)),
                'upload_max_bytes': MAX_UPLOAD,
                'hosting_options': assistant.hosting_options(),
                'capabilities': ['repair', 'backend-repair', 'monitoring', 'github', 'emergency', 'handoff', 'deploy', 'logs', 'events', 'metrics', 'restart', 'env', 'resize', 'dns', 'ssl']}

    @app.get('/api/hosting')
    def hosting():
        return assistant.hosting_catalog()

    @app.get('/api/controls')
    def controls():
        return assistant.controls()

    @app.put('/api/controls')
    async def emergency(change:ControlChange):
        # A separate, fast transaction must not wait behind a model or cloud operation.
        return assistant.emergency(change.enabled,change.expected_generation)

    @app.put('/api/managed/{name}')
    def managed(name:str,change:ControlChange):
        RunRequest(message='scope',project=name)
        return assistant.manage(name,change.enabled,change.expected_generation)

    @app.get('/api/sources')
    def sources():
        return list_uploads(assistant.data_dir)

    @app.post('/api/github',status_code=201)
    async def github(source:GitHubSource):
        from .github_source import import_github
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(import_github,source.url,assistant.data_dir,assistant.vault)

    @app.post('/api/uploads',status_code=201)
    async def upload(request:Request):
        if request.headers.get('content-type','').split(';')[0] not in {'application/zip','application/octet-stream'}:
            raise HTTPException(415,'یک فایل ZIP انتخاب کن.')
        length=request.headers.get('content-length')
        if length and (not length.isdigit() or int(length)>MAX_UPLOAD):
            raise HTTPException(413,'حجم ZIP باید کمتر از ۵۰ مگابایت باشد.')
        with tempfile.TemporaryFile() as archive:
            size=0
            async for chunk in request.stream():
                size+=len(chunk)
                if size>MAX_UPLOAD:raise HTTPException(413,'حجم ZIP باید کمتر از ۵۰ مگابایت باشد.')
                archive.write(chunk)
            archive.seek(0)
            # ZIP parsing and extraction are blocking; no project code is executed.
            from starlette.concurrency import run_in_threadpool
            return await run_in_threadpool(import_zip,archive,request.headers.get('x-file-name',''),assistant.data_dir,assistant.vault)

    @app.get('/api/projects')
    def projects():
        return assistant.cloud.projects()

    @app.get('/api/projects/{name}/evidence')
    def evidence(name: str):
        RunRequest(message='observe', project=name)
        return assistant.observe(name)

    @app.get('/api/monitoring')
    def watches():
        return monitoring.list()

    @app.put('/api/monitoring/{name}')
    def configure_watch(name:str,settings:WatchSettings):
        RunRequest(message='monitor',project=name)
        if settings.enabled:
            if not getattr(assistant.cloud,'connected',True):
                raise ValueError('برای پایش، اتصال حساب میزبانی لازم است.')
            assistant.cloud.project(name)
        return monitoring.configure(name,**settings.model_dump())

    @app.get('/api/incidents')
    def incidents(project:str|None=None):
        if project is not None:RunRequest(message='monitor',project=project)
        return monitoring.incidents(project)

    @app.get('/api/runs')
    def runs():
        return assistant.runs()

    @app.post('/api/runs', status_code=202)
    def start(request: RunRequest):
        state = assistant.new(request)
        executor.submit(assistant.process, state['id'])
        return state

    @app.get('/api/runs/{run_id}')
    def status(run_id: str):
        return assistant.get(run_id)

    @app.post('/api/runs/{run_id}/answer',status_code=202)
    def answer(run_id:str,answer:Answer):
        state=assistant.new_answer(run_id,answer)
        executor.submit(assistant.process,state['id'])
        return state

    @app.post('/api/runs/{run_id}/inputs',status_code=202)
    def inputs(run_id:str,submission:InputValues):
        state=assistant.new_inputs(run_id,submission)
        executor.submit(assistant.process,state['id'])
        return state

    @app.post('/api/runs/{run_id}/repair',status_code=202)
    def repair(run_id: str):
        state = assistant.new_repair(run_id)
        executor.submit(assistant.process, state['id'])
        return state

    @app.post('/api/runs/{run_id}/recovery',status_code=202)
    def recovery(run_id:str):
        state=assistant.new_recovery(run_id)
        executor.submit(assistant.process,state['id'])
        return state

    @app.post('/api/runs/{run_id}/recheck')
    def recheck(run_id:str):
        return assistant.recheck(run_id)

    @app.get('/api/runs/{run_id}/report')
    def report(run_id:str,format:str='json'):
        if format not in {'json','markdown'}:
            raise ValueError('قالب گزارش باید json یا markdown باشد.')
        result=assistant.report(run_id)
        if format=='markdown':
            return PlainTextResponse(report_markdown(result),media_type='text/markdown',
                headers={'Content-Disposition':'attachment; filename="lyara-report-'+result['id']+'.md"'})
        return result

    @app.get('/api/runs/{run_id}/patch')
    def patch(run_id:str):
        state=assistant.get(run_id)
        diff=state.get('repair',{}).get('diff')
        if not diff:raise HTTPException(404,'اصلاحیه‌ای برای این بررسی ثبت نشده است.')
        return PlainTextResponse(diff,media_type='text/x-diff',headers={'Content-Disposition':'attachment; filename="site-fix.patch"'})

    @app.get('/api/runs/{run_id}/handoff')
    def handoff(run_id:str,format:str='markdown'):
        if format not in {'json','markdown'}:raise ValueError('قالب خروجی باید json یا markdown باشد.')
        result=assistant.handoff(run_id)
        if format=='json':return result
        from .handoff import handoff_markdown
        return PlainTextResponse(handoff_markdown(result),media_type='text/markdown',
            headers={'Content-Disposition':'attachment; filename="coding-handoff.md"'})

    @app.post('/api/runs/{run_id}/approval', status_code=202)
    def approve(run_id: str, decision: Approval):
        state = assistant.get(run_id)
        if state.get('status')!='awaiting_approval' or state.get('plan_digest') != decision.plan_digest:
            raise HTTPException(409, 'طرح تغییر کرده یا شناسهٔ آن نادرست است.')
        executor.submit(assistant.approve, run_id, decision.plan_digest, decision.approved)
        return state

    @app.post('/api/runs/{run_id}/resume', status_code=202)
    def resume(run_id: str):
        state = assistant.get(run_id)
        executor.submit(assistant.resume, run_id)
        return state

    app.mount('/static', StaticFiles(directory=static), name='static')
    return app
