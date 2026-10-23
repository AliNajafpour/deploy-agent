// Run with: node tests/test_ui.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {compactReview, inputRequests} = require('../lyara/static/app.js');

const actions = ['create_app','resize','deploy','repair_deploy','redeploy','restart','scale','update_env','attach_domain','enable_ssl','create_zone','create_record'];
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
  querySelectorAll(){return this.children.flatMap(child=>[...(child.tag==='input'?[child]:[]),...child.querySelectorAll()]);}
}
async function checkUploadFlow(){
  const elements=Object.fromEntries(ids.map(id=>[id,new Element()])),calls=[];
  let resolveUpload, uploadError=false;
  const context=vm.createContext({document:{getElementById:id=>elements[id],querySelectorAll:()=>[],createElement:tag=>Object.assign(new Element(),{tag})},sessionStorage:{getItem:()=> 'private-token'},crypto:require('node:crypto'),URL,setInterval(){},setTimeout,fetch:async(url,options)=>{
    calls.push({url,options});let data;
    if(url==='/api/config')data={provider:'luna',hosting_connected:false};
    else if(url==='/api/projects')data={projects:[]};
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
  console.log('UI upload ordering, failures, connection questions, secret inputs, safe links and review checks passed.');
}
checkUploadFlow().catch(error=>{console.error(error);process.exitCode=1;});
