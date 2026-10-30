"""Offline report browsing. Group membership is never an approval decision."""
import json

from pcc_tool.models import canonical, digest


def plan_group(irs, query_id, status):
    """Match both ordered trees; object names, expressions and estimates are excluded."""
    pair = [irs.get((query_id, side)) for side in ("old", "new")]
    if status not in {"same", "changed"} or any(not ir or ir.parse_status != "ok" or not ir.roots for ir in pair):
        return "", "计划不完整，未分组"

    def shape(node):
        return [node.operator, node.role, node.task, node.engine, [shape(child) for child in node.children]]

    shapes = [[shape(node) for node in ir.roots] for ir in pair]
    signature = digest(shapes)
    roots = [" + ".join(node.operator for node in ir.roots) for ir in pair]
    return signature, roots[0] if roots[0] == roots[1] else " → ".join(roots)


def plan_copy_text(snapshot, ir):
    """Copy original row EXPLAIN where available, including IDs and tree branches."""
    if snapshot and snapshot.format == "row" and snapshot.columns and snapshot.rows:
        lines = ["\t".join(snapshot.columns)]
        for row in snapshot.rows:
            values = [row.get(column, "") for column in snapshot.columns] if isinstance(row, dict) else row
            lines.append("\t".join("NULL" if value is None else str(value) for value in values))
        return "\n".join(lines)
    if not ir or not ir.roots:
        return ""
    rows = []
    known = {"id", "estRows", "task", "taskType", "access object", "accessObject",
             "operator info", "operatorInfo", "subOperators"}

    def visit(node, prefix="", branch=""):
        raw = node.evidence.get("raw") or {}
        raw = raw if isinstance(raw, dict) else {}
        name = node.evidence.get("raw_id", node.operator).lstrip(" │├└─\t")
        estimate = raw.get("estRows", node.estimate)
        extra = {key: value for key, value in raw.items() if key not in known}
        rows.append(([prefix + branch + name, "" if estimate is None else str(estimate),
                      str(raw.get("task", raw.get("taskType", node.task))),
                      str(raw.get("access object", raw.get("accessObject", node.access))),
                      str(raw.get("operator info", raw.get("operatorInfo", node.attributes)))], extra))
        indent = prefix + ("  " if branch == "└─" else "│ " if branch else "")
        for i, child in enumerate(node.children):
            visit(child, indent, "└─" if i == len(node.children) - 1 else "├─")

    for root in ir.roots:
        visit(root)
    has_extra = any(extra for _, extra in rows)
    header = "id\testRows\ttask\taccess object\toperator info" + ("\tother fields" if has_extra else "")
    return "\n".join([header] + ["\t".join(values + ([canonical(extra)] if has_extra else [])) for values, extra in rows])


