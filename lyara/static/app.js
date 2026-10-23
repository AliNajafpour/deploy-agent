'use strict';
const $ = id => document.getElementById(id);
const stateNames = {preparing:'در حال بررسی',awaiting_approval:'در انتظار تأیید شما',running:'در حال اجرا',completed:'نتیجهٔ عملیات بررسی شد',rejected:'طرح رد شد',failed:'عملیات متوقف شد',unknown:'نتیجهٔ اقدام نامشخص است',needs_input:'نیاز به اطلاعات بیشتر',needs_attention:'نیاز به بررسی نتیجه',observed:'بررسی ثبت شد'};
const evidenceNames = {project:'وضعیت برنامه',logs:'لاگ‌های برنامه',events:'رویدادهای زیرساخت',releases:'نسخه‌های منتشرشده',cpu:'مصرف CPU',memory:'مصرف حافظه'};
let mode = 'deploy', current = null, signature = '', config = {}, busy = false, watching = false, displayed = null;
const token = () => sessionStorage.getItem('lyara-access') || '';
function node(tag, className, text) {const item=document.createElement(tag);if(className)item.className=className;if(text!==undefined)item.textContent=text;return item;}
function fail(error) {$('error').textContent=error.message || String(error);$('error').hidden=false;}
async function api(path, options={}) {
  const response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...(token()?{Authorization:'Bearer '+token()}:{}),...options.headers}});
  const data=await response.json();
  if(!response.ok) {if(response.status===401)$('access-dialog').showModal();throw new Error(typeof data.detail==='string'?data.detail:'اطلاعات درخواست معتبر نیست. فیلدهای فرم را بررسی کنید.');}
  return data;
}
function setMode(value) {
  mode=value;
  for(const button of document.querySelectorAll('[data-mode]')){const active=button.dataset.mode===mode;button.classList.toggle('selected',active);button.setAttribute('aria-pressed',String(active));}
  for(const item of document.querySelectorAll('.deploy-field'))item.hidden=!['deploy','repair'].includes(mode);
  const prompts={repair:'خطای انتشار یا اجرای بک‌اند را بررسی کن؛ فقط فایل‌های لازم برای اجرا و وابستگی‌ها را اصلاح کن و تغییرات را پیش از انتشار برای تأیید نشان بده.',deploy:'این پروژه را بررسی و منتشر کن. پیش از تغییر، طرح را نشان بده.',diagnose:'سایتم خطای ۵۰۰ می‌دهد. شواهد را بررسی کن و علت‌های محتمل را توضیح بده.',operate:'وضعیت سرویس را بررسی کن و برای تغییر موردنیاز طرح بده.',monitor:'سلامت برنامه، لاگ‌ها و دسترسی به داده‌های مصرف منابع را بررسی کن.'};
  $('message').value=prompts[mode];
  $('form-hint').textContent=['deploy','repair'].includes(mode)?'سورس پیش از تأیید شما آپلود نمی‌شود.':'خواندن اطلاعات، تغییری در سرویس ایجاد نمی‌کند.';
}
async function refreshProjects() {
  const data=await api('/projects');$('projects').replaceChildren();
  for(const project of data.projects || []) {
    const name=project.project_id || project.name;
    const button=node('button','project-button');button.type='button';
    button.append(node('span','project-name',name),node('span','project-state',project.status || 'موجود'));
    button.addEventListener('click',()=>{if($('project').value!==name){$('domain').value='';$('health').value='';}$('project').value=name;for(const b of $('projects').children)b.classList.toggle('active',b===button);if(project.planID)$('plan').value=project.planID;if(project.type)$('platform').value=project.type;if(config.local_lab){$('lab-project').value=name;selectLabProject();}});
    $('projects').append(button);
  }
  if(!$('projects').children.length)$('projects').append(node('p','muted','برنامه‌ای موجود نیست. اولین پروژه را منتشر کنید.'));
}
async function history() {
  const runs=await api('/runs');$('history').replaceChildren();
  for(const run of runs.slice(0,8)){
    const button=node('button','history-item',stateNames[run.status] || run.status);button.type='button';button.prepend(node('span','',run.request.project));
    button.addEventListener('click',()=>openRun(run));$('history').append(button);
  }
  if(!runs.length)$('history').append(node('p','muted','هنوز عملیاتی ثبت نشده.'));
}
function openRun(run){setMode(run.request.intent);if(config.local_lab&&config.lab_projects?.some(item=>item.project===run.request.project))$('lab-project').value=run.request.project;for(const [id,key] of Object.entries({project:'project',source:'source',plan:'plan_id',port:'port',platform:'platform',domain:'domain',health:'health_url',message:'message'}))$(id).value=run.request[key] ?? '';signature='';render(run);}
function showEvidence(run) {
  $('evidence').replaceChildren();const observations=run.verification?.observations || run.context?.observations;
  if(!observations)return;
  const heading=node('div','evidence-heading');heading.append(node('h3','','شواهد قابل بررسی'),node('span','',run.verification?'پس از عملیات':'پیش از عملیات'));$('evidence').append(heading);
  for(const [key,value] of Object.entries(observations)){
    const item=node('details','evidence-item');const summary=node('summary');summary.append(node('span','',evidenceNames[key] || key),node('span',value.available?'':'unavailable',value.available?'دریافت شد':'در دسترس نیست'));item.append(summary);
    const data=value.available?value.data:{status:value.status,error:value.error};item.append(node('pre','step-args',JSON.stringify(data,null,2)));
    if(value.id)item.append(node('p','muted',value.id+' · '+(value.collected_at || '')));
    $('evidence').append(item);
  }
  if(run.plan?.evidence?.length){const details=node('details','evidence-item');details.append(node('summary','','برداشت دستیار از شواهد'));for(const evidence of run.plan.evidence)details.append(node('p','muted',evidence));$('evidence').append(details);}
  if(run.plan?.limitations?.length){const details=node('details','evidence-item');details.open=true;details.append(node('summary','','محدودیت‌های این بررسی'));for(const limitation of run.plan.limitations)details.append(node('p','muted',limitation));$('evidence').append(details);}
  if(run.verification&&run.context?.observations){const details=node('details','evidence-item');details.append(node('summary','','مقایسه با شواهد پیش از عملیات'),node('pre','step-args',JSON.stringify(run.context.observations,null,2)));$('evidence').append(details);}
}
function render(run) {
  const newSignature=run.id+':'+run.status+':'+(run.events?.length || 0);if(signature===newSignature)return;signature=newSignature;current=run.id;
  watching=['preparing','running','awaiting_approval'].includes(run.status);
  displayed=run;
  $('welcome').hidden=true;$('run').hidden=false;$('run-title').textContent=run.request.project;
  $('run-status').textContent=stateNames[run.status] || run.status;$('run-status').className='badge'+(run.status==='completed'?' success':run.status==='failed'?' failed':'');
  $('resume').hidden=!['preparing','running'].includes(run.status);
  $('recheck').hidden=!['completed','observed','needs_attention','unknown','failed'].includes(run.status);
  $('recovery').hidden=!(run.review?.previous_source&&['completed','needs_attention','failed'].includes(run.status)&&(run.plan?.steps || []).some(step=>['deploy','redeploy','repair_deploy'].includes(step.action)));
  $('run-parent').textContent=run.parent_id?'ادامهٔ بررسی قبلی · '+run.parent_id.slice(0,8):'';
  $('summary').textContent=run.error || run.plan?.summary || 'فایل‌های پروژه و وضعیت سرویس در حال بررسی است…';
  const phaseIndex={preparing:0,needs_input:1,awaiting_approval:2,rejected:2,running:3,completed:4,observed:4,needs_attention:4,failed:3,unknown:3}[run.status] ?? 0;
  [...$('stages').children].forEach((li,i)=>{li.className=i<phaseIndex?'done':i===phaseIndex?'active':'';});
  $('questions').replaceChildren();for(const question of run.plan?.questions || [])$('questions').append(node('p','','• '+question));
  $('answer-form').hidden=run.status!=='needs_input';if(run.status==='needs_input'){$('answer-port').value=run.request.port || run.context?.port || '';$('answer-plan').value=run.request.plan_id || '';$('answer-platform').value=run.request.platform || run.context?.platform || '';$('answer-port-label').hidden=!['deploy','repair'].includes(run.request.intent)||Boolean(run.request.port || run.context?.port);$('answer-plan-label').hidden=!['deploy','repair'].includes(run.request.intent)||run.context?.exists||Boolean(run.request.plan_id);$('answer-platform-label').hidden=!['deploy','repair'].includes(run.request.intent)||Boolean(run.request.platform || run.context?.platform);}
  $('repair').hidden=!['failed','needs_attention','observed','completed'].includes(run.status);
  $('repair-preview').replaceChildren();if(run.repair?.diff){const box=node('section','change-review');box.append(node('h3','','تغییرات فایل‌های انتشار'),node('p','','این تغییرات فقط در بستهٔ جدا آماده شده‌اند؛ فایل‌های اصلی ویرایش نشده‌اند.'),node('pre','step-args',run.repair.diff));for(const check of run.repair.checks || [])box.append(node('p','',check.path+' · '+check.check+' · '+(check.passed?'تأیید ساختاری':'ناموفق')));box.append(node('p','risk-note','نصب وابستگی هنگام ساخت نسخه انجام می‌شود. بررسی ساختاری، سلامت اجرا را تضمین نمی‌کند.'));$('repair-preview').append(box);}
  $('change-review').replaceChildren();if(run.review?.changes?.length){const box=node('section','change-review');box.append(node('h3','','اثر این طرح روی '+run.review.project));for(const change of run.review.changes){const item=node('div','change-impact');item.append(node('strong','',change.impact),node('p','risk-note',change.risk),node('p','muted','بازیابی: '+change.recovery));box.append(item);}if(!run.review.previous_source&&run.review.changes.some(item=>item.action==='deploy'))box.append(node('p','risk-note','سورس فعال قبلی ثبت نشده است؛ بازگشت یک‌کلیکی به نسخهٔ قبلی قابل تضمین نیست.'));$('change-review').append(box);}
  $('steps').replaceChildren();(run.plan?.steps || []).forEach((step,i)=>{const item=node('article','plan-step');const check=run.verification?.action_checks?.find(check=>check.index===i);const executed=run.results?.length>i;item.append(node('h3','',(executed?(check?.matched===false||check?.matched===null?'ثبت درخواست · ':'✓ '):'')+step.title),node('p','',step.reason));if(Object.keys(step.args || {}).length){const args=node('details');args.append(node('summary','','پارامترهای دقیق اقدام'),node('pre','step-args',JSON.stringify(step.args,null,2)));item.append(args);}const result=run.results?.[i];if(result&&Object.keys(result).length){if(result.next_step)item.append(node('p','risk-note',result.next_step));const detail=node('details');detail.append(node('summary','','نتیجهٔ دریافت‌شده از سرویس'),node('pre','step-args',JSON.stringify(result,null,2)));item.append(detail);}$('steps').append(item);});
  $('artifact').replaceChildren();if(run.artifact?.sha256){const box=node('div','artifact-box');box.append(node('strong','','بستهٔ آمادهٔ انتشار'),node('p','',`${run.artifact.files.length.toLocaleString('fa-IR')} فایل · ${(run.artifact.compressed_bytes/1024).toLocaleString('fa-IR',{maximumFractionDigits:1})} کیلوبایت`),node('code','','SHA256 '+run.artifact.sha256));const detail=node('details');detail.append(node('summary','','بازبینی فایل‌های بسته'),node('pre','file-list',run.artifact.files.join('\n')));box.append(detail);$('artifact').append(box);}
  $('approval').hidden=run.status!=='awaiting_approval';$('approve').disabled=false;$('reject').disabled=false;
  $('approval-digest').textContent=run.plan_digest?'طرح '+run.plan_digest.slice(0,16):'';
  $('verification').replaceChildren();if(run.verification){const health=run.verification.health;const stopped=run.verification.outcome==='stopped';const incomplete=(run.verification.action_checks || []).some(check=>check.matched!==true)||['failed','unknown'].includes(run.status);const box=node('div','verification-box '+((health.healthy||stopped)&&!incomplete?'ok':'warn'));box.append(node('h3','',stopped?'خاموش‌شدن سرویس تأیید شد':health.healthy?(incomplete?'سایت پاسخ می‌دهد؛ نتیجهٔ بعضی اقدامات قطعی نیست':'پاسخ سایت بررسی شد'):'سلامت سایت هنوز تأیید نشده'),node('p','',health.available?`HTTP ${health.status} · ${health.latency_ms || '—'} ms`:health.error || 'داده در دسترس نیست'),node('p','',run.verification.note));for(const check of run.verification.action_checks || [])box.append(node('p',check.matched===true?'':'risk-note',(check.matched===true?'تأیید شد: ':'هنوز تأیید نشده: ')+check.note));try{const url=new URL(config.local_lab&&health.local_url?health.local_url:health.url);if(url.protocol==='https:'||(config.local_lab&&url.protocol==='http:'&&url.hostname==='127.0.0.1')){const link=node('a','',config.local_lab?'بازکردن برنامهٔ واقعی محلی ↗':health.url);link.href=url.href;link.target='_blank';link.rel='noreferrer';box.append(link);}}catch{}$('verification').append(box);}
  if(run.verification?.configuration_checks?.length){const box=$('verification').firstChild;for(const check of run.verification.configuration_checks){const label={resize:'تغییر پلن',scale:'تعداد نمونه‌های سرویس',update_env:'متغیر محیطی'}[check.action] || check.action;box.append(node('p','',label+(check.variable?' '+check.variable:'')+(check.matched?' تأیید شد.':' هنوز تأیید نشده است.')));if(!check.matched){box.classList.remove('ok');box.classList.add('warn');}}}
  showEvidence(run);$('events').replaceChildren();for(const event of run.events || []){const li=node('li','',event.text);li.append(node('time','',new Date(event.at).toLocaleTimeString('fa-IR')));$('events').append(li);}
}
async function initialize() {
  try {config=await api('/config');$('provider').textContent=config.provider+(config.model?' / '+config.model:'');$('connection').textContent=config.demo?'آزمایش آفلاین آماده است':'اتصال به سرور برقرار است';$('demo-banner').hidden=!config.demo;$('configuration').textContent=`Provider: ${config.provider}\nWorkspace: ${config.workspace}\nMode: ${config.demo?'offline demo':'live'}`;
    if(config.demo){$('source').value='examples/hello-node';$('project').value='my-first-app';$('plan').value='demo-plan';$('port').value=3000;}
    if(config.local_lab){$('demo-banner').hidden=false;$('demo-banner').textContent='آزمایشگاه محلی · برنامه‌ها واقعاً اجرا می‌شوند؛ فقط API لیارا شبیه‌سازی شده است. مدل: '+config.provider;$('connection').textContent='آزمایشگاه محلی آماده است';$('lab-catalog').hidden=false;$('lab-project').replaceChildren();for(const item of config.lab_projects){const option=node('option','',item.source);option.value=item.project;option.selected=Boolean(item.exists);$('lab-project').append(option);}selectLabProject();}
    await Promise.all([refreshProjects(),history()]);$('error').hidden=true;
  } catch(error){fail(error);$('connection').textContent='اتصال نیاز به بررسی دارد';}
}
for(const button of document.querySelectorAll('[data-mode]'))button.addEventListener('click',()=>setMode(button.dataset.mode));
for(const button of document.querySelectorAll('[data-example]'))button.addEventListener('click',()=>{setMode(button.dataset.example);if(mode==='diagnose'&&config.demo)$('project').value='demo-app';$('message').focus();});
$('refresh').addEventListener('click',()=>refreshProjects().catch(fail));
function selectLabProject(){const item=config.lab_projects?.find(item=>item.project===$('lab-project').value);if(!item)return;$('project').value=item.project;$('source').value=item.source;$('platform').value=item.platform;$('plan').value='local-plan';$('port').value=item.port || '';$('health').value='https://'+item.project+'.liara.run';}
$('lab-project').addEventListener('change',selectLabProject);
$('new-project').addEventListener('click',()=>{setMode('deploy');for(const id of ['project','domain','health','port','platform'])$(id).value='';$('source').value=config.demo?'examples/hello-node':'.';$('plan').value=config.demo?'demo-plan':'';$('project').focus();});
$('access-button').addEventListener('click',()=>$('access-dialog').showModal());
$('save-access').addEventListener('click',()=>{sessionStorage.setItem('lyara-access',$('access-token').value);setTimeout(initialize,0);});
$('request-form').addEventListener('submit',async event=>{event.preventDefault();if(busy)return;busy=true;$('submit').disabled=true;$('error').hidden=true;
  try{const request={message:$('message').value,intent:mode,project:$('project').value,source:$('source').value || '.',plan_id:$('plan').value,platform:$('platform').value,port:$('port').value?Number($('port').value):null,domain:$('domain').value,health_url:$('health').value};const run=await api('/runs',{method:'POST',body:JSON.stringify(request)});signature='';render(run);history().catch(fail);}catch(error){fail(error);}finally{busy=false;$('submit').disabled=false;}});
