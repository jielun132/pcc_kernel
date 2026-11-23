"""Human review kept separately from the immutable comparison and rule evidence."""

REVIEW_OVERVIEW = '''
<section id="review-overview" class="assessment-panel review-overview" aria-labelledby="review-title">
<div class="review-progress-heading"><h2 id="review-title">人工评审汇总</h2><strong id="review-progress" role="status"></strong></div>
<progress id="review-progress-bar" aria-label="本轮人工评审完成进度" value="0" max="1"></progress>
<p id="review-scope"></p>
<div id="review-counts" class="review-counts" role="group" aria-label="按人工评审状态查看明细"></div>
<details id="review-ledger"><summary>逐条评审明细</summary>
<div id="review-ledger-list" class="review-ledger-list"></div></details>
<div class="review-tools"><button type="button" id="export-review-summary">导出评审汇总 CSV</button><button type="button" id="save-reviews">保存评审 JSON</button>
<label>恢复评审 <input type="file" id="import-reviews" accept=".json,application/json"></label>
<p id="review-state" role="status"></p></div></section>'''

REVIEW_STYLES = '''
.review-overview h2{margin:0;font-size:19px}.review-progress-heading{display:flex;align-items:baseline;justify-content:space-between;gap:8px 18px;flex-wrap:wrap}
.review-progress-heading strong{color:#245e86;font-size:15px}.review-overview progress{display:block;width:100%;height:8px;accent-color:#2868a9;margin:12px 0}
#review-scope,.review-ledger-list p{font-size:12px;color:#596579}.review-counts{display:flex;flex-wrap:wrap;gap:6px}.review-counts button[aria-pressed=true]{border-color:#3979af;background:#edf5fc;color:#204f77}
#review-ledger{margin-top:14px;font-size:13px}#review-ledger summary{cursor:pointer;font-weight:600}.review-ledger-list{max-height:23rem;overflow:auto;margin-top:8px}
.review-ledger-list table{width:100%;border-collapse:collapse;font-size:12px;table-layout:fixed}.review-ledger-list th,.review-ledger-list td{text-align:left;padding:7px;border-bottom:1px solid #e4e9ef;vertical-align:top;overflow-wrap:anywhere;white-space:pre-wrap}
.review-ledger-list th{background:#f5f8fb;position:sticky;top:0}.review-ledger-list td a,.manual-review a{color:#215f94}.review-ledger-list td small{display:block;color:#596579}
.review-ledger-list th:first-child{width:36%}.review-ledger-list th:nth-child(2),.review-ledger-list th:nth-child(3){width:17%}
.query-assessment .review-verdict{font-size:16px}.query-assessment .rule-verdict{color:#536478;font-weight:600}.decision-summary{margin:8px 0;color:#35485c}
.decision-summary p{overflow-wrap:anywhere}.decision-summary b{margin-right:.6em}.manual-review-status a{margin-left:10px}
.manual-review{border-top:1px solid #dce2eb;margin-top:14px;padding-top:12px;color:#43546b}
.manual-review-header{display:flex;align-items:center;gap:14px;flex-wrap:wrap}.manual-review-header h4{margin:0;font-size:13px}
.manual-review label{font-size:12px}.manual-review select,.review-tools select,.version-profile select{max-width:100%;border:1px solid #dce2eb;background:#fff;border-radius:6px;padding:7px;color:#43546b}
.manual-review textarea{display:block;box-sizing:border-box;width:100%;min-height:70px;margin-top:6px;resize:vertical;border:1px solid #dce2eb;border-radius:6px;padding:9px;font:12px/1.6 system-ui;color:#43546b}
.manual-review-status{font-size:11px;color:#69778a}.manual-review-status[data-stale=true]{color:#a06423}
.review-tools,.version-profile{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:14px;font-size:12px}.review-tools p,.version-profile p{width:100%;margin:0;color:#69778a;overflow-wrap:anywhere}
.review-tools input[type=file]{max-width:100%;font-size:12px}.manual-review input{accent-color:#2868a9}
.batch-review{margin:12px 0 0}.batch-review button{cursor:pointer;padding:7px 11px;border:1px solid #b8cede;border-radius:6px;background:white;color:#215a86}.batch-review button:disabled{cursor:default;color:#6b7280}.batch-review details,.batch-review p{font-size:12px;overflow-wrap:anywhere;margin:6px 0}.change-summary{margin:14px 0;padding:12px 16px;border-left:3px solid #4778a5;background:#f3f7fb}.change-summary h3,.change-summary p{margin:3px 0;font-size:13px;line-height:1.65;overflow-wrap:anywhere}.text-equivalence{margin:12px 0;font-size:13px}
'''

