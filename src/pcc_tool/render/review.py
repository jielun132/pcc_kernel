"""Human review kept separately from the immutable comparison and rule evidence."""

REVIEW_STYLES = '''
.manual-review{border-top:1px solid #dce2eb;margin-top:14px;padding-top:12px;color:#43546b}
.manual-review-header{display:flex;align-items:center;gap:14px;flex-wrap:wrap}.manual-review-header h4{margin:0;font-size:13px}
.manual-review label{font-size:12px}.manual-review select,.review-tools select,.version-profile select{max-width:100%;border:1px solid #dce2eb;background:#fff;border-radius:6px;padding:7px;color:#43546b}
.manual-review textarea{display:block;box-sizing:border-box;width:100%;min-height:70px;margin-top:6px;resize:vertical;border:1px solid #dce2eb;border-radius:6px;padding:9px;font:12px/1.6 system-ui;color:#43546b}
.manual-review-status{font-size:11px;color:#69778a}.manual-review-status[data-stale=true]{color:#a06423}
.review-tools,.version-profile{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:14px;font-size:12px}.review-tools p,.version-profile p{width:100%;margin:0;color:#69778a;overflow-wrap:anywhere}
.review-tools input[type=file]{max-width:100%;font-size:12px}.manual-review input{accent-color:#2868a9}
'''

REVIEW_SCRIPT = r'''
const PCCReview=(()=>{
  const labels={better:'变优',worse:'变差',equivalent:'基本等价',review:'待定'};
  function validate(data, fingerprint, ids){
    if(!data||data.kind!=='PCCManualReview'||data.schema_version!==1||data.report_fingerprint!==fingerprint||!Array.isArray(data.reviews))throw Error('评审文件与当前报告不匹配，或格式不正确。');
    const result=new Map();
    data.reviews.forEach(r=>{
      if(!r||!ids.has(r.query_id)||result.has(r.query_id)||typeof r.checked!=='boolean'||!Object.hasOwn(labels,r.conclusion)||typeof r.note!=='string'||r.note.length>10000||typeof r.basis!=='string'||r.basis.length>100000)throw Error('评审记录含未知 SQL、重复项或无效内容；未导入任何记录。');
      result.set(r.query_id,{query_id:r.query_id,checked:r.checked,conclusion:r.conclusion,note:r.note,basis:r.basis});
    });return result;
  }
  function attach(records,config,basis,isDirty,refresh){
    const ids=new Set(records.map(r=>r.queryId)),key='pcc-review-v1:'+config.report_fingerprint;
    const status=document.getElementById('review-state'),filter=document.getElementById('review-filter');
    let reviews=new Map(),storageMessage='',notice='';
    function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
    function state(r){const value=reviews.get(r.queryId),stale=!!value?.checked&&(isDirty()||value.basis!==basis(r));
      return {reviewed:!!value?.checked&&!stale,needs_reconfirmation:stale,conclusion:value?.conclusion||'review',note:value?.note||''};}
    function payload(){return {kind:'PCCManualReview',schema_version:1,report_fingerprint:config.report_fingerprint,reviews:[...reviews.values()]};}
    function message(){if(!status)return;const values=records.map(state);status.textContent=`人工已评审 ${values.filter(v=>v.reviewed).length} / ${records.length} 条；需重新确认 ${values.filter(v=>v.needs_reconfirmation).length} 条。`+(notice?' '+notice:'')+(storageMessage?' '+storageMessage:' 已在当前浏览器保存；交付或换电脑前请保存评审 JSON。');}
    function persist(){try{localStorage.setItem(key,JSON.stringify(payload()));storageMessage='';}catch{storageMessage='浏览器保存不可用，请保存评审 JSON；关闭页面前务必导出。';}message();}
    try{const saved=localStorage.getItem(key);if(saved)reviews=validate(JSON.parse(saved),config.report_fingerprint,ids);}catch{storageMessage='浏览器保存不可用或已有记录无效，请使用评审 JSON 恢复。';}
    filter?.addEventListener('change',()=>refresh(true));
    document.getElementById('save-reviews')?.addEventListener('click',()=>{
      const blob=new Blob([JSON.stringify(payload(),null,2)],{type:'application/json;charset=utf-8'}),url=URL.createObjectURL(blob),a=el('a');
      a.href=url;a.download='pcc-manual-reviews.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
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
    function detail(record,host){
      const current=state(record),stored=reviews.get(record.queryId),section=el('section',undefined,'manual-review'),heading=el('div',undefined,'manual-review-header');
      section.setAttribute('aria-label','人工评审');heading.append(el('h4','人工评审'));
      const checked=el('input');checked.type='checkbox';checked.checked=current.reviewed;checked.disabled=isDirty();checked.dataset.reviewChecked=record.queryId;
      const checkLabel=el('label');checkLabel.append(checked,el('span',' 已评审'));heading.append(checkLabel);
      const select=el('select');select.dataset.reviewConclusion=record.queryId;select.setAttribute('aria-label','人工结论');
      Object.entries(labels).forEach(([v,label])=>{const o=el('option',label);o.value=v;select.append(o);});select.value=current.conclusion;
      const selectLabel=el('label','人工结论 ');selectLabel.append(select);heading.append(selectLabel);section.append(heading);
      const marker=el('p',undefined,'manual-review-status');section.append(marker);
      const note=el('textarea');note.value=current.note;note.maxLength=10000;note.dataset.reviewNote=record.queryId;note.setAttribute('aria-label','评审备注');
      const noteLabel=el('label','备注');noteLabel.append(note);section.append(noteLabel);host.append(section);
      let value=stored?{...stored}:{query_id:record.queryId,checked:false,conclusion:'review',note:'',basis:basis(record)};
      function mark(){const s=state(record);marker.dataset.stale=String(s.needs_reconfirmation);marker.textContent=isDirty()?'规则修改尚未应用，请重判后确认评审。':s.needs_reconfirmation?'计划证据、规则、阈值或结论已变化，请重新勾选确认。':s.reviewed?'已确认当前规则结果；人工结论与规则结论分别保留。':'尚未完成人工评审。';}
      function save(){value.conclusion=select.value;value.note=note.value;reviews.set(record.queryId,{...value});notice='';persist();mark();}
      checked.addEventListener('change',()=>{if(isDirty())return;value.checked=checked.checked;value.basis=basis(record);save();});
      select.addEventListener('change',save);note.addEventListener('input',save);mark();
    }
    message();return {detail,state,payload,message,clearFilter:()=>{if(filter)filter.value='';},matches:r=>!filter?.value||(filter.value==='reviewed'?state(r).reviewed:filter.value==='stale'?state(r).needs_reconfirmation:!state(r).reviewed)};
  }
  return {attach,validate};
})();
'''
