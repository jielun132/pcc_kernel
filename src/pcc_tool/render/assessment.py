"""Rule selection and deterministic assessment in a standalone offline report."""
import html
import json

from pcc_tool.plan.rule_config import load_policy
from pcc_tool.render.review import REVIEW_SCRIPT, REVIEW_STYLES


def assessment_markup(fingerprint, policy=None):
    config = {**(policy or load_policy()), "report_fingerprint": fingerprint}
    payload = json.dumps(config, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    groups = []
    for key, title in (("data_review", "默认必审"), ("runtime_note", "运行时提示 · 按需加入复核"),
                       ("directional", "方向判断"), ("equivalent", "基本等价"), ("review", "复杂变化复核")):
        cards = []
        for rule in config["rules"]:
            if rule["group"] != key:
                continue
            esc = html.escape
            cards.append(f'<label class="rule-card"><span class="rule-card-title"><input type="checkbox" data-rule-id="{rule["id"]}"'
                + (' checked' if rule["default"] else '') + (' disabled' if key == "data_review" else '') + f'> <strong>{rule["id"]} · {esc(rule["title"])}</strong></span>'
                f'<span class="rule-support">{"命中即待复核 · 始终生效" if key == "data_review" else "详情保留提示 · 勾选才加入待复核" if key == "runtime_note" else "可自动预判 · 满足条件才生效" if rule["automatic"] else "复核提示 · 不自动判断优劣"}</span>'
                f'<span><b>{"结论" if key != "directional" else "变优"}</b> {esc(rule["better"])}</span>'
                f'<span><b>{"边界" if key != "directional" else "变差"}</b> {esc(rule["worse"])}</span>'
                f'<span class="rule-premise">{esc(rule["requires"])}</span>'
                + ''.join(f'<span><b>{label}</b> {esc(rule[field])}</span>' for field, label in
                          (("description", "自定义说明"), ("review_note", "评审提示")) if rule.get(field))
                + f'<a href="{esc(rule["source"], quote=True)}" target="_blank" rel="noopener noreferrer">TiDB 依据 ↗</a></label>')
        groups.append(f'<details class="rule-group"><summary>{title} · {len(cards)} 张</summary><div class="rule-grid">{"".join(cards)}</div></details>')
    profiles = ''.join(f'<option value="{html.escape(p["id"], quote=True)}"'
                       + (' selected' if p["id"] == config["profile_id"] else '')
                       + f'>{html.escape(p["label"])}</option>' for p in config["profiles"])
    markup = '''<section class="assessment-panel" id="assessment-panel" aria-label="规则预判">
<div class="assessment-heading"><div><p class="eyebrow">RULE ASSESSMENT</p><h2>按规则预判计划变化</h2>
<p>先判断普通 EXPLAIN 是否代表有数据时的计划，再判断计划差异。命中率、循环和内存等运行时信息列在每条 SQL 的详情中。</p></div>
<button type="button" id="run-assessment" class="assessment-primary">按所选规则重新判断</button></div>
<div id="assessment-counts" class="assessment-counts" role="group" aria-label="按预判结果筛选"></div>
<p id="assessment-state" class="assessment-state" role="status" aria-live="polite"></p>
<div class="version-profile"><label>版本规则方案 <select id="rule-profile">''' + profiles + '''</select></label><p id="profile-state"></p></div>
<details class="rule-settings"><summary>选择规则与阈值 · ''' + str(len(config["rules"])) + ''' 张规则卡</summary>
<p class="rule-explanation">勾选后点击上方按钮生效。默认复核可能提前读表、仅靠模型评分、缺定义或差异证据不足的 SQL。可另外勾选要实测的运行时项目。
方向规则只在前提可验证时自动分类；复核提示卡提供检查依据。未覆盖、证据不足或方向冲突都保留“待复核”。</p>
<div class="rule-presets"><button type="button" data-rule-preset="default">恢复推荐</button><button type="button" data-rule-preset="runtime">推荐规则＋复核运行代价</button><button type="button" data-rule-preset="all">全部勾选</button><button type="button" data-rule-preset="none">取消可选规则</button></div>
''' + "".join(groups) + '''<div class="rule-thresholds">
<label>有效下推最小输入行数 <input type="number" id="threshold-min_rows" value="1000" min="1" max="1000000000" step="1"></label>
<label>TopN / Limit 最大 K 占比 (%) <input type="number" id="threshold-max_ratio_pct" value="10" min="1" max="50" step="1"></label>
<label>过滤至少减少 (%) <input type="number" id="threshold-reduction_pct" value="50" min="1" max="99" step="1"></label>
<label>仅估算变化容差 (%) <input type="number" id="threshold-estimate_pct" value="10" min="1" max="50" step="1"></label></div>
<p class="rule-explanation">A12 另外要求至少 10000 行；E05 默认不勾选。E11 单独识别已核验的版本估算下限。组合证明需同时勾选其全部规则。阈值是本项目策略，不是 TiDB 官方性能保证。</p>
</details><div class="assessment-exports"><span>当前结果可独立保存，含规则、阈值、证据和报告指纹。</span>
<button type="button" id="export-assessment-json">导出判断 JSON</button><button type="button" id="export-assessment-csv">导出判断 CSV</button></div>
<div class="review-tools"><label>人工评审筛选 <select id="review-filter"><option value="">全部</option><option value="pending">未完成评审</option><option value="reviewed">已评审</option><option value="stale">需重新确认</option></select></label>
<button type="button" id="save-reviews">保存评审 JSON</button><label>恢复评审 <input type="file" id="import-reviews" accept=".json,application/json"></label>
<p id="review-state" role="status" aria-live="polite"></p></div>
</section><script type="application/json" id="assessment-config">''' + payload + '</script>'
    for key, default in (("min_rows", "1000"), ("max_ratio_pct", "10"), ("reduction_pct", "50"), ("estimate_pct", "10")):
        markup = markup.replace(f'id="threshold-{key}" value="{default}"', f'id="threshold-{key}" value="{config["thresholds"][key]}"')
    return markup


ASSESSMENT_STYLES = REVIEW_STYLES + '''
.assessment-panel{margin:20px 0 26px;padding:24px;background:#fff;border:1px solid #e4e4e9;border-radius:14px}.assessment-heading{display:flex;align-items:center;justify-content:space-between;gap:20px}.assessment-heading h2{font-size:19px;margin:4px 0 8px}.assessment-heading p:not(.eyebrow){font-size:12px;color:#747b86;margin:0;line-height:1.7}.assessment-panel button{cursor:pointer;border:1px solid #dfe3eb;border-radius:7px;padding:7px 11px;background:#fff;color:#596579;font-size:12px}.assessment-panel button:disabled{opacity:.45;cursor:not-allowed}.assessment-panel .assessment-primary{background:#2868a9;color:white;border-color:#2868a9;padding:11px 16px;white-space:nowrap}.assessment-counts{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}.assessment-counts button{flex:1;text-align:left;min-width:100px;padding:12px 15px;border-color:#e7e9ee}.assessment-counts b{display:block;font-size:22px;font-weight:600;margin-bottom:3px}.assessment-counts [aria-pressed=true]{box-shadow:0 0 0 2px #78a6d9}.assessment-better{color:#31795a!important;background:#f4faf6!important}.assessment-worse{color:#a94e49!important;background:#fff6f5!important}.assessment-equivalent{color:#426fa4!important;background:#f4f8fd!important}.assessment-review{color:#967134!important;background:#fffbf2!important}.assessment-state{font-size:12px;color:#69778a;margin:12px 0}.assessment-state[data-dirty=true]{color:#a06423}.rule-settings{border-top:1px solid #eceef2;padding-top:14px;margin-top:16px}.rule-settings>summary{font-size:13px;color:#536981}.rule-explanation{font-size:12px;color:#7b818d;line-height:1.8;margin:12px 0}.rule-presets{display:flex;gap:8px;margin:12px 0}.rule-group{border-top:1px solid #eee;padding:12px 0}.rule-group>summary{font-size:13px;font-weight:600;color:#596579}.rule-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:12px}.rule-card{display:flex;flex-direction:column;gap:8px;border:1px solid #e5e8ef;border-radius:9px;padding:15px;font-size:11px;color:#6d7480;line-height:1.6}.rule-card:has(input:checked){background:#f8fbff;border-color:#cbdcef}.rule-card-title{color:#3e536d;font-size:12px}.rule-card input{accent-color:#2868a9}.rule-support{font-size:10px;color:#7a8ca1}.rule-card b{font-weight:500;color:#52647b}.rule-premise{border-top:1px dashed #dfe5ec;padding-top:8px}.rule-card a{font-size:10px;color:#6784a4;align-self:flex-start}.rule-thresholds{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;padding-top:15px}.rule-thresholds label{font-size:11px;color:#66768a}.rule-thresholds input{display:block;width:100%;box-sizing:border-box;margin-top:6px;border:1px solid #dde3eb;border-radius:6px;padding:8px;background:#fff;color:#405778}.assessment-exports{display:flex;gap:8px;align-items:center;margin-top:16px;padding-top:14px;border-top:1px solid #eceef2}.assessment-exports span{font-size:11px;color:#89909c;margin-right:auto}.query-assessment{border:1px solid #e5eaf1;border-radius:9px;padding:15px;margin:16px 0;font-size:12px;color:#65768a;line-height:1.8}.query-assessment h3{font-size:14px;margin:0 0 5px}.query-assessment p{margin:5px 0}.query-assessment ul{margin:6px 0;padding-left:20px}.query-assessment a{color:#5680ad;font-size:11px;margin-right:10px}.query-assessment .assessment-evidence{margin-top:10px}.query-assessment .assessment-hint{font-size:11px;color:#7c8693}.query-assessment .assessment-stale{color:#ac722e}.nav-assessment{display:block;font-size:10px;margin-top:5px;background:transparent!important}.assessment-counts[data-dirty=true]{opacity:.6}
@media(max-width:1100px){.rule-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.rule-thresholds{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:760px){.assessment-panel{padding:17px}.assessment-heading{align-items:flex-start;flex-direction:column}.rule-grid{grid-template-columns:1fr}.assessment-exports{flex-wrap:wrap}.assessment-exports span{width:100%}.assessment-counts{gap:6px}.assessment-counts button{min-width:80px;padding:10px}.rule-thresholds{grid-template-columns:1fr 1fr}}
.rule-presets{flex-wrap:wrap}.rule-card,.rule-thresholds label,.assessment-heading>div{min-width:0;overflow-wrap:anywhere}.assessment-model-table{width:100%;table-layout:fixed;font-size:11px;border-collapse:collapse}.assessment-model-table th,.assessment-model-table td{overflow-wrap:anywhere;text-align:left;padding:6px;border-bottom:1px solid #e5e8ef}
'''


ASSESSMENT_SCRIPT = REVIEW_SCRIPT + r'''
const PCCAssessment=(()=>{
  const labels={better:'变优',worse:'变差',equivalent:'基本等价',review:'待复核',excluded:'不参与预判'};
  // Decimal fractions use BigInt. EXPLAIN estRows may exceed Number's exact range.
  function decimal(value){
    if(value===null||value===undefined)return null;
    const m=String(value).match(/^(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/);if(!m)return null;
    const exponent=Number(m[3]||0)-(m[2]||'').length;if(Math.abs(exponent)>1000)return null;
    let n=BigInt(m[1]+(m[2]||'')),d=1n;if(exponent>=0)n*=10n**BigInt(exponent);else d=10n**BigInt(-exponent);return [n,d];
  }
  function guardPass(g,t){
    const setting=decimal(g.constant??t[g.setting]);if(!setting)return false;
    const [sn,sd]=setting;
    if(g.kind==='minimum'){const v=decimal(g.value);return !!v&&v[0]*sd>=sn*v[1];}
    const a=decimal(g.input),b=decimal(g.output);if(!a||!b||a[0]<=0n)return false;
    const x=a[0]*b[1],y=b[0]*a[1];
    if(g.kind==='ratio')return y*100n*sd<=x*sn;
    if(g.kind==='reduction')return y<x&&(x-y)*100n*sd>=x*sn;
    if(g.kind==='estimate')return y>0n&&(x>y?x:y)*100n*sd<=(x<y?x:y)*(100n*sd+sn);
    return false;
  }
  function reviewCards(e,selected){
    return [...(e.mandatory_reviews||[]),...(e.runtime_notes||[]).filter(r=>selected.includes(r.rule_id))];
  }
  function evaluate(e,selected,thresholds){
    const mandatory=reviewCards(e,selected).map(r=>r.rule_id+' · '+r.reason);
    if(e.scope!=='changed'&&mandatory.length)return {status:'review',matched:[],uncovered:[],reasons:[...mandatory,...e.blockers]};
    if(e.scope!=='changed')return {status:'excluded',matched:[],uncovered:[],reasons:['只对已确认存在差异的 SQL 预判']};
    const enabled=new Set(selected),matched=[],rejected=[];
    e.matches.forEach(m=>{if(!enabled.has(m.rule_id)||(m.requires_rules||[]).some(id=>!enabled.has(id)))return;const failures=(m.guards||[]).filter(g=>!guardPass(g,thresholds)).map(g=>g.label);
      if(failures.length)rejected.push([m,m.rule_id+'：'+failures.join('、')+'未满足阈值']);else matched.push(m);});
    const covered=new Set(matched.flatMap(m=>m.event_refs.map(String))),uncovered=e.event_refs.filter(ref=>!covered.has(String(ref)));
    const directions=new Set(matched.map(m=>m.direction).filter(x=>x!=='equivalent')),reasons=[...mandatory,...e.blockers,...rejected.filter(([m])=>m.event_refs.some(ref=>!covered.has(String(ref)))).map(([,reason])=>reason)];
    if(uncovered.length)reasons.push('存在未被所选规则完整覆盖的差异');
    if(directions.size>1)reasons.push('同时命中变优和变差，不抵消');
    return {status:reasons.length||!matched.length?'review':directions.size?[...directions][0]:'equivalent',matched:matched.map(m=>m.id),uncovered,reasons};
  }
  function csvCell(value){let s=String(value??'');if(/^[\s]*[=+@-]|^[\t\r\n]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';}
  function csv(payload){
    const header=['query_id','sql_number','schema','compare_status','assessment','matched_rules','reasons','uncovered_event_refs','selected_rules','thresholds','assessment_version','rules_fingerprint','report_fingerprint','mandatory_rules','review_steps','runtime_notes','rule_profile','reviewed','needs_reconfirmation','human_conclusion','review_note'];
    const rows=payload.results.map(r=>[r.query_id,r.number,r.schema,r.compare_status,r.result.status,
      r.evidence.matches.filter(m=>r.result.matched.includes(m.id)).map(m=>m.rule_id).join(','),r.result.reasons.join('；'),r.result.uncovered.join(','),
      payload.selected.join(','),JSON.stringify(payload.thresholds),payload.version,payload.rules_fingerprint,payload.report_fingerprint,
      (r.evidence.mandatory_reviews||[]).map(m=>m.rule_id).join(','),reviewCards(r.evidence,payload.selected).map(m=>m.check).join(' / '),
      (r.evidence.runtime_notes||[]).map(m=>m.rule_id+' · '+m.reason+' '+m.check).join(' / '),payload.profile_id,
      r.manual_review?.reviewed,r.manual_review?.needs_reconfirmation,r.manual_review?.conclusion,r.manual_review?.note]);
    return '\uFEFF'+[header,...rows].map(row=>row.map(csvCell).join(',')).join('\r\n');
  }
  function attach(records,refresh){
    const node=document.getElementById('assessment-config');if(!node)return null;
    const config=JSON.parse(node.textContent),rules=new Map(config.rules.map(r=>[r.id,r]));
    const checks=Array.from(document.querySelectorAll('[data-rule-id]'));
    const settings=Object.fromEntries(Object.keys(config.thresholds).map(k=>[k,document.getElementById('threshold-'+k)]));
    const state=document.getElementById('assessment-state'),counts=document.getElementById('assessment-counts');
    const exports=['json','csv'].map(k=>document.getElementById('export-assessment-'+k));
    const profileNode=document.getElementById('rule-profile'),profileState=document.getElementById('profile-state');
    const profiles=new Map((config.profiles||[]).map(p=>[p.id,p]));
    let profileId=config.profile_id,selected=[...config.selected],thresholds={...config.thresholds},filter=null,dirty=false,review=null;
    let results=new Map(records.map(r=>[r.id,r.assessment||evaluate(r.assessmentEvidence,selected,thresholds)]));
    const scope=()=>[...results.values()].filter(r=>r.status!=='excluded').length;
    const mandatory=records.filter(r=>r.assessmentEvidence.mandatory_reviews?.length);
    function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
    function current(){return {profile_id:profileNode?.value||profileId,selected:checks.filter(c=>c.checked).map(c=>c.dataset.ruleId),thresholds:Object.fromEntries(Object.entries(settings).map(([k,n])=>[k,n.value]))};}
    function valid(t){return Object.entries(t).every(([k,v])=>/^\d+$/.test(v)&&BigInt(v)>=1n&&BigInt(v)<=BigInt(k==='min_rows'?1000000000:k==='reduction_pct'?99:50));}
    function renderCounts(){
      counts.replaceChildren();const total=el('button');total.type='button';total.dataset.assessmentFilter='';total.setAttribute('aria-pressed',String(filter===''));total.append(el('b',String(scope())),el('span','全部预判'));counts.append(total);
      ['better','worse','equivalent','review'].forEach(key=>{const button=el('button',undefined,'assessment-'+key);button.type='button';button.dataset.assessmentFilter=key;button.setAttribute('aria-pressed',String(filter===key));
        button.append(el('b',String([...results.values()].filter(r=>r.status===key).length)),el('span',labels[key]));counts.append(button);});
      counts.dataset.dirty=String(dirty);
    }
    function message(){
      const countRule=id=>mandatory.filter(r=>r.assessmentEvidence.mandatory_reviews.some(m=>id.includes(m.rule_id))).length;
      state.dataset.dirty=String(dirty);state.textContent=dirty?'规则或阈值已修改。当前展示上次判断结果，请点击“按所选规则重新判断”。':`本次预判 ${scope()} 条。固定必审卡命中 ${mandatory.length} 条，其中可能提前读表 ${countRule(['D01','D02'])} 条，仅有代价估算 ${countRule(['D08'])} 条，缺 SQL 或视图定义 ${countRule(['D10'])} 条。卡片可重叠，总数按 SQL 去重。`;
      if(profileState){const p=profiles.get(profileNode?.value||profileId),actual=config.actual_versions||{},matched=p?.old_version===actual.old&&p?.new_version===actual.new;
        profileState.textContent=`实际采集 ${actual.old||'unknown'} → ${actual.new||'unknown'}。`+(matched?'规则按此版本组合配置；仍逐项检查证据前提。':'当前继承通用或其他版本规则；版本专属证明条件仍生效，不代表该组合已通过验证。');}
      exports.forEach(b=>b.disabled=dirty);renderCounts();review?.message();
    }
    function change(){const c=current();dirty=c.profile_id!==profileId||JSON.stringify([...c.selected].sort())!==JSON.stringify([...selected].sort())||JSON.stringify(c.thresholds)!==JSON.stringify(thresholds);message();refresh();}
    profileNode?.addEventListener('change',()=>{const p=profiles.get(profileNode.value);if(!p)return;
      checks.forEach(c=>c.checked=c.disabled||p.selected.includes(c.dataset.ruleId));Object.entries(p.thresholds).forEach(([k,v])=>settings[k].value=v);change();});
    checks.forEach(c=>c.addEventListener('change',change));Object.values(settings).forEach(n=>n.addEventListener('input',change));
    document.querySelectorAll('[data-rule-preset]').forEach(button=>button.addEventListener('click',()=>{
      const preset=button.dataset.rulePreset;
      const defaults=profiles.get(profileNode?.value||profileId)||config;
      checks.forEach(c=>c.checked=c.disabled||preset==='all'||(['default','runtime'].includes(preset)&&defaults.selected.includes(c.dataset.ruleId))||
        (preset==='runtime'&&rules.get(c.dataset.ruleId).group==='runtime_note'));
      if(['default','runtime'].includes(preset))Object.entries(defaults.thresholds).forEach(([k,v])=>settings[k].value=v);change();
    }));
    document.getElementById('run-assessment').addEventListener('click',()=>{
      const c=current();if(!valid(c.thresholds)){state.textContent='阈值需为界面标注范围内的整数；当前结果未变。';return;}
      profileId=c.profile_id;selected=c.selected;thresholds=c.thresholds;results=new Map(records.map(r=>[r.id,evaluate(r.assessmentEvidence,selected,thresholds)]));dirty=false;message();refresh();
    });
    counts.addEventListener('click',event=>{const b=event.target.closest('[data-assessment-filter]');if(!b)return;filter=b.dataset.assessmentFilter;renderCounts();refresh(true);});
    function payload(){return {version:config.version,rules_fingerprint:config.rules_fingerprint,report_fingerprint:config.report_fingerprint,
      profile_id:profileId,actual_versions:config.actual_versions,manual_reviews:review?.payload(),
      selected:[...selected],thresholds:{...thresholds},scope:'changed_or_required_or_selected_runtime_review',claim:'rule_prediction_not_measured_performance',
      results:records.map(r=>({query_id:r.queryId,number:r.number,schema:r.schema,compare_status:r.status,result:results.get(r.id),evidence:r.assessmentEvidence,manual_review:review?.state(r)}))};}
    function download(kind){if(dirty)return;const data=payload(),blob=new Blob([kind==='json'?JSON.stringify(data,null,2):csv(data)],{type:kind==='json'?'application/json;charset=utf-8':'text/csv;charset=utf-8'});
      const url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download='pcc-assessments.'+kind;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
    exports.forEach((b,i)=>b.addEventListener('click',()=>download(i===0?'json':'csv')));
    function detail(record){
      if(!record)return;const host=document.getElementById(record.id)?.querySelector('.query-assessment');if(!host)return;host.replaceChildren();
      const result=results.get(record.id),e=record.assessmentEvidence;host.className='query-assessment'+(result.status==='excluded'?'':' assessment-'+result.status);
      if(result.status==='excluded')host.append(el('p',record.status==='same'?'原始计划一致。此项没有需要复核的计划差异。':'无法完整比较，请查看采集状态和问题记录。'));
      else host.append(el('h3','规则预判 · '+labels[result.status]));
      review?.detail(record,host);
      if(e.runtime_notes?.length){
        const box=el('details',undefined,'assessment-evidence');box.append(el('summary','运行时提示 · '+e.runtime_notes.length+' 项'));
        e.runtime_notes.forEach(r=>{box.append(el('p',r.rule_id+' · '+rules.get(r.rule_id).title+' · '+(selected.includes(r.rule_id)?'已选入复核':'仅供实测参考')),
          el('p',r.reason),el('p',r.check));});host.append(box);
      }
      if(result.status==='excluded')return;
      if(dirty)host.append(el('p','显示上次规则的结果，修改尚未应用。','assessment-stale'));
      result.reasons.forEach(reason=>host.append(el('p',reason)));
      (e.cautions||[]).forEach(c=>host.append(el('p',c,'assessment-hint')));
      (e.mandatory_reviews||[]).forEach(r=>{const box=el('p',r.rule_id+' 复核方法 · '+r.check+' ');const link=el('a','TiDB 依据 ↗');link.href=rules.get(r.rule_id).source;link.target='_blank';link.rel='noopener noreferrer';box.append(link);host.append(box);});
      const matched=e.matches.filter(m=>result.matched.includes(m.id));
      matched.forEach(m=>{const box=el('details',undefined,'assessment-evidence');box.append(el('summary',`${m.rule_id} · ${rules.get(m.rule_id)?.title||m.rule_id} · ${reviewCards(e,selected).length?'仅供参考':labels[m.direction]}`),el('p',m.reason));
        if((m.requires_rules||[]).length>1)box.append(el('p','组合判断需同时勾选：'+m.requires_rules.join('、'),'assessment-hint'));
        if(m.basis==='estimated_work')box.append(el('p','估算模型预判 · 不是实测耗时；查看下方两端工作量及模型假设。','assessment-hint'));
        if(m.model){const table=el('table');const head=el('tr');['工作量指标','旧计划','新计划','权重'].forEach(s=>head.append(el('th',s)));table.append(head);
          Object.keys(m.model.weights).forEach(key=>{const row=el('tr');[m.model.labels[key],m.model.old.units[key],m.model.new.units[key],m.model.weights[key]].forEach(v=>row.append(el('td',String(v))));table.append(row);});table.className='assessment-model-table';box.append(table);
          [...m.model.assumptions,...m.model.old.assumptions,...m.model.new.assumptions].forEach(s=>box.append(el('p',s,'assessment-hint')));}
        if(m.guards.length)box.append(el('p','阈值证据：'+m.guards.map(g=>g.label+' '+(g.value??`${g.output} / ${g.input}`)+'；阈值 '+(g.constant??thresholds[g.setting])).join(' · '),'assessment-hint'));
        [['old',m.old_nodes],['new',m.new_nodes]].forEach(([side,nodes])=>nodes.forEach(id=>{const a=el('a',side+' '+id);a.href='#'+record.id+'-'+side+'-'+id;box.append(a);}));
        m.event_refs.forEach(ref=>{const a=el('a','差异 '+ref);a.href='#'+record.id+'-diff-'+ref;box.append(a);});host.append(box);});
      if(result.uncovered.length){const box=el('details');box.append(el('summary',`未覆盖差异 · ${result.uncovered.length} 条`));result.uncovered.forEach(ref=>{const a=el('a','差异 '+ref);a.href='#'+record.id+'-diff-'+ref;box.append(a);});host.append(box);}
      const hints=e.hints.filter(h=>selected.includes(h.rule_id));if(hints.length){const box=el('details');box.append(el('summary','复核依据与缺少的证明'));const list=el('ul');hints.forEach(h=>list.append(el('li',h.rule_id+' · '+rules.get(h.rule_id).title+'：'+h.reason)));box.append(list);host.append(box);}
    }
    review=PCCReview.attach(records,config,r=>JSON.stringify({evidence:r.reviewBasis,rules:config.rules_fingerprint,profile:profileId,selected:[...selected].sort(),thresholds:Object.fromEntries(Object.entries(thresholds).sort()),result:results.get(r.id)}),()=>dirty,refresh);
    message();return {matches:r=>review.matches(r)&&(filter===null||(filter===''?results.get(r.id)?.status!=='excluded':results.get(r.id)?.status===filter)),label:r=>dirty?'预判待更新':labels[results.get(r.id)?.status],
      status:r=>results.get(r.id)?.status,clearFilter:()=>{filter=null;review.clearFilter();renderCounts();},detail,payload,isDirty:()=>dirty};
  }
  return {evaluate,guardPass,csv,attach};
})();
'''
