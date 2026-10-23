import argparse
import json
import os
import sys
import time
from pathlib import Path

from .models import RunRequest
from .runtime import create_assistant


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Lyara: deploy and operate Liara with durable human approval')
    parser.add_argument('--demo', action='store_true', help='Explicit offline sandbox; never touches cloud resources')
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', default=8787, type=int)
    doctor = commands.add_parser('doctor', help='Check local or production setup without calling cloud/model APIs')
    doctor.add_argument('--json', action='store_true')
    doctor.add_argument('--production', action='store_true')
    commands.add_parser('mcp')
    commands.add_parser('projects')
    commands.add_parser('runs')
    commands.add_parser('controls', help='Read emergency stop, managed sites and permission generation')
    commands.add_parser('hosting', help='Read hosting catalog and account availability without guessing prices')
    for name in ['emergency', 'manage']:
        sub = commands.add_parser(name)
        if name == 'manage':
            sub.add_argument('project')
        choice = sub.add_mutually_exclusive_group(required=True)
        choice.add_argument('--enable', action='store_true')
        choice.add_argument('--disable', action='store_true')
    github = commands.add_parser('github', help='Import a public GitHub repository pinned to an exact commit; no deployment')
    github.add_argument('url')
    github.add_argument('--ref')
    handoff = commands.add_parser('handoff', help='Print a redacted handoff for a coding agent')
    handoff.add_argument('run_id')
    handoff.add_argument('--format', choices=['json', 'markdown'], default='markdown')
    status = commands.add_parser('status')
    status.add_argument('run_id')
    approve = commands.add_parser('approve')
    approve.add_argument('run_id')
    resume = commands.add_parser('resume')
    resume.add_argument('run_id')
    for name in ['recheck','recovery','report']:
        sub=commands.add_parser(name)
        sub.add_argument('run_id')
        if name=='report':
            sub.add_argument('--format',choices=['json','markdown'],default='json')
    answer=commands.add_parser('answer')
    answer.add_argument('run_id')
    answer.add_argument('--message',required=True)
    answer.add_argument('--port',type=int)
    answer.add_argument('--platform')
    answer.add_argument('--plan-id')
    answer.add_argument('--hosting-choice')
    answer.add_argument('--bundle-plan-id')
    answer.add_argument('--network')
    answer.add_argument('--network-name')
    for name in ['deploy', 'diagnose', 'operate', 'monitor', 'repair']:
        sub = commands.add_parser(name)
        sub.add_argument('project')
        sub.add_argument('--message', default='')
        sub.add_argument('--source', default='.')
        sub.add_argument('--plan-id', default='')
        sub.add_argument('--bundle-plan-id', default='')
        sub.add_argument('--network', default='')
        sub.add_argument('--network-name', default='')
        sub.add_argument('--platform', default='')
        sub.add_argument('--port', type=int)
        sub.add_argument('--domain', default='')
        sub.add_argument('--health-url', default='')
        if name == 'monitor':
            sub.add_argument('--interval', default=60, type=int)
            sub.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.command == 'doctor':
        from .readiness import check_readiness
        result = check_readiness(production=args.production)
        if args.json:
            emit(result)
        else:
            for check in result['checks']:
                print(f"[{check['status']}] {check['label']}: {check['detail']}")
            print('آماده است.' if result['ready'] else 'موارد خطادار را پیش از استقرار برطرف کنید.')
        raise SystemExit(0 if result['ready'] else 1)
    service = None
    try:
        service = create_assistant(args.demo, model=args.command not in {'mcp', 'projects', 'status', 'runs', 'approve', 'monitor',
            'report', 'recheck', 'recovery', 'controls', 'hosting', 'emergency', 'manage', 'github', 'handoff'})
        if args.command == 'serve':
            import uvicorn
            from .web import create_app
            token = os.getenv('LYARA_ACCESS_TOKEN', '')
            if args.host not in {'127.0.0.1', 'localhost', '::1'} and not token:
                raise ValueError('برای دسترسی غیرمحلی LYARA_ACCESS_TOKEN را تنظیم کنید.')
            hosts = {'127.0.0.1', 'localhost', '::1'}
            if os.getenv('LYARA_PUBLIC_HOST'):
                hosts.add(os.environ['LYARA_PUBLIC_HOST'].encode('idna').decode('ascii').lower())
            uvicorn.run(create_app(service, token, hosts), host=args.host, port=args.port)
        elif args.command == 'mcp':
            from .mcp_server import create_mcp
            create_mcp(service).run(transport='stdio')
        elif args.command == 'projects':
            emit(service.cloud.projects())
        elif args.command == 'runs':
            emit(service.runs())
        elif args.command == 'controls':
            emit(service.controls())
        elif args.command == 'hosting':
            emit(service.hosting_catalog())
        elif args.command in {'emergency', 'manage'}:
            if args.command == 'manage':
                RunRequest(message='scope', project=args.project)
            controls = service.controls()
            generation = controls['generation']
            if (args.command == 'emergency' and not args.enable) or (args.command == 'manage' and args.enable):
                if not sys.stdin.isatty():
                    raise ValueError('دادن دسترسی یا پایان توقف به تأیید انسان در ترمینال تعاملی نیاز دارد؛ از رابط وب استفاده کنید.')
                confirmation = f'RESUME {generation}' if args.command == 'emergency' else f'MANAGE {args.project} {generation}'
                if input(f'Type {confirmation} to confirm: ').strip() != confirmation:
                    raise ValueError('تأیید مطابقت نداشت؛ تغییری انجام نشد.')
            result = service.emergency(args.enable, generation) if args.command == 'emergency' else service.manage(args.project, args.enable, generation)
            emit(result)
        elif args.command == 'github':
            from .github_source import import_github
            emit(import_github(args.url, service.data_dir, service.vault, ref=args.ref))
        elif args.command == 'handoff':
            result = service.handoff(args.run_id)
            if args.format == 'markdown':
                from .handoff import handoff_markdown
                print(handoff_markdown(result))
            else:
                emit(result)
        elif args.command == 'status':
            emit(service.get(args.run_id))
        elif args.command == 'resume':
            emit(service.resume(args.run_id))
        elif args.command=='answer':
            emit(service.answer(args.run_id,{'message':args.message,'port':args.port,'platform':args.platform,'plan_id':args.plan_id,
                'hosting_choice':args.hosting_choice,'bundle_plan_id':args.bundle_plan_id,'network':args.network,'network_name':args.network_name}))
        elif args.command=='recheck':
            emit(service.recheck(args.run_id))
        elif args.command=='recovery':
            emit(service.recovery(args.run_id))
        elif args.command=='report':
            report=service.report(args.run_id)
            if args.format=='markdown':
                from .presentation import report_markdown
                print(report_markdown(report))
            else:
                emit(report)
        elif args.command == 'approve':
            state = service.get(args.run_id)
            emit({'id': state['id'], 'project': state['request']['project'], 'plan': state.get('plan'),
                  'artifact': state.get('artifact'), 'plan_digest': state.get('plan_digest')})
            if not sys.stdin.isatty():
                raise ValueError('تأیید به ترمینال تعاملی انسان نیاز دارد؛ از رابط وب استفاده کنید.')
            answer = input('Type the first 12 characters of plan_digest to approve, or no to reject: ').strip()
            emit(service.approve(args.run_id, state['plan_digest'], answer == state['plan_digest'][:12]))
        elif args.command == 'monitor':
            if args.interval < 30:
                raise ValueError('فاصلهٔ پایش باید حداقل ۳۰ ثانیه باشد.')
            RunRequest(message='monitor', project=args.project)
            while True:
                emit({'project': args.project, 'evidence': service.cloud.observe(args.project),
                      'health': service.cloud.health(args.health_url or 'https://' + args.project + '.liara.run')})
                if args.once:
                    break
                time.sleep(args.interval)
        else:
            request = RunRequest(message=args.message or args.command, intent=args.command, project=args.project,
                source=args.source, plan_id=args.plan_id, platform=args.platform, port=args.port,
                bundle_plan_id=args.bundle_plan_id,network=args.network,network_name=args.network_name,
                domain=args.domain, health_url=args.health_url)
            emit(service.start(request))
    except (ValueError, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        pass
    finally:
        if service:
            service.close()
