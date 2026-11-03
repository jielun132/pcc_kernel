// Exercise the shipped report script with a minimal DOM; no rendering claims.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const shipped=JSON.parse(execFileSync(process.argv[2]||'python',['-c','import json; from pcc_tool.render.navigation import SCRIPT; from pcc_tool.render.assessment import ASSESSMENT_SCRIPT; from pcc_tool.plan.rule_catalog import *; print(json.dumps(dict(script=ASSESSMENT_SCRIPT+SCRIPT, config=dict(version=ASSESSMENT_VERSION, rules=RULE_CATALOG, selected=DEFAULT_RULES, thresholds=DEFAULT_THRESHOLDS, report_fingerprint="test-report", rules_fingerprint="test-rules"))))'],{encoding:'utf8'}));
let script=shipped.script, config=shipped.config;
class Element{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes={};this.listeners={};this.parentElement=null;this.value='';this.hidden=false;this.checked=false;this.className='';this._text='';}
  set textContent(t){this._text=String(t);this.children=[];}get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
  append(...nodes){nodes.forEach(n=>{n.parentElement=this;this.children.push(n);});}
  replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes);}
  setAttribute(k,v){this.attributes[k]=String(v);if(k==='id')this.id=String(v);if(k==='href')this.href=String(v);}
  getAttribute(k){return k==='href'?this.href:this.attributes[k];}
  addEventListener(k,f){(this.listeners[k]??=[]).push(f);}
  fire(k){const e={target:this,key:'Enter',preventDefault(){}};for(let n=this;n;n=n.parentElement)for(const f of n.listeners[k]||[])f(e);}
  click(){this.fire('click');}
  matches(selector){return selector.split(',').some(s=>{s=s.trim();if(s==='a[href^="#"]')return this.tagName==='A'&&this.href?.startsWith('#');if(s[0]==='.')return this.className.split(' ').includes(s.slice(1));if(s[0]==='['){const key=s.slice(1,-1).replace(/^data-/,'').replace(/-([a-z])/g,(_,c)=>c.toUpperCase());return key in this.dataset;}return this.tagName===s.toUpperCase();});}
  closest(s){for(let e=this;e;e=e.parentElement)if(e.matches(s))return e;return null;}
  all(){return this.children.flatMap(c=>[c,...c.all()]);}
  querySelectorAll(s){return this.all().filter(e=>e.matches(s));}
  querySelector(s){return this.querySelectorAll(s)[0]||null;}
  get classList(){return {contains:c=>this.className.split(' ').includes(c)};}
  scrollIntoView(){this.scrolled=true;}
  focus(){this.focused=true;}select(){this.selected=true;}
  after(e){this.parentElement.append(e);}remove(){this.parentElement.children=this.parentElement.children.filter(e=>e!==this);}
  set innerHTML(value){
    this.replaceChildren();this.markup=value;
    for(const match of value.matchAll(/<article id="(query-\d+)"/g)){
      const card=new Element('article');card.id=match[1];
      const assessment=new Element();assessment.className='query-assessment';card.append(assessment);
      const toolbar=new Element();toolbar.className='plan-toolbar';card.append(toolbar);
      const feedback=new Element('span');feedback.className='copy-status';card.append(feedback);
      for(const side of ['old','new','both']){const b=new Element('button');b.dataset.copyPlan=side;card.append(b);}
      const toggle=new Element('input');toggle.className='expand-plan';card.append(toggle);
      for(const changed of [false,true]){const cell=new Element('td');cell.className='plan-cell'+(changed?' changed-cell':'');const detail=new Element('details');detail.className='node-details';detail.dataset.defaultOpen=String(changed);detail.id=card.id+'-diff-'+(changed?2:1);cell.append(detail);card.append(cell);}
      this.append(card);
    }
  }
}
const doc=new Element('document'), nodes={};
for(const id of ['report-data','search','status','group-mode','page-size','group-list','group-help','schema-scope','scope-count','jump-number','jump-query','nav-message','sql-nav','review-workspace','page-state','previous-page','next-page','visible-count','clear-group','empty','sql-cards','detail-position','previous-query','next-query']){
  nodes[id]=new Element(['search','jump-number'].includes(id)?'input':['previous-page','next-page','jump-query','clear-group'].includes(id)?'button':'div');nodes[id].id=id;doc.append(nodes[id]);
}
nodes['group-mode'].value='all';nodes['page-size'].value='10';
const metrics=[];for(const value of ['', 'same','changed','unavailable']){const b=new Element('button');b.dataset.filter=value;metrics.push(b);doc.append(b);}
doc.getElementById=id=>doc.all().find(e=>e.id===id)||null;doc.createElement=tag=>new Element(tag);
const nativeQuery=doc.querySelectorAll.bind(doc);doc.querySelectorAll=s=>s==='button[data-filter]'?metrics:s==='#sql-nav a'?nodes['sql-nav'].querySelectorAll('a'):nativeQuery(s);
let copied='',fallback=true;doc.execCommand=()=>fallback;
let records;
if(process.argv[3]){
  const html=fs.readFileSync(process.argv[3],'utf8');records=JSON.parse(html.match(/<script type="application\/json" id="report-data">([\s\S]*?)<\/script>/)[1]);
  script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
  config=JSON.parse(html.match(/<script type="application\/json" id="assessment-config">([\s\S]*?)<\/script>/)[1]);
}else records=Array.from({length:1053},(_,i)=>({id:`query-${i+1}`,number:i+1,schema:['sales','reports','archive'][i%3],status:['same','changed','not_run'][i%3],category:['same','changed','unavailable'][i%3],attention:i%3!==0,needsSample:i%3===2,planGroup:i%3===2?'':`shape-${i%7}`,planTitle:'HashJoin → IndexJoin',preview:`SELECT ${i+1} <script>`,search:`select ${i+1} table:orders`,oldPlan:`root\n└─old-${i+1}`,newPlan:`root\n└─new-${i+1}`,html:`<article id="query-${i+1}"></article>`}));
nodes['report-data'].textContent=JSON.stringify(records);
records.forEach(r=>{r.queryId??='q-'+r.number;r.assessmentEvidence??={scope:r.status,event_refs:r.status==='changed'?['1']:[],matches:r.status==='changed'?[{id:'m1',rule_id:'E01',direction:'equivalent',event_refs:['1'],old_nodes:['n0'],new_nodes:['n0'],guards:[],reason:'展示别名'}]:[],blockers:[],hints:[]};});
const archivedAssessments=records.map(r=>r.assessment?JSON.parse(JSON.stringify(r.assessment)):null);
nodes['report-data'].textContent=JSON.stringify(records);
for(const id of ['assessment-config','assessment-panel','assessment-state','assessment-counts','run-assessment','export-assessment-json','export-assessment-csv',...Object.keys(config.thresholds).map(k=>'threshold-'+k)]){
  const n=new Element(id.startsWith('threshold')?'input':id.startsWith('export-')||id==='run-assessment'?'button':'div');n.id=id;nodes[id]=n;doc.append(n);
}
nodes['assessment-config'].textContent=JSON.stringify(config);
Object.entries(config.thresholds).forEach(([k,v])=>nodes['threshold-'+k].value=v);
for(const rule of config.rules){const c=new Element('input');c.dataset.ruleId=rule.id;c.checked=config.selected.includes(rule.id);doc.append(c);}
for(const key of ['default','all','none']){const b=new Element('button');b.dataset.rulePreset=key;doc.append(b);}
let downloaded;
const sandbox={document:doc,navigator:{clipboard:{writeText:async text=>{copied=text;}}},location:{hash:''},window:{addEventListener(){}},console,
  Blob,URL:{createObjectURL:blob=>{downloaded=blob;return 'blob:test';},revokeObjectURL(){}},setTimeout:f=>f()};
