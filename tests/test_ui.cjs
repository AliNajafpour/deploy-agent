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
assert.ok(compactReview(run,true).join(' ').includes('خرید واقعی ندارد'));
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
console.log('UI review, consequences, input requests and DOM checks passed.');
