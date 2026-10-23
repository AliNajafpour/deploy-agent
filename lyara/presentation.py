"""Deterministic change review and portable, redacted operation reports."""
import json

from .security import redact


IMPACTS={
    'create_network':('ساخت اتصال خصوصی تازه برای سایت','هزینهٔ مستقل ساخت شبکه در API اعلام نشده؛ رایگان فرض نمی‌شود. شبکه پس از ساخت برنامه قابل تغییر نیست.','شبکهٔ ساخته‌شده با توقف طرح حذف نمی‌شود؛ بررسی و حذف احتمالی باید جداگانه انجام شود.'),
    'repair_deploy':('اصلاح مشکل انتشار و آماده‌کردن نسخهٔ تازه','تنظیمات و فایل‌های لازم برای اجرای سایت تغییر می‌کنند؛ جزئیات تغییر فایل‌ها قابل مشاهده است.','کد اصلی تو دست‌نخورده می‌ماند؛ اگر نسخهٔ قبلی ثبت شده باشد با اجازهٔ تازه می‌توانیم آن را برگردانیم.'),
    'create_app':('آماده‌کردن میزبانی سایت با گزینهٔ انتخابی تو','ممکن است هزینه از همین مرحله آغاز شود؛ مبنای قیمت و محدودیت برآورد در انتخاب میزبانی ثبت شده است.','پس از بررسی هزینه و اطلاعات سایت، می‌توانی میزبانی را متوقف یا از حساب خودت حذف کنی.'),
    'deploy':('انتشار این نسخهٔ سایت و بررسی بازشدن آن','ممکن است نسخهٔ تازه خطا داشته باشد یا سایت برای مدتی قطع شود.','اگر نسخهٔ قبلی ثبت شده باشد، با اجازهٔ تازه می‌توانیم آن را برگردانیم.'),
    'redeploy':('برگرداندن نسخهٔ قبلی سایت','فقط کد سایت برمی‌گردد؛ اطلاعات ذخیره‌شده، تنظیمات و آدرس‌های سایت برنمی‌گردند.','برای عوض‌کردن دوبارهٔ نسخه، اجازهٔ تازه لازم است.'),
    'restart':('راه‌اندازی دوبارهٔ سایت','ممکن است سایت لحظه‌ای قطع شود و کارهای در حال انجام که ذخیره نشده‌اند از دست بروند.','کارهای ذخیره‌نشده قابل برگرداندن نیستند؛ پس از راه‌اندازی وضعیت سایت را بررسی می‌کنیم.'),
    'scale':('تغییر روشن یا خاموش‌بودن سایت','اگر سایت خاموش شود دیگر برای بازدیدکننده باز نمی‌شود.','با اجازهٔ تازه می‌توانیم سایت را دوباره روشن یا خاموش کنیم.'),
    'resize':('تغییر ظرفیت میزبانی سایت','هزینه تغییر می‌کند؛ قیمت و شرایط گزینهٔ تازه را پیش از موافقت بررسی کن.','با بررسی ظرفیت مورد نیاز و اجازهٔ تازه می‌توانیم گزینهٔ قبلی را برگردانیم.'),
    'update_env':('تنظیم اطلاعات لازم برای اجرای سایت','اطلاعات محرمانه نمایش داده نمی‌شوند؛ ممکن است سایت برای اعمال تنظیمات دوباره راه‌اندازی شود.','برای برگرداندن تنظیمات، اطلاعات قبلی باید در محل امن موجود باشند؛ اجازهٔ تازه می‌گیریم.'),
    'attach_domain':('وصل‌کردن آدرس دلخواهت به سایت','ممکن است به ورود تو به حسابی که آدرس را از آن خریده‌ای نیاز باشد؛ اتصال فوری نیست.','می‌توانی اتصال آدرس را در حساب میزبانی اصلاح یا حذف کنی.'),
    'enable_ssl':('فعال‌کردن اتصال امن برای آدرس سایت','ثبت درخواست به معنی آماده‌شدن اتصال امن نیست؛ نتیجه را باید جدا بررسی کنیم.','وضعیت اتصال امن را پس از انجام کار دوباره بررسی کن.'),
    'create_zone':('آماده‌کردن مدیریت آدرس سایت','ممکن است لازم باشد وارد حسابی شوی که آدرس را از آن خریده‌ای و تنظیمات اتصال را تأیید کنی.','پیش از جابه‌جایی، تنظیمات اتصال قبلی را نگه دار.'),
    'create_record':('تغییر مسیر اتصال آدرس سایت','اشتباه در این تغییر ممکن است سایت یا ایمیل را قطع کند؛ اعمال آن ممکن است زمان ببرد.','اصلاح یا حذف این تغییر از حساب میزبانی ممکن است؛ بازگردانی خودکار نداریم.'),
}