REVIEW_SCRIPT = r'''
function downloadReportFile(name,type,content){
  const url=URL.createObjectURL(new Blob([content],{type})),a=document.createElement('a');
  a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function reportCsv(rows){
  function cell(value){let s=String(value??'');if(/^[\s]*[=+@-]|^[\t\r\n]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';}
  return '\uFEFF'+rows.map(row=>row.map(cell).join(',')).join('\r\n');
}
const PCCReview=(()=>{
  const labels={accepted:'通过',better:'变优',worse:'变差',equivalent:'基本等价',review:'待定'};
  function validate(data, fingerprint, ids){
    if(!data||data.kind!=='PCCManualReview'||data.schema_version!==1||data.report_fingerprint!==fingerprint||!Array.isArray(data.reviews))throw Error('评审文件与当前报告不匹配，或格式不正确。');
    const result=new Map();
    data.reviews.forEach(r=>{
      if(!r||!ids.has(r.query_id)||result.has(r.query_id)||typeof r.checked!=='boolean'||!Object.hasOwn(labels,r.conclusion)||typeof r.note!=='string'||r.note.length>10000||typeof r.basis!=='string'||r.basis.length>100000)throw Error('评审记录含未知 SQL、重复项或无效内容；未导入任何记录。');
      if(r.source_query_id!==undefined&&(!ids.has(r.source_query_id)||r.source_query_id===r.query_id))throw Error('批量评审来源 SQL 无效。');
      result.set(r.query_id,{query_id:r.query_id,checked:r.checked,conclusion:r.conclusion,note:r.note,basis:r.basis,
                             ...(r.source_query_id?{source_query_id:r.source_query_id}:{})});
    });return result;
  }
  function attach(records,config,basis,isDirty,refresh,groupId,currentScope,needsReview,ruleLabel,explain){
    const ids=new Set(records.map(r=>r.queryId)),key='pcc-review-v1:'+config.report_fingerprint;
    const byQuery=new Map(records.map(r=>[r.queryId,r]));
    const status=document.getElementById('review-state'),filter=document.getElementById('review-filter');
    const ledger=document.getElementById('review-ledger'),counts=document.getElementById('review-counts');
    const views={all:'本轮全部',pending:'未完成',stale:'需重新确认',...labels,outside:'范围外记录'};
    let view='all';
    let reviews=new Map(),storageMessage='',notice='';
    function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
    function state(r){const value=reviews.get(r.queryId),stale=!!value?.checked&&(isDirty()||value.basis!==basis(r));
      return {reviewed:!!value?.checked&&!stale,needs_reconfirmation:stale,conclusion:value?.conclusion||'review',note:value?.note||'',source_query_id:value?.source_query_id};}
    function payload(){return {kind:'PCCManualReview',schema_version:1,report_fingerprint:config.report_fingerprint,reviews:[...reviews.values()]};}
    function hasEntry(r){const v=reviews.get(r.queryId);return v&&(v.checked||v.note||v.conclusion!=='review');}
    function summary(){
      const result={total:0,reviewed:0,pending:0,stale:0,outside:0,conclusions:Object.fromEntries(Object.keys(labels).map(k=>[k,0]))};
      for(const r of records){
        if(!needsReview(r)){if(hasEntry(r))result.outside++;continue;}
        const s=state(r);result.total++;
        if(s.reviewed){result.reviewed++;result.conclusions[s.conclusion]++;}
        else{result.pending++;if(s.needs_reconfirmation)result.stale++;}
      }
      return result;
    }
    function caption(s){return s.reviewed?'人工已评审 · '+labels[s.conclusion]:s.needs_reconfirmation?'需重新确认 · '+labels[s.conclusion]:'未评审';}
    function inView(r,s){
      if(view==='outside')return !needsReview(r)&&hasEntry(r);
      if(!needsReview(r))return false;
      if(view==='all')return true;
      if(view==='pending')return !s.reviewed;
      if(view==='stale')return s.needs_reconfirmation;
      return s.reviewed&&s.conclusion===view;
    }
    function showLedger(){
      if(!ledger.open)return;
      const host=document.getElementById('review-ledger-list'),table=el('table'),header=el('tr');
      for(const name of ['SQL','规则预判','人工评审','备注与来源'])header.append(el('th',name));
      const head=el('thead'),body=el('tbody');head.append(header);table.append(head,body);
      let n=0;
      for(const r of records){
        const s=state(r);
        if(inView(r,s)){
          const row=el('tr'),query=el('td'),link=el('a','#'+String(r.number).padStart(3,'0'));
          link.href='#'+r.id;query.append(link,el('small',r.preview));row.dataset.reviewRow=r.queryId;
          const notes=el('td',s.note);
          if(s.source_query_id){const source=byQuery.get(s.source_query_id),a=el('a','同组来源 #'+source.number);a.href='#'+source.id;notes.append(el('br'),a);}
          row.append(query,el('td',ruleLabel(r)),el('td',caption(s)),notes);body.append(row);n++;
        }
      }
      host.replaceChildren(el('p',views[view]+' · '+n+' 条 SQL'),...(n?[table]:[]));
    }
    function message(){
      const s=summary(),progress=document.getElementById('review-progress'),bar=document.getElementById('review-progress-bar');
      const progressText=s.total===0?'本轮没有需评审的 SQL':s.pending===0?'本轮全部已评审':'已评审';
      progress.textContent=s.total?`${progressText} ${s.reviewed} / ${s.total} · 剩余 ${s.pending} 条`:progressText;
      bar.max=s.total||1;bar.value=s.reviewed;
      document.getElementById('review-scope').textContent='本轮统计整份报告中存在差异或命中复核条件的 SQL。'+
        (s.conclusions.review?` 已确认的 SQL 中有 ${s.conclusions.review} 条人工结论待定。`:'')+(isDirty()?' 规则修改尚未应用。':'');
      const values={all:s.total,pending:s.pending,stale:s.stale,...s.conclusions,outside:s.outside};
      for(const b of counts.children){const k=b.dataset.reviewView;b.textContent=views[k]+' '+values[k];b.setAttribute('aria-pressed',String(view===k));b.hidden=k==='outside'&&!s.outside&&view!==k;}
      showLedger();
      status.textContent=[notice,storageMessage||'评审记录保存在当前浏览器。交付或换电脑前请保存评审 JSON。'].filter(Boolean).join(' ');
      document.getElementById('export-review-summary').disabled=isDirty();
    }
    for(const k of Object.keys(views)){const b=el('button');b.type='button';b.dataset.reviewView=k;
      b.addEventListener('click',()=>{view=k;ledger.open=true;message();});counts.append(b);}
    ledger.addEventListener('toggle',showLedger);
    document.getElementById('export-review-summary').addEventListener('click',()=>{
      if(isDirty())return;
      const rows=[['SQL 编号','SQL 标识','数据库','SQL 摘录','评审范围','规则预判','判断依据','建议核实','人工评审状态','人工结论','备注','同组来源 SQL 编号']];
      for(const r of records){
        if(!needsReview(r)&&!hasEntry(r))continue;
        const s=state(r),why=explain(r),source=byQuery.get(s.source_query_id);
        rows.push([r.number,r.queryId,r.schema,r.preview,needsReview(r)?'本轮':'范围外记录',ruleLabel(r),why.reason,why.check,
          s.reviewed?'已评审':s.needs_reconfirmation?'需重新确认':'未评审',s.reviewed||s.needs_reconfirmation||s.conclusion!=='review'?labels[s.conclusion]:'',s.note,source?.number]);
      }
      downloadReportFile('pcc-review-summary.csv','text/csv;charset=utf-8',reportCsv(rows));
    });
    function persist(){try{localStorage.setItem(key,JSON.stringify(payload()));storageMessage='';}catch{storageMessage='浏览器保存不可用，请保存评审 JSON；关闭页面前务必导出。';}message();}
    try{const saved=localStorage.getItem(key);if(saved)reviews=validate(JSON.parse(saved),config.report_fingerprint,ids);}catch{storageMessage='浏览器保存不可用或已有记录无效，请使用评审 JSON 恢复。';}
    filter?.addEventListener('change',()=>refresh(true));
    document.getElementById('save-reviews')?.addEventListener('click',()=>{
      downloadReportFile('pcc-manual-reviews.json','application/json;charset=utf-8',JSON.stringify(payload(),null,2));
    });
    const input=document.getElementById('import-reviews');
    input?.addEventListener('change',async()=>{
      const file=input.files?.[0];if(!file)return;
      try{if(file.size>Math.max(1000000,records.length*120000))throw Error('评审文件过大。');
        const incoming=validate(JSON.parse(await file.text()),config.report_fingerprint,ids);
        // Validate the entire file before merging. Unmentioned reviews are retained.
        incoming.forEach((v,k)=>reviews.set(k,v));notice=`已导入 ${incoming.size} 条评审。`;persist();refresh();
      }catch(e){notice=e instanceof SyntaxError?'评审 JSON 格式不正确；现有评审未变。':e.message;message();}finally{input.value='';}
    });
    function detail(record,host,onState){
      const current=state(record),stored=reviews.get(record.queryId),section=el('section',undefined,'manual-review'),heading=el('div',undefined,'manual-review-header');
      section.setAttribute('aria-label','人工评审');heading.append(el('h4','人工评审'));
      const checked=el('input');checked.type='checkbox';checked.checked=current.reviewed;checked.disabled=isDirty();checked.dataset.reviewChecked=record.queryId;
      const checkLabel=el('label');checkLabel.append(checked,el('span',' 已评审'));heading.append(checkLabel);
      const select=el('select');select.dataset.reviewConclusion=record.queryId;select.setAttribute('aria-label','人工结论');
      Object.entries(labels).forEach(([v,label])=>{const o=el('option',label);o.value=v;select.append(o);});select.value=current.conclusion;
      const selectLabel=el('label','人工结论 ');selectLabel.append(select);heading.append(selectLabel);section.append(heading);
      const marker=el('p',undefined,'manual-review-status'),back=el('a','查看评审汇总');back.href='#review-overview';section.append(marker);
      const note=el('textarea');note.value=current.note;note.maxLength=10000;note.dataset.reviewNote=record.queryId;note.setAttribute('aria-label','评审备注');
      const noteLabel=el('label','备注');noteLabel.append(note);section.append(noteLabel);host.append(section);
      let value=stored?{...stored}:{query_id:record.queryId,checked:false,conclusion:'review',note:'',basis:basis(record)};
      const batch=el('div',undefined,'batch-review');
      if(groupId(record))section.append(batch);
      function targets(){
        return currentScope().filter(r=>{
          const prior=reviews.get(r.queryId);
          return r!==record&&groupId(r)===groupId(record)&&!prior?.checked&&(!prior||prior.conclusion==='review'||prior.conclusion===value.conclusion);
        });
      }
      function ready(){return !isDirty()&&state(record).reviewed&&value.conclusion!=='review'&&!value.source_query_id;}
      function showBatch(){
        if(!groupId(record))return;
        const rows=targets(),button=el('button',`将本条人工结论应用到同组 ${rows.length} 条 SQL`);
        button.type='button';button.dataset.applyReviewGroup=groupId(record);button.disabled=!ready()||!rows.length;
        const details=el('details');details.append(el('summary',`评审组 ${groupId(record)} · 查看本次适用范围`),
          el('p',rows.length?rows.map(r=>'#'+r.number).join('、'):'当前筛选范围内没有可应用的同组 SQL。'));
        batch.replaceChildren(details,button);
        if(!ready())batch.append(el('p','先为代表 SQL 选择明确的人工结论并勾选“已评审”。'));
        button.addEventListener('click',()=>{
          if(!ready())return;
          const pending=targets();
          for(const target of pending){
            reviews.set(target.queryId,{query_id:target.queryId,checked:true,conclusion:value.conclusion,
              note:reviews.get(target.queryId)?.note||'',basis:basis(target),source_query_id:record.queryId});
          }
          notice=`已将 SQL #${record.number} 的“${labels[value.conclusion]}”应用到同组 ${pending.length} 条 SQL。`;persist();refresh();
        });
      }
      function mark(){const s=state(record);onState(s);marker.dataset.stale=String(s.needs_reconfirmation);marker.textContent=isDirty()?'规则修改尚未应用，请重判后确认评审。':s.needs_reconfirmation?'计划证据、规则、阈值或结论已变化，请重新勾选确认。':s.reviewed?'已计入评审汇总。':'选择人工结论后，勾选“已评审”即可确认。';
        if(s.source_query_id)marker.textContent+=' 同组评审来源 SQL #'+byQuery.get(s.source_query_id).number+'。';
        marker.append(back);
        showBatch();}
      function save(){value.conclusion=select.value;value.note=note.value;reviews.set(record.queryId,{...value});notice='';persist();}
      function saveVerdict(){save();mark();refresh('review-edit');}
      checked.addEventListener('change',()=>{if(isDirty())return;value.checked=checked.checked;value.basis=basis(record);delete value.source_query_id;saveVerdict();});
      select.addEventListener('change',()=>{delete value.source_query_id;saveVerdict();});note.addEventListener('input',save);mark();
    }
    return {detail,state,payload,message,summary,caption,clearFilter:()=>{if(filter)filter.value='';},matches:r=>!filter?.value||(filter.value==='reviewed'?state(r).reviewed:filter.value==='stale'?state(r).needs_reconfirmation:needsReview(r)&&!state(r).reviewed)};
  }
  return {attach,validate};
})();
'''