def navigation_markup(records):
    # Escape script end tags even inside JSON strings. No user value is inserted as HTML by JS.
    payload = json.dumps(records, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    initial = records[0]["html"] if records else ""
    return '''<div class="review-layout" id="review-workspace">
<aside class="sql-sidebar" aria-label="SQL 浏览目录">
  <div class="sidebar-heading"><h2>SQL 目录</h2><span id="visible-count" aria-live="polite"></span></div>
  <div class="filters"><input id="search" type="search" aria-label="搜索 SQL、对象或标识" placeholder="搜索 SQL、表名…"></div>
  <div class="sidebar-tools"><label class="sr-only" for="group-mode">分组方式</label><select id="group-mode"><option value="all">全部 SQL</option><option value="schema">按数据库</option><option value="plan">按相似计划</option></select><button id="clear-group" type="button" hidden>退出分组</button></div>
  <p id="group-help" class="sidebar-note"></p><div id="group-list"></div>
  <details class="scope-settings"><summary>筛选与范围</summary>
    <label for="status">比较状态</label><select id="status"><option value="" selected>全部状态</option><option value="changed">存在差异</option><option value="same">执行计划一致</option><option value="unavailable">无法比对</option><option value="attention">差异与无法比对</option><option value="incomparable">比较未完成</option><option value="not_run">未运行</option><option value="needs_sample">缺少样例</option></select>
    <h3>本次暂不审核</h3><p class="sidebar-note">勾选库后隐藏，不计为通过；刷新后恢复。</p><div id="schema-scope"></div>
    <details><summary>分组口径</summary><p class="sidebar-note">按默认数据库归类，跨库依赖仍保留。相似计划按 old / new 两棵有序树的算子、角色、任务和引擎归类，忽略表名、谓词与估算值；各条 SQL 仍需独立评审。</p></details>
  </details>
  <p id="scope-count" class="sidebar-note" aria-live="polite"></p>
  <div class="directory-tools"><span>当前页</span><label class="sr-only" for="jump-number">跳转到 SQL 编号</label><div class="jump-controls"><input id="jump-number" type="number" min="1" placeholder="SQL 编号"><button type="button" id="jump-query" aria-label="跳转到指定 SQL">跳转</button></div></div>
  <p id="nav-message" role="status"></p><nav id="sql-nav" aria-label="当前页 SQL"></nav>
<div class="page-controls"><button type="button" id="previous-page" aria-label="目录上一页">‹</button><span id="page-state" aria-live="polite"></span><button type="button" id="next-page" aria-label="目录下一页">›</button>
<label class="sr-only" for="page-size">目录每页条数</label><select id="page-size"><option value="10">10 条 / 页</option><option value="20">20 条 / 页</option><option value="50">50 条 / 页</option></select></div>
</aside><section class="sql-results" aria-label="SQL 比较结果">
<div class="focus-toolbar"><span id="detail-position" aria-live="polite"></span><div><button type="button" id="previous-query">← 上一条</button><button type="button" id="next-query">下一条 →</button></div></div>
<div id="sql-cards">''' + initial + '''</div><div id="empty" class="empty" role="status"''' + (' hidden' if records else '') + '''>没有符合当前筛选条件的 SQL。</div>
<noscript>启用 JavaScript 可切换 SQL、分组和复制计划。此处仅展示首条 SQL，完整数据在 differences.csv 中。</noscript>
</section></div><script type="application/json" id="report-data">''' + payload + '</script>'


NAV_STYLES = '''
.review-layout{display:grid;grid-template-columns:280px minmax(0,1fr);align-items:start;background:white;border:1px solid #e4e4e9;border-radius:14px;box-shadow:0 4px 22px #1d244008;scroll-margin-top:16px}
.sql-sidebar{position:sticky;top:16px;background:#fafafb;border-right:1px solid var(--line);border-radius:14px 0 0 14px;padding:20px 16px 0;max-height:calc(100vh - 32px);overflow-y:auto;scrollbar-width:thin}.sidebar-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}.sidebar-heading h2{font-size:13px;font-weight:600;color:#6b6b74}#visible-count{font-size:11px;color:#767680;font-variant-numeric:tabular-nums}.filters input{width:100%;border:1px solid #e1e1e7;border-radius:7px;background:#fff;padding:9px 11px;font-size:12px;color:var(--ink);outline-offset:1px}.filters input::placeholder{color:#a0a0a8}
.sidebar-tools{display:flex;gap:6px;align-items:center;margin:9px 0}.sql-sidebar select{border:1px solid #e1e1e7;border-radius:6px;background:white;padding:7px 9px;color:#656570;font-size:11px;min-width:0}.sidebar-tools select{flex:1}.sidebar-note{font-size:11px;color:#787882;margin:7px 0 10px}.sidebar-note:empty,#nav-message:empty{display:none}.sql-sidebar label:not(.sr-only){display:block;font-size:11px;color:var(--muted);margin:10px 0 5px}.scope-settings{margin:12px 0}.scope-settings>summary{font-size:11px;color:#73737d}.scope-settings h3{font-size:11px;margin-top:14px;color:#71717c}.scope-settings select{width:100%}.scope-settings>details{border-top:1px solid var(--line);padding-top:8px}.scope-settings>details>summary{font-size:10px}#schema-scope label{overflow-wrap:anywhere;font-size:10px}.directory-tools{border-top:1px solid var(--line);padding-top:12px;margin:14px 0 8px;display:flex;align-items:center;justify-content:space-between;gap:8px}.directory-tools>span{font-size:10px;color:#7b7b85}.jump-controls{display:flex;align-items:center;gap:4px}.jump-controls input{font-size:10px;width:75px;border:0;background:transparent;color:#666670;min-width:0;padding:3px}.jump-controls button,#clear-group{border:0;background:transparent;font-size:10px;color:#7491b1;padding:3px 4px}#clear-group{white-space:nowrap}#nav-message{font-size:11px;color:#9a7538;margin:7px 0}
#sql-nav{display:grid;gap:4px}#sql-nav a{display:block;padding:10px 11px;border:1px solid transparent;border-radius:8px;text-decoration:none;color:#555a64;min-width:0}#sql-nav a:hover{background:#f0f0f4}#sql-nav a[aria-current="true"]{background:#eaf2fc;border-color:#dbe9f9}.nav-item-heading{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:4px}.nav-item-heading strong{font:500 11px/1.4 ui-monospace,SFMono-Regular,monospace;color:#8a909a}.nav-item-heading small{font-size:10px;color:#5f7c69}.nav-item-heading small:before{content:"";display:inline-block;width:4px;height:4px;border-radius:50%;background:currentColor;margin-right:5px;vertical-align:2px}#sql-nav a[data-status="changed"] small{color:#9b752e}#sql-nav a[data-status="incomparable"] small,#sql-nav a[data-status="not_run"] small{color:#a45e57}#sql-nav a[aria-current="true"] strong{color:#377cb7}.nav-preview{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font:11px/1.5 ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere;color:#606777}.nav-schema{display:block;font-size:10px;color:#777e8a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:4px}
#group-list{display:grid;gap:4px;max-height:180px;overflow-y:auto;scrollbar-width:thin}.group-choice{text-align:left;background:white;border:1px solid #e8e8ed;border-radius:7px;padding:8px 10px;color:#73737d;min-width:0}.group-choice[aria-pressed="true"]{background:#edf4fd;border-color:#d9e7f9}.group-choice strong{display:block;font-size:11px;font-weight:500;overflow-wrap:anywhere}.group-choice small{display:block;font-size:10px;color:#787882;margin-top:4px}.page-controls{display:flex;align-items:center;gap:8px;padding:12px 0;margin-top:10px;position:sticky;bottom:0;background:#fafafb;border-top:1px solid var(--line)}.page-controls button{font-size:19px;line-height:1;padding:2px 6px;border:0;background:transparent;color:#6e7684}.page-controls span{font:10px ui-monospace,monospace;color:#92929e}.page-controls select{margin-left:auto;font-size:10px;padding:4px 5px}
.sql-results{min-width:0}.focus-toolbar{display:flex;align-items:center;justify-content:space-between;padding:12px 26px;border-bottom:1px solid #f0f0f3;gap:10px}.focus-toolbar>span{font-size:10px;color:#7b7b85;font-variant-numeric:tabular-nums}.focus-toolbar>div{display:flex;gap:18px}.focus-toolbar button{border:0;background:transparent;color:#7c8798;font-size:11px;padding:3px 0}.focus-toolbar button:hover:not(:disabled){color:var(--blue)}
@media(max-width:1100px){.review-layout{grid-template-columns:240px minmax(0,1fr)}.sql-sidebar{padding:16px 12px 0}.focus-toolbar{padding:12px 20px}}
@media(max-width:760px){.review-layout{grid-template-columns:1fr}.sql-sidebar{position:static;max-height:none;border-right:0;border-bottom:1px solid var(--line);border-radius:14px 14px 0 0;padding:16px}.sidebar-heading{margin-bottom:10px}#sql-nav{max-height:210px;overflow-y:auto;grid-template-columns:1fr 1fr}#group-list{max-height:150px}.page-controls{position:static;margin:10px 0 0;padding:10px 0 0}.focus-toolbar{padding:12px 20px}.sql-results article{padding:18px}.directory-tools{margin-top:10px}.plan-scroll{max-height:65vh}}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
'''


SCRIPT = r'''
const records=JSON.parse(document.getElementById('report-data').textContent);
const byId=new Map(records.map(r=>[r.id,r]));
const planNumbers=new Map([...new Set(records.map(r=>r.planGroup).filter(Boolean))].map((key,i)=>[key,i+1]));
const search=document.getElementById('search'), status=document.getElementById('status');
const mode=document.getElementById('group-mode'), pageSize=document.getElementById('page-size');
const metrics=Array.from(document.querySelectorAll('button[data-filter]'));
let activeGroup=null, activeKey=null, activeId=null, page=0, filtered=[];
const skipped=new Set();
const labels={same:'一致',changed:'有差异',incomparable:'无法比对',not_run:'无法比对'};
function element(tag,text,className){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;}
function statusMatch(r){const s=status.value;return s==='attention'?r.attention:s==='needs_sample'?r.needsSample:s==='unavailable'?r.category==='unavailable':!s||r.status===s;}
function baseMatch(r){return !skipped.has(r.schema)&&statusMatch(r)&&r.search.includes(search.value.trim().toLowerCase());}
function groupValue(r){return mode.value==='schema'?r.schema:r.planGroup;}
function buildGroups(){
  const list=document.getElementById('group-list'), scroll=list.scrollTop;list.replaceChildren();
  document.getElementById('group-help').textContent=mode.value==='plan'?'结构相似，仍需逐条判断。':mode.value==='schema'?'按 SQL 默认数据库归类。':'';
  if(mode.value==='all')return;
  const groups=new Map();
  records.filter(baseMatch).forEach(r=>{const key=groupValue(r);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);});
  const all=element('button','显示全部分组','group-choice');all.type='button';all.dataset.allGroups='';all.setAttribute('aria-pressed',String(activeKey===null));list.append(all);
  [...groups].sort((a,b)=>mode.value==='plan'?b[1].length-a[1].length:0).forEach(([key,members])=>{
    const b=element('button',undefined,'group-choice');b.type='button';b.dataset.groupKey=key;b.setAttribute('aria-pressed',String(key===activeKey));
    const name=mode.value==='schema'?(key||'全限定 SQL / 未指定库'):(key?`结构组 ${planNumbers.get(key)} · ${members[0].planTitle}`:'计划不完整 / 未分组');
    b.append(element('strong',`${name} · ${members.length} 条`));
    const counts=['same','changed','unavailable'].map(s=>members.filter(r=>r.category===s).length);
    b.append(element('small',counts.map((count,i)=>count?`${['一致','差异','无法比对'][i]} ${count}`:'').filter(Boolean).join(' · ')));list.append(b);
  });
  list.scrollTop=scroll;
}
function render(){
  filtered=records.filter(r=>baseMatch(r)&&(!activeGroup||activeGroup.has(r.id))&&(activeKey===null||groupValue(r)===activeKey));
  const size=Number(pageSize.value), pages=Math.max(1,Math.ceil(filtered.length/size));page=Math.min(Math.max(0,page),pages-1);
  const visible=filtered.slice(page*size,(page+1)*size);
  const selected=visible.find(r=>r.id===activeId)||visible[0];activeId=selected?.id||null;
  // HTML was generated and escaped by the report renderer, never composed from search/input values.
  if(!selected||!document.getElementById(selected.id))document.getElementById('sql-cards').innerHTML=selected?.html||'';
  const nav=document.getElementById('sql-nav');nav.replaceChildren();
  visible.forEach(r=>{
    const a=element('a');a.href='#'+r.id;a.dataset.queryLink='';a.dataset.status=r.status;a.setAttribute('aria-current',String(r.id===activeId));
    const heading=element('span',undefined,'nav-item-heading');heading.append(element('strong',`#${String(r.number).padStart(3,'0')}`),element('small',labels[r.status]));
    const schema=element('span',r.schema||'未指定库','nav-schema');schema.setAttribute('title',r.schema||'未指定库');
    a.append(heading,element('span',r.preview,'nav-preview'),schema);nav.append(a);
  });
  document.getElementById('page-state').textContent=`${page+1} / ${pages}`;
  document.getElementById('previous-page').disabled=page===0;document.getElementById('next-page').disabled=page>=pages-1;
  document.getElementById('visible-count').textContent=filtered.length===records.length?`${records.length} 条`:`${filtered.length} / ${records.length} 条`;
  const scopeCount=document.getElementById('scope-count');scopeCount.hidden=skipped.size===0;scopeCount.textContent=`暂不审核 ${skipped.size} 个库 · ${records.filter(r=>skipped.has(r.schema)).length} 条`;
  const position=selected?filtered.indexOf(selected):-1;
  document.getElementById('detail-position').textContent=selected?`${position+1} / ${filtered.length} 条 SQL`:'';
  document.getElementById('previous-query').disabled=position<=0;document.getElementById('next-query').disabled=position<0||position>=filtered.length-1;
  document.getElementById('clear-group').hidden=!activeGroup&&activeKey===null;
  document.getElementById('empty').hidden=filtered.length!==0;
  metrics.forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.filter===status.value)));
  document.querySelectorAll('[data-skip-schema]').forEach(c=>{c.checked=skipped.has(c.dataset.skipSchema);});
  buildGroups();
}
function resetPage(){page=0;document.getElementById('nav-message').textContent='';render();}
function jump(id){
  const r=byId.get(id);if(!r)return false;
  if(!filtered.includes(r)){
    activeGroup=null;activeKey=null;search.value='';status.value='';skipped.delete(r.schema);
    document.getElementById('nav-message').textContent='已恢复该 SQL 所在库，并清除筛选以定位。';render();
  }
  page=Math.floor(filtered.indexOf(r)/Number(pageSize.value));activeId=id;render();
  return true;
}
function reveal(hash){
  const match=hash.match(/^(query-\d+)(?:-|$)/);if(match&&!jump(match[1]))return;
  const target=document.getElementById(hash);if(!target)return;
  for(let n=target;n;n=n.parentElement){if(n.tagName==='DETAILS')n.open=true;}
  target.scrollIntoView({block:'start'});
}
async function copyPlan(button){
  const card=button.closest('article'), r=byId.get(card.id), side=button.dataset.copyPlan;
  const text=side==='both'?`old · 升级前\n${r.oldPlan||'没有可用计划'}\n\nnew · 升级后\n${r.newPlan||'没有可用计划'}`:(side==='old'?r.oldPlan:r.newPlan);
  const feedback=card.querySelector('.copy-status');
  card.querySelectorAll('.copy-manual').forEach(area=>area.remove());
  if(!text){feedback.textContent='此侧没有可用计划。';return;}
  try{if(!navigator.clipboard)throw new Error('unavailable');await navigator.clipboard.writeText(text);feedback.textContent='已复制执行计划文本';return;}catch(error){/* Local file clipboard can be restricted. */}
  const area=element('textarea',undefined,'copy-manual');area.value=text;area.setAttribute('aria-label','执行计划文本，按 Cmd+C 或 Ctrl+C 复制');card.querySelector('.plan-toolbar').after(area);area.focus();area.select();
  let copied=false;try{copied=!!document.execCommand('copy');}catch(error){/* Keep selected text for manual copy. */}
  if(copied){area.remove();feedback.textContent='已复制执行计划文本';button.focus();}else{feedback.textContent='浏览器未允许自动复制，文本已选中，请按 Cmd+C / Ctrl+C。';}
}
metrics.forEach(b=>b.addEventListener('click',()=>{activeGroup=null;activeKey=null;status.value=b.dataset.filter;resetPage();}));
search.addEventListener('input',()=>{activeKey=null;resetPage();});status.addEventListener('change',()=>{activeKey=null;resetPage();});
mode.addEventListener('change',()=>{activeGroup=null;activeKey=null;resetPage();});pageSize.addEventListener('change',resetPage);
document.getElementById('clear-group').addEventListener('click',()=>{activeGroup=null;activeKey=null;resetPage();});
document.getElementById('previous-page').addEventListener('click',()=>{page--;render();document.getElementById('review-workspace').scrollIntoView({block:'start'});});
document.getElementById('next-page').addEventListener('click',()=>{page++;render();document.getElementById('review-workspace').scrollIntoView({block:'start'});});
function adjacentQuery(offset){const i=filtered.findIndex(r=>r.id===activeId), next=filtered[i+offset];if(next)reveal(next.id);}
document.getElementById('previous-query').addEventListener('click',()=>adjacentQuery(-1));
document.getElementById('next-query').addEventListener('click',()=>adjacentQuery(1));
function jumpNumber(){const n=Number(document.getElementById('jump-number').value);if(!Number.isInteger(n)||!jump('query-'+n)){document.getElementById('nav-message').textContent=`请输入 1 到 ${records.length} 的 SQL 编号。`;return;}reveal('query-'+n);}
document.getElementById('jump-query').addEventListener('click',jumpNumber);
document.getElementById('jump-number').addEventListener('keydown',event=>{if(event.key==='Enter')jumpNumber();});
const scope=document.getElementById('schema-scope');
[...new Set(records.map(r=>r.schema))].sort().forEach(schema=>{const label=element('label'), c=element('input');c.type='checkbox';c.dataset.skipSchema=schema;label.append(c,element('span',' '+(schema||'未指定库')));scope.append(label);});
document.addEventListener('change',event=>{
  const t=event.target;if(t.matches('[data-skip-schema]')){t.checked?skipped.add(t.dataset.skipSchema):skipped.delete(t.dataset.skipSchema);activeKey=null;resetPage();}
  if(t.matches('.expand-plan'))t.closest('article').querySelectorAll('.node-details').forEach(d=>{d.open=t.checked||d.dataset.defaultOpen==='true';});
});
document.addEventListener('click',event=>{
  const t=event.target.closest('button,a');if(!t)return;
  if(t.matches('[data-all-groups]')){activeKey=null;activeGroup=null;resetPage();}
  if(t.matches('[data-group-key]')){activeKey=t.dataset.groupKey;activeGroup=null;resetPage();}
  if(t.matches('[data-group]')){activeGroup=new Set(t.dataset.group.split(','));activeKey=null;status.value='';search.value='';resetPage();document.getElementById('review-workspace').scrollIntoView({block:'start'});}
  if(t.matches('[data-copy-plan]'))copyPlan(t);
  if(t.matches('a[href^="#"]')){event.preventDefault();reveal(t.getAttribute('href').slice(1));}
});
render();
if(location.hash)reveal(location.hash.slice(1));
window.addEventListener('hashchange',()=>reveal(location.hash.slice(1)));
'''
