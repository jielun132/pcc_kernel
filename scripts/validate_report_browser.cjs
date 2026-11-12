// Optional real-browser acceptance. Node >=22 and an explicitly supplied Chrome executable.
// Usage: node scripts/validate_report_browser.cjs report.html /path/to/chrome evidence-dir
const fs=require('node:fs'),fsp=require('node:fs/promises'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process'),{pathToFileURL}=require('node:url'),assert=require('node:assert/strict');
const [report,chrome,output]=process.argv.slice(2);
if(!report||!chrome||!output)throw Error('Supply report.html, Chrome executable and evidence directory');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const destination=path.resolve(output);fs.mkdirSync(destination,{recursive:true});
const profile=fs.mkdtempSync(path.join(os.tmpdir(),'pcc-browser-'));
const processChrome=spawn(chrome,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
  '--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','--user-data-dir='+profile,'about:blank'],{stdio:'ignore'});
let socket,id=0;const pending=new Map(),errors=[];
function call(method,params={}){return new Promise((resolve,reject)=>{
  const key=++id,timer=setTimeout(()=>{pending.delete(key);reject(Error('CDP timeout: '+method));},30000);
  pending.set(key,{resolve:value=>{clearTimeout(timer);resolve(value);},reject});socket.send(JSON.stringify({id:key,method,params}));
});}
async function evaluate(expression){const value=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  if(value.exceptionDetails)throw Error(value.exceptionDetails.exception?.description||value.exceptionDetails.text);
  return value.result.value;}
async function waitFor(expression){for(let i=0;i<150;i++){if(await evaluate(expression))return;await pause(100);}throw Error('Page did not become ready');}
async function change(selector,value,event='change'){await evaluate(`(()=>{const n=document.querySelector(${JSON.stringify(selector)});n.value=${JSON.stringify(value)};n.dispatchEvent(new Event(${JSON.stringify(event)},{bubbles:true}));})()`);}
async function click(selector){await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);}
async function screenshot(name){const {data}=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(destination,name+'.png'),Buffer.from(data,'base64'));}
async function main(){
  let port;
  for(let i=0;i<200;i++){try{port=(await fsp.readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0];break;}catch{await pause(50);}}
  assert.ok(port,'Chrome did not start');
  const targets=await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
  socket.onmessage=event=>{const value=JSON.parse(event.data);if(value.id){const p=pending.get(value.id);if(p){pending.delete(value.id);value.error?p.reject(Error(value.error.message)):p.resolve(value.result);}}
    if(value.method==='Runtime.exceptionThrown')errors.push(value.params.exceptionDetails.exception?.description||value.params.exceptionDetails.text);};
  await call('Runtime.enable');await call('Page.enable');
  await call('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:destination});
  await call('Page.navigate',{url:pathToFileURL(path.resolve(report)).href});
  await waitFor(`document.readyState==='complete'&&typeof assessment!=='undefined'&&!!assessment`);
  const total=await evaluate('records.length'),facts=await evaluate('JSON.stringify(records.map(r=>r.status))');
  const layouts=[];
  for(const width of [1440,1024,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
    await evaluate('window.scrollTo(0,0)');await pause(100);
    const sizes=await evaluate(`({width:innerWidth,scroll:document.documentElement.scrollWidth,body:document.body.scrollWidth})`);
    assert.ok(sizes.scroll<=width+1,'Page-wide overflow: '+JSON.stringify(sizes));layouts.push(sizes);
    await screenshot('overview-'+width);
    await evaluate(`document.querySelector('#sql-cards').scrollIntoView({block:'start'})`);await pause(50);
    assert.ok(await evaluate(`Array.from(document.querySelectorAll('.manual-review,.plan-scroll,.query-assessment')).every(n=>n.getBoundingClientRect().right<=innerWidth+1)`),'Detail escapes viewport');
    await screenshot('detail-'+width);
  }
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await evaluate(`document.body.style.zoom='1.25'`);await pause(50);
  assert.ok(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'Zoom overflow');await screenshot('zoom-125');
  await evaluate(`document.body.style.zoom='1'`);
  await change('#jump-number','1');await click('#jump-query');
  const note='现场人工复核：保留索引；长备注 '+('订单计划 / '.repeat(40));
  await change('[data-review-note]',note,'input');await change('[data-review-conclusion]','equivalent');await click('[data-review-checked]');
  assert.equal(await evaluate('assessment.payload().results[0].manual_review.reviewed'),true);
  await change('#jump-number',String(total));await click('#jump-query');await change('#jump-number','1');await click('#jump-query');
  assert.equal(await evaluate(`document.querySelector('[data-review-note]').value`),note);
  await call('Page.reload');await waitFor(`document.readyState==='complete'&&typeof assessment!=='undefined'&&!!assessment`);
  assert.equal(await evaluate('assessment.payload().results[0].manual_review.reviewed'),true);
  assert.equal(await evaluate('assessment.payload().results[0].manual_review.note'),note);
  await change('#threshold-min_rows','2001','input');await click('#run-assessment');
  assert.equal(await evaluate('assessment.payload().results[0].manual_review.needs_reconfirmation'),true);
  await change('#jump-number','1');await click('#jump-query');await click('[data-review-checked]');
  await click('#save-reviews');
  for(let i=0;i<50&&!fs.existsSync(path.join(destination,'pcc-manual-reviews.json'));i++)await pause(100);
  assert.ok(fs.existsSync(path.join(destination,'pcc-manual-reviews.json')));
  const dom=await call('DOM.getDocument'),input=await call('DOM.querySelector',{nodeId:dom.root.nodeId,selector:'#import-reviews'});
  await call('DOM.setFileInputFiles',{nodeId:input.nodeId,files:[path.join(destination,'pcc-manual-reviews.json')]});
  await waitFor(`document.querySelector('#review-state').textContent.includes('已导入')`);
  await change('#review-filter','reviewed');assert.equal(await evaluate('filtered.length'),1);
  await click('button[data-filter=""]');assert.equal(await evaluate('filtered.length'),total);
  await change('#search','nothing-can-match-this-string','input');assert.equal(await evaluate('filtered.length'),0);
  await change('#search','','input');await change('#page-size','20');
  assert.equal(await evaluate('document.querySelectorAll("#sql-cards article").length'),1);
  await click('#export-assessment-json');await click('#export-assessment-csv');
  for(let i=0;i<50&&!fs.existsSync(path.join(destination,'pcc-assessments.csv'));i++)await pause(100);
  const exported=JSON.parse(fs.readFileSync(path.join(destination,'pcc-assessments.json'),'utf8'));
  assert.equal(exported.results.length,total);assert.equal(exported.results[0].manual_review.note,note);
  assert.equal(await evaluate('JSON.stringify(records.map(r=>r.status))'),facts);assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(destination,'browser-validation.json'),JSON.stringify({report:path.resolve(report),total,layouts,errors,reviewReload:true,reviewFileRoundtrip:true,exports:true,profile},null,2));
  console.log(JSON.stringify({total,layouts,reviewReload:true,reviewFileRoundtrip:true,exports:true,errors}));
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{socket?.close();processChrome.kill('SIGTERM');});
