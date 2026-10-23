"use strict";
const $ = id => document.getElementById(id);
const stateNames = {preparing:'در حال بررسی',awaiting_approval:'پیشنهاد آماده است',running:'در حال اجرا',completed:'انجام شد',rejected:'انجام نشد',failed:'کار متوقف شد',unknown:'نتیجه نامشخص',needs_input:'اطلاعات لازم برای ادامه',needs_attention:'نیاز به بررسی بیشتر',observed:'بررسی انجام شد'};
const evidenceNames = {project:'وضعیت سایت',logs:'گزارش اجرا',events:'رویدادهای میزبانی',releases:'نسخه‌های سایت',cpu:'مصرف پردازنده',memory:'مصرف حافظه'};
const samples = {'01-static':'یک سایت ساده','02-node':'سایت آمادهٔ انتشار','03-missing-port':'تنظیمات اجرای ناقص','04-missing-env':'اطلاعات اتصال ناقص','05-http500':'خطای داخلی سایت','05-http500-fixed':'نسخهٔ اصلاح‌شده','06-startup-crash':'خطای روشن‌شدن سایت','07-wrong-port':'تنظیم اجرای اشتباه','08-worker-sqlite':'سایت با بخش‌های وابسته','09-restart-approval':'نیاز به راه‌اندازی دوباره','10-log-injection':'گزارش مشکوک'};
const siteSources=new Map(), projects=new Map(), previousRuns=new Map();
let mode='deploy',current=null,signature='',config={},busy=false,watching=false,displayed=null;
const token = () => sessionStorage.getItem('lyara-access') || '';
function node(tag,className,text){const item=document.createElement(tag);if(className)item.className=className;if(text!==undefined)item.textContent=text;return item;}
function plainError(message){if(/API مدل|ارتباط با.*مدل/.test(message))return 'ارتباط دستیار برقرار نشد؛ دوباره بررسی کن.';if(/پورت|پلتفرم|پارامترهای اقدام/.test(message))return 'تنظیمات اجرا کامل نیست؛ از بخش جزئیات، اصلاح انتشار را انتخاب کن.';if(/مسیر پروژه/.test(message))return 'به این پوشه دسترسی ندارم؛ یکی از پوشه‌های لیست را انتخاب کن.';return message;}
function fail(error){$('error').textContent=plainError(error.message || String(error));$('error').hidden=false;}
async function api(path,options={}){
  const response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...(token()?{Authorization:'Bearer '+token()}:{}),...options.headers}});
  const data=await response.json();
  if(!response.ok){if(response.status===401&&!$('access-dialog').open)$('access-dialog').showModal();throw new Error(typeof data.detail==='string'?data.detail:'درخواست کامل نیست؛ پوشه و متن درخواست را بررسی کن.');}
  return data;
}
function labelFor(project,source=''){const entry=config.lab_projects?.find(item=>item.project===project);if(entry)return samples[entry.source] || entry.source;source=source || siteSources.get(project) || '';return source&&source!=='.'?source.split(/[\\/]/).filter(Boolean).pop():project || 'سایت جدید';}
function setMode(value,replaceMessage=true){
  mode=value;
  for(const button of document.querySelectorAll('[data-mode]')){const active=button.dataset.mode===mode;button.classList.toggle('selected',active);button.setAttribute('aria-pressed',String(active));}
  $('source-choice-box').hidden=config.local_lab || !['deploy','repair'].includes(mode);
  const titles={deploy:'سایتت را آنلاین کن.',repair:'انتشار را درست کنیم.',diagnose:'مشکل سایت را بررسی کن.',operate:'سایتت را تغییر بده.',monitor:'وضعیت سایت را ببین.'};
  const prompts={deploy:'این سایت را منتشر کن؛ قبل از خرید یا تغییر مهم از من اجازه بگیر.',repair:'مشکل انتشار این سایت را پیدا و اصلاح کن.',diagnose:'سایتم درست باز نمی‌شود؛ مشکل چیست؟',operate:'',monitor:'بررسی کن سایتم درست کار می‌کند.'};
  $('workspace-title').textContent=titles[mode];if(replaceMessage)$('message').value=prompts[mode];
}
function showComposer(){current=null;watching=false;signature='';$('run').hidden=true;$('composer').hidden=false;$('error').hidden=true;$('technical-details').open=false;$('project-inputs').replaceChildren();$('message').focus();}
async function refreshProjects(){
  const data=await api('/projects');const selected=$('project').value;projects.clear();$('projects').replaceChildren();const empty=node('option','','سایت جدید');empty.value='';$('projects').append(empty);
  for(const project of data.projects || []){const name=project.project_id || project.name;projects.set(name,project);const option=node('option','',labelFor(name));option.value=name;$('projects').append(option);}
  $('projects').value=selected;
}
async function history(){
  const runs=await api('/runs');previousRuns.clear();$('history').replaceChildren();const empty=node('option','','درخواست‌های قبلی');empty.value='';$('history').append(empty);
  for(const run of [...runs].reverse())if(run.request?.source)siteSources.set(run.request.project,run.request.source);
  for(const run of runs.slice(0,8)){previousRuns.set(run.id,run);const option=node('option','',labelFor(run.request.project,run.request.source)+' · '+(stateNames[run.status] || run.status));option.value=run.id;$('history').append(option);}
  $('history').hidden=!runs.length;
}
function openRun(run){setMode(run.request.intent,false);if(config.local_lab)$('lab-project').value=run.request.project;for(const [id,key] of Object.entries({project:'project',source:'source',plan:'plan_id',port:'port',platform:'platform',domain:'domain',health:'health_url',message:'message'}))$(id).value=run.request[key] ?? '';$('source-picker').value=run.request.source;$('source-manual').value=run.request.source;$('projects').value=run.request.project;signature='';render(run);}
// Group validated action types so the approval review stays short without truncating a risk.
function compactReview(run,localLab=false){
  const changes=run.review?.changes || [], has=(...actions)=>changes.some(change=>actions.includes(change.action)), sentences=[];
  const billing=changes.filter(change=>['create_app','resize'].includes(change.action));
  if(billing.length){const choices=[...new Set(billing.map(change=>{const choice=run.context?.hosting_options?.find(item=>item.id===change.parameters?.plan_id);return choice?choice.title+'، '+choice.cost:'قیمت این گزینه مشخص نیست';}))];sentences.push((has('create_app')?'میزبانی ساخته می‌شود':'ظرفیت میزبانی تغییر می‌کند')+' با '+choices.join('؛ ')+(localLab?'؛ این اجرا آزمایشی است و خرید واقعی ندارد.':'؛ هزینه ممکن است از همین مرحله آغاز شود و قیمت زنده تأیید نشده است.'));}
  if(has('repair_deploy','deploy','redeploy','restart')){const actions=[];if(has('repair_deploy'))actions.push('فایل‌های اجرا اصلاح می‌شوند');if(has('deploy'))actions.push('نسخهٔ انتخابی منتشر می‌شود');if(has('redeploy'))actions.push('کد نسخهٔ قبلی برمی‌گردد، بدون بازگردانی داده‌ها و تنظیمات');if(has('restart'))actions.push('سایت دوباره راه‌اندازی می‌شود');sentences.push(actions.join(' و ')+'؛ احتمال خطا یا قطعی وجود دارد'+(has('restart')?' و کارهای ذخیره‌نشده ممکن است از دست بروند':'')+'.');}
  if(has('scale')){const values=changes.filter(change=>change.action==='scale').map(change=>change.parameters?.scale);sentences.push(values.includes(0)?'سایت خاموش می‌شود و برای بازدیدکنندگان باز نخواهد شد؛ روشن‌کردن دوباره به تأیید تازه نیاز دارد.':'سایت روشن می‌شود؛ هزینه و وضعیت اجرا پس از تغییر بررسی می‌شوند.');}
  if(has('update_env'))sentences.push('اطلاعات لازم برای اجرا تنظیم می‌شوند؛ مقدار محرمانه نمایش داده نمی‌شود و ممکن است سایت دوباره راه‌اندازی شود.');
  if(has('attach_domain','enable_ssl','create_zone','create_record')){const domains=[...new Set(changes.filter(change=>['attach_domain','enable_ssl','create_zone','create_record'].includes(change.action)).map(change=>change.parameters?.domain || change.parameters?.zone).filter(Boolean))];sentences.push('اتصال آدرس'+(domains.length?' '+domains.join('، '):' سایت')+' و تنظیمات انتخابی آن انجام می‌شود؛ نتیجه فوری نیست'+(has('create_record')?' و خطای تنظیم می‌تواند سایت یا ایمیل را قطع کند':' و ممکن است تأیید تو در حساب دامنه لازم باشد')+(has('enable_ssl')?'، آماده‌شدن اتصال امن جداگانه بررسی می‌شود':'')+'.');}
  if(changes.some(change=>!['create_app','resize','repair_deploy','deploy','redeploy','restart','scale','update_env','attach_domain','enable_ssl','create_zone','create_record'].includes(change.action)))sentences.push('اقدام دیگری در طرح وجود دارد؛ پیش از تأیید، جزئیات کامل اقدام‌ها را بخوان.');
  if(has('deploy','repair_deploy','redeploy'))sentences.push('بعد از اجرا، بازشدن سایت و نتیجهٔ تغییرات را بررسی می‌کنم.');
  return sentences;
}
function showEvidence(run){
  $('evidence').replaceChildren();const observations=run.verification?.observations || run.context?.observations;
  if(observations){$('evidence').append(node('h3','','شواهد ثبت‌شده'));for(const [key,value] of Object.entries(observations)){const item=node('details','evidence-item');const summary=node('summary');summary.append(node('span','',evidenceNames[key] || key),node('span',value.available?'':'unavailable',value.available?'دریافت شد':'در دسترس نیست'));item.append(summary,node('pre','step-args',JSON.stringify(value.available?value.data:{status:value.status,error:value.error},null,2)));if(value.id)item.append(node('p','muted',value.id+' · '+(value.collected_at || '')));$('evidence').append(item);}}
  if(run.plan?.evidence?.length){const detail=node('details','evidence-item');detail.append(node('summary','','برداشت دستیار از شواهد'));for(const item of run.plan.evidence)detail.append(node('p','muted',item));$('evidence').append(detail);}
  if(run.verification&&run.context?.observations){const detail=node('details','evidence-item');detail.append(node('summary','','وضعیت پیش از اجرا'),node('pre','step-args',JSON.stringify(run.context.observations,null,2)));$('evidence').append(detail);}
}
function inputRequests(run){return run.context?.input_requests || run.plan?.inputs || [];}
function renderQuestions(run){
  const requests=inputRequests(run);$('questions').replaceChildren();
  if(run.status==='needs_input'&&!requests.length)for(const question of run.plan?.questions || [])$('questions').append(node('p','',question));
  $('answer-form').hidden=run.status!=='needs_input';$('project-inputs').replaceChildren();
  if(run.status==='needs_input')for(const request of requests){const label=node('label','',request.label || 'اطلاعات لازم برای اجرا');const input=node('input');input.type=['secret','connection'].includes(request.kind)?'password':'text';input.dataset.inputName=request.name;input.required=true;input.autocomplete='off';input.spellcheck=false;input.dir='ltr';input.maxLength=12000;label.append(input);const hint=node('details','input-hint');hint.append(node('summary','','راهنمای این مقدار'),node('p','',request.hint || ''),node('code','',request.name));label.append(hint);$('project-inputs').append(label);}
  const needsHosting=['deploy','repair'].includes(run.request.intent)&&run.context?.exists===false&&!run.request.plan_id, options=run.context?.hosting_options || [];
  $('answer-hosting-label').hidden=!needsHosting || !options.length;$('hosting-unavailable').hidden=!needsHosting || options.length>0;
  $('answer-hosting').replaceChildren();const empty=node('option','','انتخاب کن');empty.value='';$('answer-hosting').append(empty);for(const item of options){const option=node('option','',item.title+' · '+item.cost);option.value=item.id;$('answer-hosting').append(option);}
  $('answer-hosting').required=needsHosting&&options.length>0;$('send-answer').disabled=busy || needsHosting&&!options.length;
  $('answer-message-label').hidden=Boolean(requests.length);$('answer-message').placeholder='پاسخ سؤال بالا را بنویس؛ اطلاعات محرمانه را اینجا نفرست.';
}
function render(run){
  const next=run.id+':'+run.status+':'+(run.events?.length || 0)+':'+(run.plan_digest || '');if(signature===next)return;signature=next;current=run.id;displayed=run;watching=['preparing','running','awaiting_approval'].includes(run.status);
  $('composer').hidden=true;$('run').hidden=false;$('run-title').textContent=labelFor(run.request.project,run.request.source);$('run-status').textContent=stateNames[run.status] || run.status;
  $('resume').hidden=!['preparing','running'].includes(run.status);$('recheck').hidden=!['completed','observed','needs_attention','unknown','failed'].includes(run.status);$('repair').hidden=!['failed','needs_attention','observed','completed'].includes(run.status);$('recovery').hidden=!(run.review?.previous_source&&['completed','needs_attention','failed'].includes(run.status)&&(run.plan?.steps || []).some(step=>['deploy','redeploy','repair_deploy'].includes(step.action)));
  const brief={preparing:'دارم فایل‌ها و وضعیت سایت را بررسی می‌کنم…',running:'کارهای تأییدشده در حال اجرا هستند.',completed:'اجرای کارهای تأییدشده تمام شد؛ نتیجهٔ بررسی در ادامه آمده است.',rejected:'این تغییرات انجام نشدند.',needs_input:'این اطلاعات را وارد کن تا بررسی را ادامه بدهم.',needs_attention:'اثر همهٔ تغییرات هنوز تأیید نشده است.',unknown:'نتیجه هنوز مشخص نیست؛ برای جلوگیری از اجرای تکراری، کار را تکرار نمی‌کنم.',failed:'کار متوقف شد؛ نتیجهٔ کامل تأیید نشده است.',observed:'بررسی سایت انجام شد.'};
  $('summary').textContent=run.error?plainError(run.error):run.status==='observed'?(run.plan?.summary || brief.observed):brief[run.status] || '';$('summary').hidden=!$('summary').textContent;
  $('change-review').replaceChildren();if(run.status!=='needs_input')for(const sentence of compactReview(run,config.local_lab||config.demo))$('change-review').append(node('p','',sentence));
  $('user-notes').replaceChildren();const limitations=run.plan?.limitations || [];
  if(limitations.length){const note=node('div','review-notice');note.append(node('span','','بخشی از بررسی قطعی نیست؛ '));const button=node('button','',limitations.length.toLocaleString('fa-IR')+' نکتهٔ بررسی');button.type='button';button.addEventListener('click',()=>{$('technical-details').open=true;$('limitations').scrollIntoView({block:'nearest'});});note.append(button);$('user-notes').append(note);}
  for(const result of run.results || [])if(result.next_step){$('user-notes').append(node('p','review-notice','اتصال دامنه به تأیید تو در حساب دامنه نیاز دارد؛ راهنما در جزئیات ثبت شده است.'));break;}
  renderQuestions(run);$('approval').hidden=run.status!=='awaiting_approval';$('approve').disabled=false;$('reject').disabled=false;
  $('details-label').textContent='جزئیات بررسی'+(limitations.length?' · '+limitations.length.toLocaleString('fa-IR')+' نکته':'');$('limitations').replaceChildren();for(const item of limitations)$('limitations').append(node('p','',item));$('planner-summary').textContent=run.plan?.summary || '';
  $('run-parent').textContent=run.parent_id?'درخواست قبلی: '+run.parent_id:'';$('approval-digest').textContent=run.plan_digest || '';
  $('setup-info').textContent=JSON.stringify({project:run.request.project,platform:run.context?.platform,port:run.context?.port,error:run.error,health:run.verification?.health,results:run.results,available_inputs:run.context?.available_inputs},null,2);
  $('steps').replaceChildren();for(const step of run.plan?.steps || []){const item=node('article','plan-step');item.append(node('h3','',step.title),node('p','',step.reason));const review=run.review?.changes?.find(change=>change.action===step.action&&JSON.stringify(change.parameters)===JSON.stringify(step.args));if(review){item.append(node('p','change-details',review.impact),node('p','change-details',review.risk),node('p','change-details','بازگشت: '+review.recovery));}item.append(node('pre','step-args',JSON.stringify(step.args,null,2)));$('steps').append(item);}
  $('artifact').replaceChildren();if(run.artifact?.sha256){const detail=node('details','artifact-box');detail.append(node('summary','','فایل‌های نسخهٔ آمادهٔ انتشار'),node('code','','SHA256 '+run.artifact.sha256),node('pre','file-list',(run.artifact.files || []).join('\n')));$('artifact').append(detail);}
  $('repair-preview').replaceChildren();if(run.repair?.diff){$('repair-preview').append(node('h3','','تغییر فایل‌های اجرا'),node('pre','step-args',run.repair.diff));for(const check of run.repair.checks || [])$('repair-preview').append(node('p','',check.path+' · '+check.check));}
  renderVerification(run);showEvidence(run);$('events').replaceChildren();for(const event of run.events || []){const item=node('li','',event.text);item.append(node('time','',new Date(event.at).toLocaleTimeString('fa-IR')));$('events').append(item);}
}
function renderVerification(run){
  $('verification').replaceChildren();if(!run.verification)return;const health=run.verification.health || {},stopped=run.verification.outcome==='stopped',incomplete=(run.verification.action_checks || []).some(check=>check.matched!==true)||(run.verification.configuration_checks || []).some(check=>check.matched!==true)||['failed','unknown','needs_attention'].includes(run.status),good=(health.healthy||stopped)&&!incomplete;
  const box=node('section','verification-box '+(good?'ok':'warn'));const title=stopped?'خاموش‌شدن سایت بررسی شد':health.healthy?(incomplete?'سایت باز می‌شود؛ همهٔ تغییرات تأیید نشده‌اند':'سایت باز می‌شود'):'سلامت سایت هنوز تأیید نشده است';box.append(node('h3','',title));
  if(!good)box.append(node('p','',health.healthy?'اثر همهٔ تغییرات هنوز مشخص نیست.':'نتیجهٔ اجرا را قطعی و موفق اعلام نمی‌کنم.'));
  if(!stopped)try{const url=new URL(config.local_lab&&health.local_url?health.local_url:health.url);if(url.protocol==='https:'||(config.local_lab&&url.protocol==='http:'&&url.hostname==='127.0.0.1')){url.pathname='/';url.search='';url.hash='';const link=node('a','site-link','بازکردن سایت ↗');link.href=url.href;link.target='_blank';link.rel='noreferrer';box.append(link);}}catch{}
  $('verification').append(box);
}
async function loadSources(){
  const sources=await api('/sources');$('source-picker').replaceChildren();const empty=node('option','','پوشه را انتخاب کن');empty.value='';$('source-picker').append(empty);for(const item of sources){const option=node('option','',item.label);option.value=item.source;$('source-picker').append(option);}$('source-picker').value=$('source').value;if(!config.demo&&!config.local_lab&&!sources.some(item=>item.source===$('source').value))$('source').value='';if(!sources.length)$('manual-source').open=true;
}
function selectLabProject(){const item=config.lab_projects?.find(entry=>entry.project===$('lab-project').value);if(!item)return;for(const id of ['port','platform','health','domain'])$(id).value='';$('project').value=item.project;$('source').value=item.source;$('plan').value='local-plan';$('projects').value=item.project;}
async function initialize(){
  try{config=await api('/config');$('connection').textContent='متصل';$('configuration').textContent=`Provider: ${config.provider}\nModel: ${config.model || ''}\nWorkspace: ${config.workspace}`;$('environment').hidden=!(config.demo||config.local_lab);$('environment').textContent=config.local_lab?'آزمایش محلی':config.demo?'آزمایشی':'';
    if(config.demo){$('source').value='examples/hello-node';$('project').value='my-first-site';$('plan').value='demo-plan';}
    $('lab-catalog').hidden=!config.local_lab;if(config.local_lab){$('lab-project').replaceChildren();for(const item of config.lab_projects){const option=node('option','',samples[item.source] || item.source);option.value=item.project;option.selected=Boolean(item.exists);$('lab-project').append(option);}selectLabProject();}
    setMode(mode,false);await history();await Promise.all([refreshProjects(),loadSources()]);$('error').hidden=true;
  }catch(error){fail(error);$('connection').textContent='اتصال قطع است';}
}
function newProject(){showComposer();setMode('deploy');for(const id of ['project','domain','health','port','platform','plan'])$(id).value='';$('projects').value='';if(config.local_lab){const item=config.lab_projects.find(entry=>!entry.exists) || config.lab_projects[0];if(item){$('lab-project').value=item.project;selectLabProject();}}else if(config.demo)$('plan').value='demo-plan';}
function bindUI(){
  for(const button of document.querySelectorAll('[data-mode]'))button.addEventListener('click',()=>{setMode(button.dataset.mode);document.querySelector('.more-actions').open=false;});
  $('refresh').addEventListener('click',()=>refreshProjects().catch(fail));$('lab-project').addEventListener('change',selectLabProject);
  $('projects').addEventListener('change',()=>{const name=$('projects').value;if(!name){newProject();return;}showComposer();const project=projects.get(name);for(const id of ['domain','health','port','platform'])$(id).value='';$('project').value=name;$('plan').value=project?.planID || '';$('source').value=siteSources.get(name) || '';$('source-picker').value=$('source').value;if(config.local_lab){$('lab-project').value=name;selectLabProject();}});
  $('history').addEventListener('change',()=>{const run=previousRuns.get($('history').value);if(run)openRun(run);});$('edit-request').addEventListener('click',showComposer);
  $('source-picker').addEventListener('change',()=>{$('source').value=$('source-picker').value;$('source-manual').value=$('source').value;});$('source-manual').addEventListener('input',()=>{$('source').value=$('source-manual').value;$('source-picker').value=$('source').value;});$('new-project').addEventListener('click',newProject);
  $('access-button').addEventListener('click',()=>$('access-dialog').showModal());$('save-access').addEventListener('click',()=>{sessionStorage.setItem('lyara-access',$('access-token').value);$('access-token').value='';setTimeout(initialize,0);});
  $('request-form').addEventListener('submit',async event=>{event.preventDefault();if(busy)return;busy=true;$('submit').disabled=true;$('error').hidden=true;try{let project=$('project').value;if(!project){if(!['deploy','repair'].includes(mode))throw new Error('اول سایت را از بالای صفحه انتخاب کن.');project='site-'+crypto.randomUUID().replaceAll('-','').slice(0,10);$('project').value=project;}if(['deploy','repair'].includes(mode)&&!$('source').value)throw new Error('پوشهٔ سایت را انتخاب کن.');const request={message:$('message').value,intent:mode,project,source:$('source').value || '.',plan_id:$('plan').value,platform:$('platform').value,port:$('port').value?Number($('port').value):null,domain:$('domain').value.trim(),health_url:$('health').value};const run=await api('/runs',{method:'POST',body:JSON.stringify(request)});signature='';render(run);history().catch(fail);}catch(error){fail(error);}finally{busy=false;$('submit').disabled=false;}});
  async function decide(approved){if(!current||displayed?.status!=='awaiting_approval'||$('approve').disabled)return;$('approve').disabled=true;$('reject').disabled=true;try{const run=await api('/runs/'+current+'/approval',{method:'POST',body:JSON.stringify({approved,plan_digest:displayed.plan_digest})});if(run.status!=='awaiting_approval'){signature='';render(run);}history().catch(fail);}catch(error){fail(error);$('approve').disabled=false;$('reject').disabled=false;}}
  $('approve').addEventListener('click',()=>decide(true));$('reject').addEventListener('click',()=>decide(false));
  $('resume').addEventListener('click',async()=>{if(current)try{const run=await api('/runs/'+current+'/resume',{method:'POST'});signature='';render(run);}catch(error){fail(error);}});
  $('answer-form').addEventListener('submit',async event=>{event.preventDefault();if(!current||busy)return;busy=true;$('send-answer').disabled=true;$('error').hidden=true;try{const plan=$('answer-hosting').value,inputs=[...$('project-inputs').querySelectorAll('input[data-input-name]')],message=inputs.length?'':$('answer-message').value.trim();if(!$('answer-hosting-label').hidden&&!plan)throw new Error('گزینهٔ میزبانی را انتخاب کن.');let body,path;if(inputs.length){const values=Object.fromEntries(inputs.map(input=>[input.dataset.inputName,input.value]));if(Object.values(values).some(value=>!value))throw new Error('اطلاعات لازم را کامل کن.');body={values,plan_id:plan || null};path='/inputs';}else{if(!message&&!plan)throw new Error('پاسخ را بنویس یا گزینهٔ هزینه را انتخاب کن.');body={message:message || 'گزینهٔ هزینه‌ای که انتخاب کردم را استفاده کن.',plan_id:plan || null};path='/answer';}const run=await api('/runs/'+current+path,{method:'POST',body:JSON.stringify(body)});for(const input of inputs)input.value='';$('answer-message').value='';openRun(run);history().catch(fail);}catch(error){fail(error);}finally{busy=false;$('send-answer').disabled=false;}});
  for(const name of ['repair','recovery'])$(name).addEventListener('click',async()=>{if(!current||busy)return;busy=true;$(name).disabled=true;try{const run=await api('/runs/'+current+'/'+name,{method:'POST'});openRun(run);history().catch(fail);}catch(error){fail(error);}finally{busy=false;$(name).disabled=false;}});
  $('recheck').addEventListener('click',async()=>{if(!current)return;const id=current;$('recheck').disabled=true;try{const run=await api('/runs/'+id+'/recheck',{method:'POST'});if(current===id){signature='';render(run);}history().catch(fail);}catch(error){fail(error);}finally{$('recheck').disabled=false;}});
  $('download-report').addEventListener('click',async()=>{if(!current)return;try{const response=await fetch('/api/runs/'+current+'/report?format=markdown',{headers:token()?{Authorization:'Bearer '+token()}: {}});if(!response.ok)throw new Error('گزارش دریافت نشد؛ اتصال و دسترسی را بررسی کن.');const url=URL.createObjectURL(await response.blob()),link=node('a');link.href=url;link.download='site-report.md';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){fail(error);}});
  setInterval(async()=>{if(!current||!watching||document.hidden)return;const id=current;try{const run=await api('/runs/'+id);if(current!==id)return;const old=signature;render(run);if(old!==signature)history().catch(fail);}catch(error){fail(error);}},1500);
  setMode('deploy');initialize();
}
if(typeof document!=='undefined')bindUI();
if(typeof module!=='undefined')module.exports={compactReview,inputRequests};
