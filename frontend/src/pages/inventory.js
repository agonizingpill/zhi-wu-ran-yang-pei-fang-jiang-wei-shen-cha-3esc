import {store,refresh} from '../store.js';import {api,session} from '../api.js';import {heading} from '../components/common.js';import {escape,decimal,formData,notify} from '../utils.js';
const stateLabel={pending:'待主管裁定',normal_loss:'正常损耗已调账',abnormal_shortage:'异常短缺已调账',rejected:'主管驳回未调账'};
const stateClass={pending:'rejected',normal_loss:'approved',abnormal_shortage:'rejected',rejected:'rejected'};
const lotOf=c=>store.lots.find(l=>l.id===c.lot_id);
const today=()=>new Date().toISOString().slice(0,10);
function probabilityBlock(c){
 if(c.anomaly_probability==null){const reason=c.probability_evidence?.reason||'证据不足，不输出概率';return `<span class="status rejected">证据不足</span><p class="hint">${escape(reason)}</p>`;}
 const e=c.probability_evidence||{};
 return `<span class="status rejected">异常参考概率 ${decimal(c.anomaly_probability,3)}</span><p class="hint">盘亏率${decimal(e.shortage_rate*100,3)}% · 正常均值${decimal((e.mean_rate??0)*100,3)}% · 2σ阈值${decimal((e.threshold_rate??0)*100,3)}%<br>基于${e.normal_samples??0}条正常损耗历史，概率仅供参考，不代替主管裁定</p>`;
}
export function render(){
 return heading('10','盘点审查：固定账存实盘，按差额调账','登记瞬间冻结账存与实盘；盘点后的称料领用保留在余额中。主管按冻结差额调账，概率仅作参考，证据不足时不输出概率。')
 +`<div class="split"><section class="panel"><h2>登记染料盘点</h2><form id="count-form">
 <label>盘点单号<input name="code" placeholder="CNT-A-1118-01" required></label>
 <label>染料批次<select name="lot_id">${store.lots.map(l=>`<option value="${l.id}">${escape(l.code)} · ${l.component}组分 · 当前账面${decimal(l.available_kg,3)}kg</option>`).join('')}</select></label>
 <div class="grid2"><label>盘点日期<input name="counted_on" type="date" value="${today()}" required></label>
 <label>实盘 / kg<input name="measured_kg" type="number" step=".001" min="0" value="10" required></label></div>
 <label>备注（可选）<textarea name="note" maxlength="400" placeholder="称料仍在进行，账存与实盘先固定，后续领用不受影响"></textarea></label>
 <button class="primary" ${session().role==='viewer'?'disabled':''}>固定账存与实盘</button></form>
 <div class="rule">登记只冻结账存/实盘，不直接覆盖库存。同批次当日限一张盘点单；同批次更新盘点已调账后，旧盘点拒绝入账。</div>
 <h2 class="subhead">分组学习与检验依据</h2>${store.anomalyModels.map(m=>{const ready=!!(m.params&&m.params.mean);const v=m.validation||{};const c=v.confusion||{};return `<article class="trial"><div class="code">${m.component} 组分 · ${ready?'模型已就绪':'证据不足，不输出概率'}</div>${ready?`<p class="hint">正常样本${m.params.normal_samples}条 · 盘亏率均值${decimal(m.params.mean*100,3)}% · 标准差${decimal(m.params.std*100,3)}% · 2σ阈值${decimal(m.params.threshold_rate*100,3)}%</p><p class="hint">检验表现（回判）：异常命中${c.tp??0} · 误报${c.fp??0} · 正常正确${c.tn??0} · 漏报${c.fn??0} · 召回${v.recall??'-'} · 精确率${v.precision??'-'}</p>`:`<p class="hint">${escape(m.training_evidence?.reason||'正常损耗样本不足，不输出概率')}（累计样本${m.sample_size}条）</p>`}<details><summary class="hint">回看 ${(m.training_evidence?.rows||[]).length} 条训练依据</summary><table><thead><tr><th>盘点单</th><th>账存</th><th>实盘</th><th>称料消耗</th><th>盘亏率</th><th>主管裁定</th></tr></thead><tbody>${(m.training_evidence?.rows||[]).map(r=>`<tr><td>${escape(r.lot_code)}-${r.count_id}</td><td>${decimal(r.book_kg,2)}</td><td>${decimal(r.measured_kg,2)}</td><td>${decimal(r.consumed_kg,2)}</td><td>${r.shortage_rate==null?'-':decimal(r.shortage_rate*100,2)+'%'}</td><td>${r.label==='normal_loss'?'正常损耗':'异常短缺'}</td></tr>`).join('')}</tbody></table></details></article>`}).join('')||'<div class="empty">主管完成首次调账后生成分组模型。</div>'}</section>
 <section class="panel"><h2>待主管裁定的盘点</h2>${store.counts.filter(c=>c.state==='pending').map(c=>{const l=lotOf(c);return `<article class="complaint trial" data-id="${c.id}"><div class="code">${escape(c.code)} · ${escape(l?.code)} · ${l?.component}组分</div><h3>${stateLabel[c.state]} <span class="status ${stateClass[c.state]}">${escape(c.counted_on)}</span></h3><div class="grid3"><div>冻结账存<strong class="value">${decimal(c.book_kg,3)}kg</strong></div><div>冻结实盘<strong class="value">${decimal(c.measured_kg,3)}kg</strong></div><div>盘亏差额<strong class="value">${decimal(c.shortage_kg,3)}kg</strong></div></div><p class="hint">登记前累计称料消耗${decimal(c.consumed_kg,3)}kg · 登记人${escape(c.created_by)}${c.note?'<br>备注：'+escape(c.note):''}</p>${probabilityBlock(c)}<form class="verdict-form" data-key="${c.id}"><label>主管裁定<select name="verdict"><option value="normal_loss">正常损耗（按差额调账）</option><option value="abnormal_shortage">异常短缺（按差额调账）</option><option value="rejected">驳回，不调整库存</option></select></label><label>裁定依据<textarea name="note" minlength="5" required placeholder="核对历史损耗区间、称料凭据与现场情况"></textarea></label><button ${session().role!=='admin'?'disabled':''}>主管提交裁定并调账</button></form><div class="rule">调账差额=实盘−账存（登记时冻结值）；盘点后的领料保留。调账后库存为负或同批次更新盘点已调账时整单拒绝，重试不会重复调整。</div></article>`}).join('')||'<div class="empty">没有待裁定盘点。</div>'}
 <h2 class="subhead">已裁定盘点与调账余额</h2><table><thead><tr><th>盘点单/批次</th><th>冻结账存→实盘</th><th>调账差额</th><th>调账前→后余额</th><th>裁定</th></tr></thead><tbody>${store.counts.filter(c=>c.state!=='pending').map(c=>{const l=lotOf(c);return `<tr><td>${escape(c.code)}<br><span class="hint">${escape(l?.code)} · ${escape(c.counted_on)}</span></td><td>${decimal(c.book_kg,3)}→${decimal(c.measured_kg,3)}</td><td>${c.adjusted_kg==null?'-':decimal(c.adjusted_kg,3)+'kg'}</td><td>${c.balance_before==null?'-':decimal(c.balance_before,3)+'→'+decimal(c.balance_after,3)+'kg'}</td><td><span class="status ${stateClass[c.state]}">${stateLabel[c.state]}</span><br><span class="hint">${escape(c.verdict_by)}</span></td></tr>`}).join('')}</tbody></table></section></div>`;
}
export function bind(render){
 async function save(path,body,msg){try{await api(path,'POST',body);await refresh();render();notify(msg);}catch(e){notify(e.message);}}
 const f=document.querySelector('#count-form');
 if(f)f.onsubmit=e=>{e.preventDefault();const d=formData(e.target);d.lot_id=Number(d.lot_id);d.measured_kg=Number(d.measured_kg);save('/stock-counts',d,'盘点已登记，账存与实盘已固定');};
 document.querySelectorAll('.verdict-form').forEach(x=>x.onsubmit=e=>{e.preventDefault();save('/stock-counts/'+x.dataset.key+'/verdict',formData(x),'主管裁定已记录，库存按冻结差额调整');});
}