async function decide(approved){if(!current||displayed?.status!=='awaiting_approval')return;$('approve').disabled=true;$('reject').disabled=true;try{await api('/runs/'+current+'/approval',{method:'POST',body:JSON.stringify({approved,plan_digest:displayed.plan_digest})});}catch(error){fail(error);$('approve').disabled=false;$('reject').disabled=false;}}
$('approve').addEventListener('click',()=>decide(true));$('reject').addEventListener('click',()=>decide(false));
$('resume').addEventListener('click',async()=>{if(!current)return;try{await api('/runs/'+current+'/resume',{method:'POST'});}catch(error){fail(error);}});
$('answer-form').addEventListener('submit',async event=>{event.preventDefault();if(!current||busy)return;busy=true;$('send-answer').disabled=true;try{const answer={message:$('answer-message').value,port:$('answer-port').value?Number($('answer-port').value):null,plan_id:$('answer-plan').value||null,platform:$('answer-platform').value||null};const run=await api('/runs/'+current+'/answer',{method:'POST',body:JSON.stringify(answer)});$('answer-message').value='';openRun(run);history().catch(fail);}catch(error){fail(error);}finally{busy=false;$('send-answer').disabled=false;}});
$('repair').addEventListener('click',async()=>{if(!current||busy)return;busy=true;$('repair').disabled=true;try{const run=await api('/runs/'+current+'/repair',{method:'POST'});openRun(run);history().catch(fail);}catch(error){fail(error);}finally{busy=false;$('repair').disabled=false;}});
$('recovery').addEventListener('click',async()=>{if(!current||busy)return;busy=true;$('recovery').disabled=true;try{const run=await api('/runs/'+current+'/recovery',{method:'POST'});openRun(run);history().catch(fail);}catch(error){fail(error);}finally{busy=false;$('recovery').disabled=false;}});
$('recheck').addEventListener('click',async()=>{if(!current)return;const id=current;$('recheck').disabled=true;$('recheck').textContent='در حال خواندن دوبارهٔ وضعیت…';try{const run=await api('/runs/'+id+'/recheck',{method:'POST'});if(current===id){signature='';render(run);}history().catch(fail);}catch(error){fail(error);}finally{$('recheck').disabled=false;$('recheck').textContent='بررسی دوبارهٔ نتیجه';}});
$('download-report').addEventListener('click',async()=>{if(!current)return;const id=current;try{const response=await fetch('/api/runs/'+id+'/report?format=markdown',{headers:token()?{Authorization:'Bearer '+token()}: {}});if(!response.ok)throw new Error('دریافت گزارش ممکن نبود. اتصال و دسترسی را بررسی کنید.');const url=URL.createObjectURL(await response.blob());const link=node('a');link.href=url;link.download='lyara-report-'+id+'.md';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){fail(error);}});
setInterval(async()=>{if(!current||!watching||document.hidden)return;const id=current;try{const run=await api('/runs/'+id);if(current!==id)return;const old=signature;render(run);if(old!==signature)history().catch(fail);}catch(error){fail(error);}},1500);
setMode('deploy');initialize();
