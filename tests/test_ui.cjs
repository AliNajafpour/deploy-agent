// Run with: node tests/test_ui.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {compactReview, inputRequests} = require('../lyara/static/app.js');

const actions = ['create_network','create_app','resize','deploy','repair_deploy','redeploy','restart','scale','update_env','attach_domain','enable_ssl','create_zone','create_record'];
const run = {context:{hosting_options:[{id:'small',title:'کوچک',cost:'ماهانه ۱۰۰ تومان'}]},review:{changes:actions.map(action=>({action,parameters:{plan_id:'small',scale:0,domain:'example.com'}}))}};
const review = compactReview(run).join('\n');
assert.ok(compactReview(run).length <= 8, 'all validated actions fit the review');
for (const text of ['ماهانه ۱۰۰ تومان','هزینه','قطعی','ذخیره‌نشده','خاموش','محرمانه','example.com','ایمیل','اتصال امن','بدون بازگردانی داده‌ها']) assert.ok(review.includes(text), `consequence remains visible: ${text}`);
assert.deepEqual(compactReview({}), []);
assert.ok(compactReview({review:{changes:[{action:'create_app',parameters:{plan_id:'missing'}}]}})[0].includes('قیمت این گزینه مشخص نیست'));
assert.ok(compactReview({review:{changes:[{action:'scale',parameters:{scale:1}}]}})[0].includes('روشن'));
assert.ok(compactReview({review:{changes:[{action:'unrecognized'}]}})[0].includes('جزئیات'));
const inputs=[{name:'DATABASE_URL',kind:'connection'}];
assert.equal(inputRequests({context:{input_requests:inputs}}),inputs);
assert.equal(inputRequests({plan:{inputs}}),inputs);
const html=fs.readFileSync(path.join(__dirname,'../lyara/static/index.html'),'utf8');
const js=fs.readFileSync(path.join(__dirname,'../lyara/static/app.js'),'utf8');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);
assert.equal(ids.length,new Set(ids).size,'IDs are unique');
for(const [,id] of js.matchAll(/\$\('([^']+)'\)/g))assert.ok(ids.includes(id),`JS target exists: ${id}`);
assert.ok(html.indexOf('id="approval"')<html.indexOf('id="change-review"'),'approval precedes plan review');
assert.ok(!/sidebar|id="welcome"|demo-banner/.test(html),'removed filler does not return');
assert.ok(!/lab-project|lab-catalog|source-picker|source-manual|manual-source/.test(html),'upload replaces fixture and server path controls');
assert.ok(!/samples|lab_projects|local_lab|config\.demo|selectLabProject|آزمایشی/.test(js+html),'no sample sites or simulated hosting in product UI');
assert.match(html, /id="site-upload" type="file" accept="\.zip,application\/zip"/);

// Exercise the actual form handlers without a browser, server, or model call.
const vm = require('node:vm');
class Element {
  constructor(){this.value='';this.textContent='';this.children=[];this.files=[];this.hidden=false;this.dataset={};this.listeners={};this.classList={toggle(){}};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  addEventListener(name,callback){this.listeners[name]=callback;}
  setAttribute(){}
  focus(){}
  select(){this.selected=true;}
  querySelectorAll(){return this.children.flatMap(child=>[...(child.tag==='input'?[child]:[]),...child.querySelectorAll()]);}
}
async function checkUploadFlow(){
  const elements=Object.fromEntries(ids.map(id=>[id,new Element()])),calls=[];
  let resolveUpload,resolveGithub,uploadError=false,githubError=false;let controlState={stopped:false,generation:0,managed_projects:[]};
  const context=vm.createContext({document:{getElementById:id=>elements[id],querySelectorAll:()=>[],createElement:tag=>Object.assign(new Element(),{tag})},sessionStorage:{getItem:()=> 'private-token'},navigator:{clipboard:{writeText:async()=>{throw new Error('denied');}}},crypto:require('node:crypto'),URL,setInterval(){},setTimeout,fetch:async(url,options)=>{
    calls.push({url,options});let data;
    if(url==='/api/config')data={provider:'luna',hosting_connected:false};
    else if(url==='/api/projects')data={projects:[]};
    else if(url==='/api/runs/run-1/handoff?format=markdown')return {ok:true,text:async()=> '# safe handoff'};
    else if(url==='/api/controls'){if(options.method==='PUT'){const change=JSON.parse(options.body);assert.equal(change.expected_generation,controlState.generation);controlState={...controlState,stopped:change.enabled,generation:controlState.generation+1};}data=controlState;}
    else if(url==='/api/managed/site-one'){const change=JSON.parse(options.body);assert.equal(change.expected_generation,controlState.generation);controlState={...controlState,generation:controlState.generation+1,managed_projects:change.enabled?['site-one']:[]};data=controlState;}
    else if(url==='/api/runs/run-1')data={id:'run-1',status:'invalidated',request:{project:'site-one',intent:'repair'},context:{exists:true,controls:controlState}};
    else if(url==='/api/github'){await new Promise(resolve=>{resolveGithub=resolve;});if(githubError)return {ok:false,status:400,json:async()=>({detail:'Invalid repository'})};data={source:'upload:commit-fixed',label:'github-site',github:{commit:'a'.repeat(40)}};}
    else if(url==='/api/monitoring/site-one')data={project:'site-one',enabled:false};
    else if(url==='/api/monitoring'||url==='/api/incidents')data=[];
    else if(url==='/api/sources'||url==='/api/runs'&&!options.method)data=[];
    else if(url==='/api/uploads'){await new Promise(resolve=>{resolveUpload=resolve;});if(uploadError)return {ok:false,status:400,json:async()=>({detail:'Invalid ZIP'})};data={source:'upload:opaque-id',label:'my-site',files:2,bytes:100};}
    else if(url==='/api/runs')data={id:'run-1',status:'needs_input',request:JSON.parse(options.body),context:{exists:false,connection_required:true},plan:{questions:['حساب میزبانی را متصل کن.']}};
    else throw new Error('Unexpected request '+url);
    return {ok:true,json:async()=>data};
  }});
  vm.runInContext(js,context);
  await new Promise(setImmediate);
  assert.equal(elements.project.value,'');assert.equal(elements.source.value,'');assert.equal(elements.plan.value,'');assert.equal(elements.message.value,'');
  const submit=()=>elements['request-form'].listeners.submit({preventDefault(){}});
  elements['site-upload'].files=[{name:'my site.zip',size:100}];elements['site-upload'].listeners.change();elements.message.value='سایتم را منتشر کن';
  elements.bundle.value='chosen-bundle';elements.network.value='chosen-network';
  const pending=submit();
  assert.equal(elements.submit.disabled,true);
  assert.equal(calls.filter(call=>call.url==='/api/runs'&&call.options.method==='POST').length,0,'run waits until ZIP upload finishes');
  const upload=calls.find(call=>call.url==='/api/uploads');
  assert.equal(upload.options.headers['Content-Type'],'application/zip');
  assert.equal(upload.options.headers['X-File-Name'],'my%20site.zip');
  assert.equal(upload.options.headers.Authorization,'Bearer private-token');
  assert.equal(upload.options.body,elements['site-upload'].files[0],'send raw ZIP bytes');
  resolveUpload();await pending;
  const created=calls.find(call=>call.url==='/api/runs'&&call.options.method==='POST');
  assert.equal(JSON.parse(created.options.body).source,'upload:opaque-id');
  assert.equal(JSON.parse(created.options.body).bundle_plan_id,'chosen-bundle');assert.equal(JSON.parse(created.options.body).network,'chosen-network','editing keeps the reviewed bundle and network');
  assert.equal(elements['run-title'].textContent,'my-site');
  assert.equal(elements['answer-form'].hidden,true,'hosting connection is not an answerable text question');
  assert.equal(elements.submit.disabled,false);assert.equal(elements['site-upload'].disabled,false);
  vm.runInContext("renderQuestions({status:'needs_input',request:{intent:'deploy'},context:{exists:false,connection_required:true,input_requests:[{name:'DATABASE_URL',kind:'connection'}]}})",context);
  assert.equal(elements['answer-form'].hidden,false,'secrets can still be collected without hosting');
  assert.equal(elements['project-inputs'].querySelectorAll()[0].type,'password');
  assert.equal(elements['send-answer'].disabled,false);
  vm.runInContext("config={hosting_connected:true,capabilities:['monitoring']};projects.set('site-one',{name:'My site'});$('project').value='site-one';monitored=[{project:'site-one',enabled:true,status:'unhealthy',failure_count:2,last_check:'2026-10-23T10:00:00Z'}];incidents=[{project:'site-one',state:'open',run_id:'incident-run'}];renderMonitoring();",context);
  assert.equal(elements.monitoring.hidden,false);assert.equal(elements['watch-toggle'].textContent,'توقف پایش');
  assert.ok(elements['watch-status'].textContent.includes('پیاپی'));
  assert.equal(elements.incidents.children[0].children[1].textContent,'بررسی و پیشنهاد راه‌حل');
  await elements['watch-toggle'].listeners.click();
  const toggle=calls.find(call=>call.url==='/api/monitoring/site-one');
  assert.equal(toggle.options.method,'PUT');assert.equal(JSON.parse(toggle.options.body).enabled,false);
  assert.ok(!calls.some(call=>call.url.endsWith('/approval')),'monitoring never approves cloud changes');
  elements['new-project'].listeners.click();uploadError=true;
  elements['site-upload'].files=[{name:'bad.zip',size:10}];elements['site-upload'].listeners.change();
  const rejected=submit();resolveUpload();await rejected;
  assert.equal(calls.filter(call=>call.url==='/api/runs'&&call.options.method==='POST').length,1,'rejected upload never starts a run');
  assert.equal(elements.source.value,'');assert.equal(elements.composer.hidden,false);assert.equal(elements.error.hidden,false);assert.equal(elements.submit.disabled,false);
  const uploads=calls.filter(call=>call.url==='/api/uploads').length;
  elements['site-upload'].files=[{name:'empty.zip',size:0}];elements['site-upload'].listeners.change();await submit();
  assert.equal(calls.filter(call=>call.url==='/api/uploads').length,uploads,'empty files are rejected before upload');
  elements['site-upload'].files=[];elements.project.value='existing';elements.source.value='.';
  await submit();
  assert.equal(calls.filter(call=>call.url==='/api/runs'&&call.options.method==='POST').length,1,'server workspace is never a web deployment source');
  for(const health of [{healthy:false,url:'https://example.com'},{healthy:true,url:'javascript:alert(1)'},{healthy:true,url:'http://127.0.0.1:8000'},{healthy:true,url:'https://private:secret@example.com'}]){
    context.checkHealth=health;vm.runInContext("renderVerification({status:'completed',verification:{health:checkHealth}})",context);
    assert.ok(!elements.verification.children[0].children.some(child=>child.tag==='a'),'unchecked or unsafe links stay hidden');
  }
  elements['new-project'].listeners.click();elements['github-url'].value='https://github.com/owner/site';elements['github-url'].listeners.input();elements.message.value='سایتم را منتشر کن';
  const githubPending=submit();assert.equal(calls.filter(call=>call.url==='/api/runs'&&call.options.method==='POST').length,1,'run waits for a fixed GitHub source');resolveGithub();await githubPending;
  assert.equal(JSON.parse(calls.filter(call=>call.url==='/api/runs'&&call.options.method==='POST').at(-1).options.body).source,'upload:commit-fixed');assert.equal(elements['run-title'].textContent,'github-site');
  vm.runInContext("config={hosting_connected:true,capabilities:['emergency','handoff']};controls={stopped:false,generation:0,managed_projects:[]};$('project').value='site-one';projects.set('site-one',{name:'Site'});render({id:'run-1',status:'awaiting_approval',plan_digest:'a'.repeat(64),request:{project:'site-one',intent:'repair'},context:{exists:true},model_usage:[{model:'Luna',total_tokens:123,cost_kind:'unknown',cost_usd:null}],build_progress:{state:'building'}})",context);
  assert.equal(elements.approve.disabled,true,'unmanaged existing site cannot approve');assert.equal(elements['build-progress'].textContent,'در حال ساخت');assert.ok(elements['model-usage'].children[0].children[1].textContent.includes('هزینه: نامعلوم'));
  await elements['manage-toggle'].listeners.click();assert.ok(calls.some(call=>call.url==='/api/managed/site-one'&&JSON.parse(call.options.body).enabled));
  await elements['emergency-toggle'].listeners.click();assert.equal(elements['emergency-status'].hidden,false);assert.equal(elements.approval.hidden,true,'invalidated plans cannot be approved');assert.equal(controlState.stopped,true);
  await elements['emergency-toggle'].listeners.click();assert.equal(elements['emergency-status'].hidden,true);assert.equal(elements.approval.hidden,true,'removing stop does not revive a stale proposal');
  await elements['copy-handoff'].listeners.click();assert.equal(elements['handoff-text'].hidden,false);assert.equal(elements['handoff-text'].value,'# safe handoff');assert.equal(elements['handoff-text'].selected,true,'clipboard refusal keeps handoff selectable');
  vm.runInContext("renderVerification({status:'needs_attention',verification:{health:{healthy:true},comparison:{outcome:'هنوز مشکل دارد',message:'هنوز مشکل دارد؛ ۲ خطا'}}})",context);assert.equal(elements.verification.children[0].children[0].textContent,'هنوز مشکل دارد؛ ۲ خطا');
  vm.runInContext("displayed={context:{hosting_options:[{id:'plan',choice_id:'unique-network-choice',title:'مناسب',cost:'نامعلوم',recommended:true}]}};config.hosting_connected=true;renderQuestions({status:'needs_input',request:{intent:'deploy'},context:{exists:false,hosting_options:displayed.context.hosting_options}})",context);
  assert.equal(elements['answer-hosting'].value,'unique-network-choice');await elements['answer-form'].listeners.submit({preventDefault(){}}).catch(()=>{});
  const answer=calls.find(call=>call.url==='/api/runs/run-1/answer');assert.equal(JSON.parse(answer.options.body).hosting_choice,'unique-network-choice');assert.ok(!('plan_id' in JSON.parse(answer.options.body)));
  console.log('UI ZIP and fixed GitHub source flow, failures, secret inputs, scope, emergency invalidation, unknown costs and review checks passed.');
}
checkUploadFlow().catch(error=>{console.error(error);process.exitCode=1;});
