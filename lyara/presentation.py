"""Deterministic change review and portable, redacted operation reports."""
import json

from .security import redact


IMPACTS={
    'repair_deploy':('اصلاح فایل‌های لازم برای انتشار و ساخت نسخهٔ تازه','تغییر فرمان شروع یا وابستگی می‌تواند ساخت و اجرای برنامه را تغییر دهد؛ diff و بررسی‌های ساختاری را ببینید.','سورس اصلی ویرایش نمی‌شود؛ در صورت ثبت نسخهٔ قبلی، بازگشت سورس با تأیید تازه ممکن است.'),
    'create_app':('ساخت برنامه با پلن انتخاب‌شده','ممکن است هزینهٔ سرویس از زمان ایجاد آغاز شود؛ قیمت در این برنامه محاسبه نمی‌شود.','توقف سرویس یا حذف آن در پنل، پس از بررسی هزینه و داده‌ها؛ حذف خودکار نداریم.'),
    'deploy':('انتشار بستهٔ بازبینی‌شده و بررسی همان نسخه','ممکن است نسخهٔ جدید خطا یا وقفه ایجاد کند.','در صورت ثبت سورس قبلی، طرح بازگشت به آن با تأیید تازه قابل ساخت است.'),
    'redeploy':('انتشار دوبارهٔ سورس شناخته‌شده','فقط سورس برمی‌گردد؛ داده‌ها، env، پلن و DNS برنمی‌گردند.','برای تغییر مجدد سورس، طرح تازه و تأیید جدید لازم است.'),
    'restart':('راه‌اندازی دوبارهٔ فرایند برنامه','وقفهٔ کوتاه و از دست‌رفتن وضعیت ذخیره‌نشدهٔ فرایند محتمل است.','ری‌استارت قابل برگرداندن نیست؛ سلامت و لاگ جدید را بررسی کنید.'),
    'scale':('روشن یا خاموش‌کردن سرویس','خاموشی باعث در دسترس نبودن سایت می‌شود.','تغییر مجدد تعداد نمونه‌ها به مقدار قبلی، با تأیید جدید.'),
    'resize':('تغییر پلن سرویس','هزینه و ظرفیت تغییر می‌کند؛ قیمت پلن باید در کاتالوگ لیارا بررسی شود.','بازگشت به پلن قبلی با بررسی ظرفیت و تأیید جدید.'),
    'update_env':('تغییر فقط نام‌های محیطی مشخص‌شده','مقادیر نمایش داده نمی‌شوند؛ اثر بر برنامه ممکن است به ری‌استارت نیاز داشته باشد.','مقدار قبلی محرمانه در گزارش ذخیره نمی‌شود؛ بازگردانی از مرجع امن به طرح تازه نیاز دارد.'),
    'attach_domain':('اتصال دامنه به برنامه','تنظیم DNS ثبت‌کننده ممکن است دستی باشد؛ انتشار DNS فوری نیست.','حذف یا اصلاح اتصال در پنل؛ تغییر ثبت‌کننده خودکار انجام نمی‌شود.'),
    'enable_ssl':('درخواست صدور گواهی HTTPS','پذیرش درخواست به معنی آماده‌شدن گواهی نیست.','وضعیت دامنه و پاسخ HTTPS همان دامنه باید جدا بررسی شود.'),
    'create_zone':('ایجاد ناحیهٔ DNS','تغییر nameserver در ثبت‌کننده ممکن است لازم باشد.','تنظیم nameserver و ناحیهٔ قبلی را پیش از جابه‌جایی نگه دارید.'),
    'create_record':('افزودن رکورد DNS','رکورد اشتباه می‌تواند دسترسی سایت یا ایمیل را مختل کند؛ انتشار DNS تأخیر دارد.','اصلاح یا حذف رکورد در پنل؛ بازگردانی خودکار رکورد نداریم.'),
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