def change_review(request,plan,context):
    data=context.get('observations',{}).get('releases',{}).get('data',{})
    active=next((r for r in data.get('releases',[]) if r.get('_id')==data.get('currentRelease')),None)
    previous=None
    known=context.get('previous_release') or {}
    if active and (active.get('sourceID') or known.get('sourceID')) and str(active.get('state','')).upper() in {'READY','SUCCESS','SUCCEEDED','RUNNING'}:
        port=active.get('port') or known.get('port')
        platform=active.get('type') or active.get('projectType') or known.get('platform') or context.get('observations',{}).get('project',{}).get('data',{}).get('project',{}).get('type')
        if type(port) is int and 1<=port<=65535 and platform:
            previous={'source_id':active.get('sourceID') or known['sourceID'],'release_id':active['_id'],'platform':platform,'port':port}
    changes=[]
    for step in plan.steps:
        impact,risk,recovery=IMPACTS[step.action]
        if step.action == 'scale':
            impact = 'خاموش‌کردن سایت' if step.args['scale'] == 0 else 'روشن‌کردن سایت'
        elif step.action in {'attach_domain','enable_ssl','create_zone','create_record'}:
            impact += ' — ' + (step.args.get('domain') or step.args.get('zone',''))
        elif step.action in {'create_app','resize'}:
            choice=(context.get('billing_quotes',{}).get(step.args['plan_id']) if step.action=='resize' else context.get('hosting_choice'))
            choice=choice or next((item for item in context.get('hosting_options',[]) if item['id']==step.args['plan_id']),None)
            impact += ' — ' + (choice['title']+'؛ '+choice['cost'] if choice else 'هزینه و شرایط این گزینه در دسترس نیست؛ پیش از موافقت بررسی کن')
        changes.append({'action':step.action,'impact':impact,'risk':risk,'recovery':recovery,'parameters':step.args})
    return {'project':request['project'],'changes':changes,'previous_source':previous,
            'note':'این پیش‌نمایش از اقدام‌های طرح ساخته شده است؛ نتیجهٔ اجرا یا قیمت را پیش‌بینی نمی‌کند.'}


def operation_report(state,actions):
    artifact={key:state.get('artifact',{}).get(key) for key in ['sha256','files','bytes','compressed_bytes']}
    report={'id':state['id'],'parent_id':state.get('parent_id'),'project':state['request']['project'],
        'status':state['status'],'mode':'local or simulated cloud' if state.get('demo') else 'Liara API',
        'request':state['request'],'plan':state.get('plan'),'review':state.get('review'),
        'approval':{'approved':state.get('approved',False),'plan_digest':state.get('plan_digest')},
        'artifact':artifact,'repair':state.get('repair'),'before':state.get('context',{}),'after':state.get('verification'),
        'results':state.get('results',[]),'pending_release':state.get('pending_release'),
        'events':state.get('events',[]),'actions':actions,'error':state.get('error'),
        'model_usage':state.get('model_usage',[]),'control_generation':state.get('control_generation'),
        'build_progress':state.get('build_progress'),
        'limits':['گزارش حاوی شواهد و برداشت مدل است؛ فرضیهٔ مدل به‌تنهایی علت قطعی نیست.',
                  'اطلاعات محرمانهٔ شناخته‌شده پوشانده شده‌اند؛ پیش از انتشار عمومی، متن لاگ‌ها را بازبینی کنید.']}
    return redact(report)


def report_markdown(report):
    lines=['# گزارش عملیات لیارا','',f'برنامه: {report["project"]}',f'شناسه: {report["id"]}',
           f'وضعیت: {report["status"]}',f'محیط: {report["mode"]}','',
           '## درخواست','',report['request']['message'],'','## جمع‌بندی طرح','',
           (report.get('plan') or {}).get('summary','طرحی ثبت نشده است.'),'',
           '## تأیید و شواهد ثبت‌شده','','```json',json.dumps(report,ensure_ascii=False,indent=2),'```','']
    return '\n'.join(lines)