doc.body=doc;
vm.createContext(sandbox);vm.runInContext(script,sandbox);
const read=code=>vm.runInContext(code,sandbox),change=(id,value)=>{nodes[id].value=value;nodes[id].fire(id==='search'?'input':'change');};
const click=el=>{assert.ok(el);el.fire('click');};
async function checks(){
  assert.equal(nodes['sql-cards'].children.length,1);
  assert.equal(read('filtered.length'),records.length);assert.equal(nodes['sql-nav'].children.length,Math.min(10,records.length));
  assert.equal(nodes['previous-query'].disabled,true);
  click(nodes['sql-nav'].children[4]);assert.equal(nodes['sql-cards'].children[0].id,'query-5');assert.equal(nodes['sql-cards'].children.length,1);
  click(nodes['previous-query']);assert.equal(nodes['sql-cards'].children[0].id,'query-4');
  nodes['jump-number'].value='10';click(nodes['jump-query']);click(nodes['next-query']);assert.equal(nodes['sql-cards'].children[0].id,'query-11');assert.equal(read('page'),1);
  click(nodes['previous-query']);assert.equal(nodes['sql-cards'].children[0].id,'query-10');assert.equal(read('page'),0);
  click(nodes['next-page']);assert.equal(nodes['sql-cards'].children[0].id,'query-11');
  change('page-size','20');assert.equal(nodes['sql-cards'].children.length,1);assert.equal(nodes['sql-nav'].children.length,Math.min(20,records.length));assert.equal(read('page'),0);
  nodes['jump-number'].value=String(records.length);click(nodes['jump-query']);assert.ok(doc.getElementById('query-'+records.length));
  assert.equal(nodes['next-query'].disabled,true);
  change('status','unavailable');assert.equal(read('filtered.length'),records.filter(r=>r.category==='unavailable').length);
  const before=read('page');nodes['jump-number'].value='100000';click(nodes['jump-query']);assert.equal(read('page'),before);
  change('status','');change('search','nothing-can-match-this-string');assert.equal(nodes['empty'].hidden,false);assert.equal(nodes['sql-cards'].children.length,0);
  nodes['jump-number'].value='1';click(nodes['jump-query']);assert.equal(nodes['search'].value,'');assert.ok(doc.getElementById('query-1'));
  change('group-mode','schema');
  nodes['group-list'].scrollTop=120;
  const schema=records[0].schema;click(nodes['group-list'].children.find(b=>b.dataset.groupKey===schema));assert.equal(read('filtered.length'),records.filter(r=>r.schema===schema).length);
  assert.equal(nodes['group-list'].scrollTop,120);
  const skip=nodes['schema-scope'].querySelectorAll('input').find(c=>c.dataset.skipSchema===schema);skip.checked=true;skip.fire('change');assert.equal(read('filtered.some(r=>r.schema===records[0].schema)'),false);
  nodes['jump-number'].value='1';click(nodes['jump-query']);assert.equal(skip.checked,false);assert.ok(doc.getElementById('query-1'));
  change('group-mode','plan');const plan=records.find(r=>r.planGroup);click(nodes['group-list'].children.find(b=>b.dataset.groupKey===plan.planGroup));assert.equal(read('filtered.length'),records.filter(r=>r.planGroup===plan.planGroup).length);
  click(nodes['clear-group']);assert.equal(read('filtered.length'),records.length);
  const group=new Element('button');group.dataset.group='query-2,query-12';doc.append(group);click(group);assert.equal(read('filtered.length'),2);
  const link=new Element('a');link.href='#query-'+records.length+'-diff-2';doc.append(link);click(link);assert.ok(doc.getElementById('query-'+records.length+'-diff-2').open);
  nodes['jump-number'].value='1';click(nodes['jump-query']);const card=doc.getElementById('query-1');
  const toggle=card.querySelector('.expand-plan');toggle.checked=true;toggle.fire('change');assert.ok(card.querySelectorAll('.node-details').every(d=>d.open));
  const evidenceLink=new Element('a');evidenceLink.href='#query-1-diff-2';doc.append(evidenceLink);click(evidenceLink);assert.equal(doc.getElementById('query-1'),card);assert.ok(card.querySelectorAll('.node-details').every(d=>d.open));
  toggle.checked=false;toggle.fire('change');assert.equal(card.querySelectorAll('.node-details')[0].open,false);assert.equal(card.querySelectorAll('.node-details')[1].open,true);
  const button=card.querySelectorAll('button').find(b=>b.dataset.copyPlan==='both');await read('copyPlan')(button);assert.equal(copied,`old · 升级前\n${records[0].oldPlan||'没有可用计划'}\n\nnew · 升级后\n${records[0].newPlan||'没有可用计划'}`);
  sandbox.navigator.clipboard.writeText=async()=>{throw new Error('denied');};await read('copyPlan')(button);assert.match(card.querySelector('.copy-status').textContent,/已复制/);assert.equal(card.querySelector('textarea'),null);
  fallback=false;await read('copyPlan')(button);assert.match(card.querySelector('.copy-status').textContent,/Cmd\+C/);assert.equal(card.querySelector('textarea').value,copied);assert.ok(card.querySelector('textarea').selected);
  // Rule selection never changes the original status, nor inherits group decisions.
  const original=JSON.stringify(records.map(r=>r.status)),changed=records.filter(r=>r.status==='changed').length;
  const initial=JSON.parse(JSON.stringify(read('assessment.payload()')));
  assert.equal(initial.results.length,records.length);
  initial.results.forEach((r,i)=>{if(archivedAssessments[i])assert.deepEqual(r.result,archivedAssessments[i]);});
  assert.equal(initial.results.filter(r=>r.result.status!=='excluded').length,changed);
  const preset=key=>doc.querySelectorAll('[data-rule-preset]').find(b=>b.dataset.rulePreset===key);
  click(preset('none'));assert.equal(read('assessment.isDirty()'),true);assert.equal(nodes['export-assessment-json'].disabled,true);
  assert.deepEqual(JSON.parse(JSON.stringify(read('assessment.payload()'))),initial);
  change('search','nothing-can-match-this-string');
  click(nodes['run-assessment']);assert.equal(read('assessment.isDirty()'),false);
  assert.equal(read('assessment.payload().results.filter(r=>r.result.status==="review").length'),changed);
  assert.equal(read('assessment.payload().results.filter(r=>r.result.status==="excluded").length'),records.length-changed);
  assert.equal(read('filtered.length'),0); // Recalculation did not narrow its scope to the empty view.
  click(nodes['assessment-counts'].children.find(b=>b.dataset.assessmentFilter==='review'));
  assert.equal(read('filtered.length'),changed);assert.equal(nodes['search'].value,'');
  const skippedSchema=records.find(r=>r.status==='changed').schema;
  const skipAgain=nodes['schema-scope'].querySelectorAll('input').find(c=>c.dataset.skipSchema===skippedSchema);skipAgain.checked=true;skipAgain.fire('change');
  click(preset('default'));click(nodes['run-assessment']);
  assert.deepEqual(JSON.parse(JSON.stringify(read('assessment.payload()'))),initial);
  const equalCount=initial.results.filter(r=>r.result.status==='equivalent').length;
  click(nodes['assessment-counts'].children.find(b=>b.dataset.assessmentFilter==='equivalent'));assert.equal(read('filtered.length'),equalCount);
  assert.equal(read('skipped.size'),0);
  if(equalCount){const equalRecord=initial.results.find(r=>r.result.status==='equivalent');assert.match(doc.getElementById('query-'+equalRecord.number).querySelector('.query-assessment').textContent,/基本等价/);}
  for(const [status,label] of [['better','变优'],['worse','变差']]){
    click(nodes['assessment-counts'].children.find(b=>b.dataset.assessmentFilter===status));
    const matches=initial.results.filter(r=>r.result.status===status);
    assert.equal(read('filtered.length'),matches.length);
    if(matches.length)assert.ok(doc.getElementById('query-'+matches[0].number).querySelector('.query-assessment').textContent.includes(label));
  }
  nodes['threshold-min_rows'].value='0';nodes['threshold-min_rows'].fire('input');click(nodes['run-assessment']);
  assert.match(nodes['assessment-state'].textContent,/阈值需/);assert.equal(read('assessment.isDirty()'),true);
  assert.deepEqual(JSON.parse(JSON.stringify(read('assessment.payload()'))),initial);
  click(preset('default'));assert.equal(read('assessment.isDirty()'),false);
  click(nodes['export-assessment-json']);assert.deepEqual(JSON.parse(await downloaded.text()),initial);
  click(nodes['export-assessment-csv']);const csv=await downloaded.text();assert.match(csv,/report_fingerprint/);assert.equal(csv.split('\r\n').length,records.length+1);
  assert.ok(read(`PCCAssessment.csv({...assessment.payload(),results:[{...assessment.payload().results[0],schema:'=1+1'}]})`).includes('"\'=1+1"'));
  assert.equal(JSON.stringify(records.map(r=>r.status)),original);
  click(metrics[0]);assert.equal(read('filtered.length'),records.length);
  console.log(`Rule selection, full-scope recalculation, stale state, thresholds, filters and JSON/CSV exports passed (${changed} changed SQLs).`);
  console.log(`Navigation, paging, schema/plan groups, deferred scopes, deep links and clipboard paths passed (${records.length} SQLs).`);
}
checks().catch(error=>{console.error(error);process.exitCode=1;});
