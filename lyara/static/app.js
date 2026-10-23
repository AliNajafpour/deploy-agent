"use strict";
const $ = id => document.getElementById(id);
const stateNames = {preparing:'در حال بررسی سایت',awaiting_approval:'منتظر اجازهٔ تو',running:'دارم انجامش می‌دهم',completed:'انجام شد',rejected:'فعلاً انجام نشد',failed:'کار متوقف شد',unknown:'نتیجه هنوز مشخص نیست',needs_input:'این تصمیم با توست',needs_attention:'نیاز به بررسی بیشتر',observed:'بررسی انجام شد'};
const evidenceNames = {project:'وضعیت سایت',logs:'گزارش اجرای سایت',events:'رویدادهای میزبانی',releases:'نسخه‌های سایت',cpu:'مصرف پردازنده',memory:'مصرف حافظه'};
const samples = {'01-static':'یک سایت ساده','02-node':'سایت آمادهٔ انتشار','03-missing-port':'سایتی که تنظیمات اجرا ندارد','04-missing-env':'سایتی که یک اطلاعات لازم را ندارد','05-http500':'سایتی با خطای داخلی','05-http500-fixed':'نسخهٔ اصلاح‌شدهٔ سایت','06-startup-crash':'سایتی که روشن نمی‌شود','07-wrong-port':'سایتی با تنظیم اجرای اشتباه','08-worker-sqlite':'سایتی با چند بخش وابسته','09-restart-approval':'سایتی که با راه‌اندازی دوباره بهتر می‌شود','10-log-injection':'سایتی با گزارش مشکوک'};
const siteSources=new Map();
let mode='deploy',current=null,signature='',config={},busy=false,watching=false,displayed=null;
const token = () => sessionStorage.getItem('lyara-access') || '';
function node(tag,className,text){const item=document.createElement(tag);if(className)item.className=className;if(text!==undefined)item.textContent=text;return item;}
function plainError(message){if(/پورت|پلتفرم|پارامترهای اقدام/.test(message))return 'تنظیمات اجرای سایت هنوز آماده نشده‌اند. می‌توانی از من بخواهی مشکل انتشار را بررسی کنم.';if(/مسیر پروژه/.test(message))return 'به این پوشه دسترسی ندارم؛ یکی از پوشه‌های موجود در لیست را انتخاب کن.';return message;}
function fail(error){$('error').textContent=plainError(error.message || String(error));$('error').hidden=false;}
async function api(path,options={}){
  const response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...(token()?{Authorization:'Bearer '+token()}:{}),...options.headers}});
  const data=await response.json();
  if(!response.ok){if(response.status===401)$('access-dialog').showModal();throw new Error(typeof data.detail==='string'?data.detail:'درخواست کامل نیست؛ انتخاب پوشه و متن درخواستت را بررسی کن.');}
  return data;
}
function labelFor(project,source=''){const entry=config.lab_projects?.find(item=>item.project===project);if(entry)return samples[entry.source] || entry.source;source=source || siteSources.get(project) || '';return source && source!=='.'?source.split(/[\/]/).filter(Boolean).pop():project || 'سایت جدید';}
function setMode(value){
  mode=value;
  for(const button of document.querySelectorAll('[data-mode]')){const active=button.dataset.mode===mode;button.classList.toggle('selected',active);button.setAttribute('aria-pressed',String(active));}
  $('source-choice-box').hidden=config.local_lab || !['deploy','repair'].includes(mode);
  const prompts={deploy:'این سایت را منتشر کن. تنظیمات لازم را خودت بررسی کن و قبل از هر خرید یا تغییر مهم از من اجازه بگیر.',repair:'سایتم منتشر نمی‌شود. مشکل اجرای آن را پیدا کن و راهش بینداز؛ تغییر مهم را اول به من بگو.',diagnose:'سایتم درست باز نمی‌شود. خودت بررسی کن مشکل چیست و چه کاری لازم است.',operate:'می‌خواهم تغییری در سایتم بدهم؛ قبل از انجامش نتیجه و هزینه را توضیح بده.',monitor:'بررسی کن سایتم درست کار می‌کند یا نیاز به رسیدگی دارد.'};
  $('message').value=prompts[mode];
  $('form-hint').textContent=['deploy','repair'].includes(mode)?'اول بررسی می‌کنم؛ تا تأیید نکنی چیزی منتشر یا خریداری نمی‌شود.':'اول وضعیت را بررسی می‌کنم؛ تغییر مهم به اجازهٔ تو نیاز دارد.';
  $('submit').textContent=['deploy','repair'].includes(mode)?'بررسی کن و راهش بینداز':'سایتم را بررسی کن';
}
async function refreshProjects(){
  const data=await api('/projects');$('projects').replaceChildren();
  for(const project of data.projects || []){
    const name=project.project_id || project.name;
    const button=node('button','project-button');button.type='button';
    button.append(node('span','project-name',labelFor(name)),node('span','project-state','سایت موجود'));
    button.addEventListener('click',()=>{if($('project').value!==name){$('domain').value='';$('health').value='';}$('project').value=name;for(const item of $('projects').children)item.classList.toggle('active',item===button);$('plan').value=project.planID || '';$('port').value='';$('platform').value='';if(config.local_lab){$('lab-project').value=name;selectLabProject();}});
    $('projects').append(button);
  }
  if(!$('projects').children.length)$('projects').append(node('p','muted','هنوز سایتی نداری؛ از انتخاب پوشه شروع کن.'));
}
async function history(){
  const runs=await api('/runs');$('history').replaceChildren();
  for(const run of [...runs].reverse())if(run.request?.source)siteSources.set(run.request.project,run.request.source);
  for(const run of runs.slice(0,8)){const button=node('button','history-item',stateNames[run.status] || run.status);button.type='button';button.prepend(node('span','',labelFor(run.request.project,run.request.source)));button.addEventListener('click',()=>openRun(run));$('history').append(button);}
  if(!runs.length)$('history').append(node('p','muted','اولین درخواستت را همین‌جا شروع کن.'));
}
function openRun(run){setMode(run.request.intent);if(config.local_lab)$('lab-project').value=run.request.project;for(const [id,key] of Object.entries({project:'project',source:'source',plan:'plan_id',port:'port',platform:'platform',domain:'domain',health:'health_url',message:'message'}))$(id).value=run.request[key] ?? '';$('source-picker').value=run.request.source;$('source-manual').value=run.request.source;signature='';render(run);}
function showEvidence(run){
  $('evidence').replaceChildren();const observations=run.verification?.observations || run.context?.observations;if(!observations)return;
  $('evidence').append(node('h3','','شواهد ثبت‌شده'));
  for(const [key,value] of Object.entries(observations)){const item=node('details','evidence-item');const summary=node('summary');summary.append(node('span','',evidenceNames[key] || key),node('span',value.available?'':'unavailable',value.available?'دریافت شد':'در دسترس نیست'));item.append(summary,node('pre','step-args',JSON.stringify(value.available?value.data:{status:value.status,error:value.error},null,2)));if(value.id)item.append(node('p','muted',value.id+' · '+(value.collected_at || '')));$('evidence').append(item);}
  if(run.plan?.evidence?.length){const detail=node('details','evidence-item');detail.append(node('summary','','برداشت دستیار از شواهد'));for(const item of run.plan.evidence)detail.append(node('p','muted',item));$('evidence').append(detail);}
  if(run.verification&&run.context?.observations){const detail=node('details','evidence-item');detail.append(node('summary','','وضعیت پیش از انجام کار'),node('pre','step-args',JSON.stringify(run.context.observations,null,2)));$('evidence').append(detail);}
}
function renderQuestions(run){
  $('questions').replaceChildren();for(const question of run.plan?.questions || [])$('questions').append(node('p','',question));
  $('answer-form').hidden=run.status!=='needs_input';
  const needsHosting=['deploy','repair'].includes(run.request.intent)&&run.context?.exists===false&&!run.request.plan_id;
  const options=run.context?.hosting_options || [];
  $('answer-hosting-label').hidden=!needsHosting || !options.length;
  $('hosting-unavailable').hidden=!needsHosting || options.length>0;
  $('answer-hosting').replaceChildren();const empty=node('option','','یک گزینه را انتخاب کن');empty.value='';$('answer-hosting').append(empty);
  for(const item of options){const option=node('option','',item.title+' · '+item.cost);option.value=item.id;$('answer-hosting').append(option);}
  $('send-answer').disabled=needsHosting && !options.length;
}
function render(run){
  const next=run.id+':'+run.status+':'+(run.events?.length || 0);if(signature===next)return;signature=next;current=run.id;displayed=run;
  watching=['preparing','running','awaiting_approval'].includes(run.status);
  $('welcome').hidden=true;$('run').hidden=false;$('run-title').textContent=labelFor(run.request.project,run.request.source);
  $('run-status').textContent=stateNames[run.status] || run.status;$('run-status').className='badge'+(run.status==='completed'?' success':run.status==='failed'?' failed':'');
  $('resume').hidden=!['preparing','running'].includes(run.status);$('recheck').hidden=!['completed','observed','needs_attention','unknown','failed'].includes(run.status);
  $('repair').hidden=!['failed','needs_attention','observed','completed'].includes(run.status);
  $('recovery').hidden=!(run.review?.previous_source&&['completed','needs_attention','failed'].includes(run.status)&&(run.plan?.steps || []).some(step=>['deploy','redeploy','repair_deploy'].includes(step.action)));
  $('run-parent').textContent=run.parent_id?'بررسی قبلی: '+run.parent_id:'';
  const brief={awaiting_approval:'پیشنهادم آماده است. کارها و اثرشان را بخوان؛ اگر موافقی اجازه بده انجامشان بدهم.',running:'دارم کارهایی را که اجازه دادی انجام می‌دهم.',completed:'کارهای تأییدشده انجام شدند؛ نتیجهٔ بررسی سایت را پایین ببین.',rejected:'با انتخاب تو این کارها انجام نشدند.',needs_input:'برای ادامه فقط به پاسخ این سؤال نیاز دارم.',needs_attention:'کار به بررسی بیشتری نیاز دارد؛ هنوز همهٔ نتیجه را تأیید نکرده‌ام.',unknown:'نتیجهٔ این کار هنوز روشن نیست؛ برای جلوگیری از تکرار، دوباره انجامش نمی‌دهم.'};
  $('summary').textContent=run.error?plainError(run.error):(run.plan?.steps?.length || run.status==='needs_input')&&brief[run.status]?brief[run.status]:run.plan?.summary || 'دارم فایل‌های سایت و وضعیتش را بررسی می‌کنم…';
  $('setup-info').textContent=JSON.stringify({project:run.request.project,platform:run.context?.platform,port:run.context?.port,error:run.error,health:run.verification?.health,results:run.results,planner_summary:run.plan?.summary},null,2);
  const index={preparing:0,needs_input:1,awaiting_approval:2,rejected:2,running:3,completed:4,observed:4,needs_attention:4,failed:3,unknown:3}[run.status] ?? 0;
  [...$('stages').children].forEach((item,i)=>item.className=i<index?'done':i===index?'active':'');
  renderQuestions(run);
  $('user-notes').replaceChildren();if(run.plan?.limitations?.length){const box=node('section','owner-notes');box.append(node('strong','','چیزهایی که باید بدانی'));for(const item of run.plan.limitations)box.append(node('p','',item));$('user-notes').append(box);}
  for(const result of run.results || [])if(result.next_step){const note=node('section','owner-notes');note.append(node('strong','','یک کار به خودت نیاز دارد'),node('p','','برای وصل‌شدن آدرس دلخواهت، ممکن است لازم باشد وارد حسابی شوی که آدرس را از آن خریده‌ای و اتصالش را تأیید کنی. راهنمای اتصال در جزئیات این کار ثبت شده است.'));$('user-notes').append(note);}
  $('change-review').replaceChildren();if(run.review?.changes?.length){const box=node('section','change-review');box.append(node('h3','',['completed','needs_attention','failed','unknown'].includes(run.status)?'کارهایی که اجازه دادی':'با اجازهٔ تو این کارها را انجام می‌دهم'));
    const hosting=(run.context?.hosting_options || []).find(item=>item.id===run.request.plan_id);if(hosting&&run.review.changes.some(item=>item.action==='create_app'))box.append(node('p','risk-note',hosting.title+' · '+hosting.cost));
    for(const change of run.review.changes){const item=node('div','change-impact');item.append(node('strong','',change.impact),node('p','risk-note',config.local_lab&&change.action==='create_app'?'در این آزمایش خرید یا تغییری در حساب واقعی انجام نمی‌شود.':change.risk),node('p','muted','اگر لازم شد برگردیم: '+change.recovery));box.append(item);}$('change-review').append(box);}
  $('steps').replaceChildren();for(const step of run.plan?.steps || []){const item=node('article','plan-step');item.append(node('h3','',step.title),node('p','',step.reason));const args=node('details');args.append(node('summary','','تنظیمات انتخاب‌شده'),node('pre','step-args',JSON.stringify(step.args,null,2)));item.append(args);$('steps').append(item);}
  $('artifact').replaceChildren();if(run.artifact?.sha256){const detail=node('details','artifact-box');detail.append(node('summary','','فایل‌های نسخهٔ آمادهٔ انتشار'),node('code','','SHA256 '+run.artifact.sha256),node('pre','file-list',run.artifact.files.join('\n')));$('artifact').append(detail);}
  $('repair-preview').replaceChildren();if(run.repair?.diff){const box=node('section','change-review');box.append(node('h3','','تغییر فایل‌های اجرا'),node('pre','step-args',run.repair.diff));for(const check of run.repair.checks || [])box.append(node('p','',check.path+' · '+check.check));$('repair-preview').append(box);}
  $('approval').hidden=run.status!=='awaiting_approval';$('approve').disabled=false;$('reject').disabled=false;$('approval-digest').textContent=run.plan_digest || '';
  $('verification').replaceChildren();if(run.verification){
    const health=run.verification.health;const stopped=run.verification.outcome==='stopped';const incomplete=(run.verification.action_checks || []).some(check=>check.matched!==true)||(run.verification.configuration_checks || []).some(check=>check.matched!==true)||['failed','unknown','needs_attention'].includes(run.status);
    const good=(health.healthy||stopped)&&!incomplete;const box=node('section','verification-box '+(good?'ok':'warn'));
    const title=stopped?'خاموش‌شدن سایت بررسی شد':health.healthy?(incomplete?'سایت باز می‌شود؛ این کار هنوز کامل تأیید نشده':run.status==='completed'?'سایتت آماده است':'سایت باز می‌شود'):'هنوز نتوانستم سایت را باز کنم';
    box.append(node('h3','',title),node('p','',good?'نتیجهٔ کار را جداگانه بررسی کردم.':health.healthy?'بازشدن سایت را دیدم؛ اثر همهٔ تغییرات هنوز مشخص نیست.':'بدون تأیید سلامت، کار را تمام‌شده اعلام نمی‌کنم.'));
    try{const url=new URL(config.local_lab&&health.local_url?health.local_url:health.url);if(url.protocol==='https:'||(config.local_lab&&url.protocol==='http:'&&url.hostname==='127.0.0.1')){url.pathname='/';url.search='';url.hash='';const link=node('a','site-link','سایتم را باز کن ↗');link.href=url.href;link.target='_blank';link.rel='noreferrer';box.append(link);}}catch{}
    $('verification').append(box);
  }
  showEvidence(run);$('events').replaceChildren();for(const event of run.events || []){const item=node('li','',event.text);item.append(node('time','',new Date(event.at).toLocaleTimeString('fa-IR')));$('events').append(item);}
}
async function loadSources(){
  const sources=await api('/sources');$('source-picker').replaceChildren();const empty=node('option','','پوشهٔ سایت را انتخاب کن');empty.value='';$('source-picker').append(empty);
  for(const item of sources){const option=node('option','',item.label);option.value=item.source;$('source-picker').append(option);}
  $('source-picker').value=$('source').value;if(!config.demo&&!config.local_lab&&!sources.some(item=>item.source===$('source').value))$('source').value='';if(!sources.length)$('manual-source').open=true;
}
function selectLabProject(){
  const item=config.lab_projects?.find(entry=>entry.project===$('lab-project').value);if(!item)return;
  $('project').value=item.project;$('source').value=item.source;$('plan').value='local-plan';$('port').value='';$('platform').value='';$('health').value='';$('domain').value='';
}
async function initialize(){
  try{config=await api('/config');$('provider').textContent=config.model?.includes('luna')?'همراه Luna':'همکار هوشمندت';$('connection').textContent='آماده‌ام کمکت کنم';$('configuration').textContent=`Provider: ${config.provider}\nModel: ${config.model || ''}\nWorkspace: ${config.workspace}`;$('demo-banner').hidden=!config.demo;$('demo-banner').textContent='نمونهٔ نمایشی · خرید یا تغییری در حساب واقعی انجام نمی‌شود.';
    if(config.demo){$('source').value='examples/hello-node';$('project').value='my-first-site';$('plan').value='demo-plan';}
    $('lab-catalog').hidden=!config.local_lab;
    if(config.local_lab){$('demo-banner').hidden=false;$('demo-banner').textContent='آزمایش محلی · برنامه‌ها اجرا می‌شوند، اما حساب میزبانی واقعی تغییر نمی‌کند.';$('lab-project').replaceChildren();for(const item of config.lab_projects){const option=node('option','',samples[item.source] || item.source);option.value=item.project;option.selected=Boolean(item.exists);$('lab-project').append(option);}selectLabProject();}
    setMode(mode);await history();await Promise.all([refreshProjects(),loadSources()]);$('error').hidden=true;
  }catch(error){fail(error);$('connection').textContent='اتصال نیاز به رسیدگی دارد';}
}
for(const button of document.querySelectorAll('[data-mode]'))button.addEventListener('click',()=>setMode(button.dataset.mode));
for(const button of document.querySelectorAll('[data-example]'))button.addEventListener('click',()=>{setMode(button.dataset.example);if(mode==='diagnose'&&config.demo)$('project').value='demo-app';$('message').focus();});
$('refresh').addEventListener('click',()=>refreshProjects().catch(fail));
$('lab-project').addEventListener('change',selectLabProject);
$('source-picker').addEventListener('change',()=>{$('source').value=$('source-picker').value;$('source-manual').value=$('source').value;});
$('source-manual').addEventListener('input',()=>{$('source').value=$('source-manual').value;$('source-picker').value=$('source').value;});
$('new-project').addEventListener('click',()=>{setMode('deploy');for(const id of ['project','domain','health','port','platform','plan'])$(id).value='';if(config.local_lab){const item=config.lab_projects.find(entry=>!entry.exists) || config.lab_projects[0];if(item){$('lab-project').value=item.project;selectLabProject();}}else if(config.demo)$('plan').value='demo-plan';$('message').focus();});
$('access-button').addEventListener('click',()=>$('access-dialog').showModal());
$('save-access').addEventListener('click',()=>{sessionStorage.setItem('lyara-access',$('access-token').value);setTimeout(initialize,0);});
$('request-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;busy=true;$('submit').disabled=true;$('error').hidden=true;
  try{let project=$('project').value;if(!project){if(!['deploy','repair'].includes(mode))throw new Error('اول سایتی را که می‌خواهی بررسی کنم از لیست انتخاب کن.');project='site-'+crypto.randomUUID().replaceAll('-','').slice(0,10);$('project').value=project;}
    if(['deploy','repair'].includes(mode)&&!$('source').value)throw new Error('پوشهٔ سایتت را انتخاب کن.');
    const request={message:$('message').value,intent:mode,project,source:$('source').value || '.',plan_id:$('plan').value,platform:$('platform').value,port:$('port').value?Number($('port').value):null,domain:$('domain').value.trim(),health_url:$('health').value};
    const run=await api('/runs',{method:'POST',body:JSON.stringify(request)});signature='';render(run);history().catch(fail);
  }catch(error){fail(error);}finally{busy=false;$('submit').disabled=false;}
});
async function decide(approved){if(!current||displayed?.status!=='awaiting_approval')return;$('approve').disabled=true;$('reject').disabled=true;try{await api('/runs/'+current+'/approval',{method:'POST',body:JSON.stringify({approved,plan_digest:displayed.plan_digest})});}catch(error){fail(error);$('approve').disabled=false;$('reject').disabled=false;}}
$('approve').addEventListener('click',()=>decide(true));$('reject').addEventListener('click',()=>decide(false));
$('resume').addEventListener('click',async()=>{if(current)try{await api('/runs/'+current+'/resume',{method:'POST'});}catch(error){fail(error);}});
$('answer-form').addEventListener('submit',async event=>{event.preventDefault();if(!current||busy)return;busy=true;$('send-answer').disabled=true;try{const plan=$('answer-hosting').value;const message=$('answer-message').value.trim() || (plan?'گزینهٔ هزینه‌ای که انتخاب کردم را برای سایت استفاده کن.':'');if(!message)throw new Error('پاسخ سؤال را بنویس یا گزینهٔ هزینه را انتخاب کن.');if(!$('answer-hosting-label').hidden&&!plan)throw new Error('برای هزینهٔ میزبانی یک گزینه را انتخاب کن.');const answer={message,plan_id:plan || null};const run=await api('/runs/'+current+'/answer',{method:'POST',body:JSON.stringify(answer)});$('answer-message').value='';openRun(run);history().catch(fail);}catch(error){fail(error);}finally{busy=false;renderQuestions(displayed);}});
for(const name of ['repair','recovery'])$(name).addEventListener('click',async()=>{if(!current||busy)return;busy=true;$(name).disabled=true;try{const run=await api('/runs/'+current+'/'+name,{method:'POST'});openRun(run);history().catch(fail);}catch(error){fail(error);}finally{busy=false;$(name).disabled=false;}});
$('recheck').addEventListener('click',async()=>{if(!current)return;const id=current;$('recheck').disabled=true;try{const run=await api('/runs/'+id+'/recheck',{method:'POST'});if(current===id){signature='';render(run);}history().catch(fail);}catch(error){fail(error);}finally{$('recheck').disabled=false;}});
$('download-report').addEventListener('click',async()=>{if(!current)return;try{const response=await fetch('/api/runs/'+current+'/report?format=markdown',{headers:token()?{Authorization:'Bearer '+token()}: {}});if(!response.ok)throw new Error('گزارش دریافت نشد؛ اتصال و دسترسی را بررسی کن.');const url=URL.createObjectURL(await response.blob());const link=node('a');link.href=url;link.download='site-report.md';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){fail(error);}});
setInterval(async()=>{if(!current||!watching||document.hidden)return;const id=current;try{const run=await api('/runs/'+id);if(current!==id)return;const old=signature;render(run);if(old!==signature)history().catch(fail);}catch(error){fail(error);}},1500);
setMode('deploy');initialize();
