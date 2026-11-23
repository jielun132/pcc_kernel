"""Explain the active assessment from its own proof, unresolved changes and checks."""

RATIONALE_SCRIPT = r'''
const costReasons={
    A01:['新计划按相同聚簇主键直接点查，省去了单点范围扫描的执行环节。','新计划对同一个主键值走单点范围扫描，增加了原来直接点查可省去的执行环节。'],
    A02:['同一索引已经覆盖所需列，新计划省去了逐条取表数据的回表步骤。','相同的候选行原来只读覆盖索引，新计划还要按行回表取数。'],
    A03:['新计划先在索引端过滤，预计需要回表的候选行更少。','过滤移到回表之后，预计有更多候选行需要先读表再淘汰。'],
    A06:['扫描范围相同，新计划在 TiKV 先过滤，预计传给 TiDB 的行数减少。','过滤从 TiKV 移回 TiDB，预计原可在存储端淘汰的行也要上传。'],
    A08:['最终排序和截断相同，新计划让 TiKV 先取局部 TopN，预计上传的候选行更少。','新计划去掉了 TiKV 的局部 TopN，预计更多候选行需要上传后再选取。'],
    A09:['新计划在扫描端先按相同的窗口截断，预计交给最终 Limit 的候选行更少。','局部 Limit 被移除，原可在扫描端截断的候选行可能继续上传。'],
    A11:['排序键与截断参数相同，新计划用有界 TopN 替代全量排序，预计需要维护的排序数据更少。','相同排序键和截断参数下，有界 TopN 变成先全量排序再取前几行，预计排序工作量增加。'],
    A12:['新计划在存储端先做部分聚合，只上传中间结果，预计传输的记录更少。','新计划把原始行上传后再聚合，预计比只传部分聚合结果需要传输更多记录。'],
    A17:['SQL 条件恒假，新计划用空 TableDual 直接返回，省去了计划里的扫描分支。','SQL 条件恒假，新计划仍保留原本可用空 TableDual 替代的扫描分支。'],
    A20:['新计划在保留 NULL 语义的同时用哈希键查找反半连接候选，预计需要逐对检查的候选组合更少。','反半连接失去哈希查找键，恢复为对笛卡尔候选逐对过滤，预计需要检查的组合更多。'],
    A21:['相同的 NULL 安全等值条件成为半连接哈希键，预计可以跳过不匹配的候选组合。','相同的 NULL 安全等值条件退回候选过滤，失去按哈希键跳过不匹配组合的机会。']
};
function decisionSummary(record,result,selected,thresholds){
  const evidence=record.assessmentEvidence;
  if(result.status==='excluded')return {reason:'',check:''};
  const matches=evidence.matches.filter(m=>result.matched.includes(m.id));
  function proof(m){
    const text=costReasons[m.rule_id]?.[m.direction==='worse'?1:0]||m.reason;
    const guard=(m.guards||[]).find(g=>g.kind==='reduction'||g.kind==='ratio'||g.kind==='estimate');
    if(!guard)return text;
    const limit=guard.constant??thresholds[guard.setting];
    if(guard.kind==='estimate')return text+` 两端估算为 ${guard.input} 和 ${guard.output} 行，容差为 ${limit}%。`;
    const output=m.rule_id==='A12'?'部分聚合的估算输出行数':guard.kind==='ratio'?'截断上限':'过滤后的估算行数';
    return text+` 规则核对的输入为 ${guard.input}，${output}为 ${guard.output}，满足${guard.kind==='ratio'?'占比至多':'减少至少'} ${limit}%。`;
  }
  const unique=values=>[...new Set(values.filter(Boolean))];
  const join=values=>unique(values).map(v=>/[。！？]$/.test(v)?v:v+'。').join('');
  if(result.status!=='review'){
    const decisive=matches.filter(m=>m.direction===result.status)
      .sort((a,b)=>b.event_refs.length-a.event_refs.length);
    const byRule=new Map();
    for(const m of decisive)if(!byRule.has(m.rule_id))byRule.set(m.rule_id,proof(m));
    return {reason:join([...byRule.values()].slice(0,2)),check:''};
  }
  const holds=reviewCards(evidence,selected);
  const model=matches.find(m=>m.basis==='estimated_work');
  const reasons=holds.slice(0,2).map(h=>h.reason);
  const checks=holds.slice(0,2).map(h=>h.check);
  if(model)reasons.unshift(model.reason.split('。')[0]);
  reasons.push(...evidence.blockers);
  const directions=unique(matches.map(m=>m.direction).filter(d=>d!=='equivalent'));
  if(directions.length>1)reasons.push('不同变化给出相反方向。'+join(directions.map(d=>proof(matches.find(m=>m.direction===d)))));
  const refs=new Set(result.uncovered.map(String));
  const facts=(record.reviewFacts||[]).filter(f=>f.refs.some(n=>refs.has(String(n))));
  if(refs.size){
    if(directions.length===1&&!model&&!holds.length)reasons.push('已解释的部分倾向'+labels[directions[0]]+'。'+proof(matches.find(m=>m.direction===directions[0])));
    const uncovered=unique(facts.map(f=>f.label)).slice(0,3);
    const disabled=evidence.matches.filter(m=>m.event_refs.some(n=>refs.has(String(n)))&&
      [m.rule_id,...(m.requires_rules||[])].some(id=>!selected.includes(id)));
    const failed=result.reasons.filter(x=>x.includes('未满足阈值'));
    if(disabled.length)reasons.push('当前方案未启用覆盖这些差异所需的 '+unique(disabled.flatMap(m=>[m.rule_id,...(m.requires_rules||[])]).filter(id=>!selected.includes(id))).join('、'));
    reasons.push(...failed);
    reasons.push(`还有 ${refs.size} 项字段差异缺少完整证明${uncovered.length?'，涉及'+uncovered.join('、'):''}`);
    const projection=facts.some(f=>f.category==='engine'&&f.operators.includes('Projection'));
    if(projection){
      reasons.push('Projection 改变执行位置，单凭位置无法确定跨 Reader 传输的数据量是否减少');
      checks.push('对照两侧 Projection 的输出列与表达式，在相同绑定和数据下核对 Reader 传输的行数、字节数及计算耗时。');
    }else if(!checks.length){
      const hints=evidence.hints.filter(h=>selected.includes(h.rule_id)&&h.rule_id.startsWith('C'));
      checks.push(hints[0]?.reason||'逐项核对未覆盖字段两端的算子及输入，在相同数据和绑定下比较实际行数与耗时。');
    }
  }
  if(!reasons.length)reasons.push(...result.reasons);
  if(!checks.length)checks.push('用相同数据和绑定核对两端的实际行数、资源用量与耗时。');
  return {reason:join(reasons),check:join(checks)};
}
'''
