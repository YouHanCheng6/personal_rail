'use strict';
const $ = (id) => document.getElementById(id);
const state = {data:null, selected:new Set(), limit:12, preference:'balanced', version:0, busy:false, meta:null};
const escapeHTML = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const e = escapeHTML;
const duration = (n) => n < 60 ? `${n}分钟` : `${Math.floor(n/60)}小时${n%60 ? `${n%60}分` : ''}`;
const money = (n) => n === null || n === undefined ? '待核实' : new Intl.NumberFormat('zh-CN',{maximumFractionDigits:1}).format(n);
const dayTime = (v) => `${v.slice(5,10)} ${v.slice(11,16)}`;
const dayGap = (a,b) => Math.round((Date.parse(`${b.slice(0,10)}T00:00:00Z`)-Date.parse(`${a.slice(0,10)}T00:00:00Z`))/86400000);
const trainNames = (p) => p.legs.map(l=>l.train).join(' → ');
const sourceProvider = (url) => new URL(url).hostname==='train.qunar.com'?'去哪儿':'携程';
const sourceName = (url) => {
  const cities={jieyangjichang:'揭阳机场站',jieyang:'揭阳地区',longchuanxi:'龙川西',longchuan:'龙川地区',beijing:'北京',ganzhou:'赣州地区',nanchang:'南昌地区'};
  const parts=new URL(url).pathname.split('/').filter(Boolean);
  const dated=/^\d{4}-\d{2}-\d{2}$/.test(parts.at(-1));
  const slug=parts.at(dated?-2:-1);
  return slug.replace('-to-','-').split('-').map(s=>cities[s]||s).join(' → ')+(dated?` · ${parts.at(-1)}`:'');
};
async function api(url, options={}) {
  const response=await fetch(url,{...options, headers:{'Content-Type':'application/json',...options.headers}});
  let data; try{data=await response.json();}catch{throw new Error('服务没有返回可用结果，请重试。');}
  if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'查询条件无效，请检查日期、预算和换乘时间。');
  return data;
}
function setBusy(busy){
  state.busy=busy;
  $('search-button').disabled=busy;
  $('search-button').innerHTML=busy?'正在读取公开资料…':'查找并比较 <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24"><path d="M4 12h15M13 5l7 7-7 7"/></svg>';
  document.querySelectorAll('[data-sort]').forEach(b=>b.disabled=busy);
  $('results').setAttribute('aria-busy', String(busy));
}
function queryFromForm(){
  const form=new FormData($('search-form'));
  return {route:form.get('route'), departure_date:form.get('departure_date'),seat:form.get('seat'),
    budget:form.get('budget')?Number(form.get('budget')):null,min_transfer:Number(form.get('min_transfer')),
    same_station:form.has('same_station'),preference:state.preference};
}
async function search(){
  if(!$('search-form').reportValidity() || state.busy) return;
  const version=++state.version;
  const query=queryFromForm();
  state.data=null;state.selected.clear();state.limit=12;
  $('error').hidden=true;$('comparison').hidden=true;$('show-more').hidden=true;$('date-warning').hidden=true;$('export').disabled=true;
  $('results-title').textContent='候选行程';$('result-count').textContent='读取中';
  $('results').innerHTML='<p class="loading-label">正在读取公开路线页，计算直达与一次换乘…</p><div class="skeleton" aria-hidden="true"></div><div class="skeleton" aria-hidden="true"></div>';
  $('advice').innerHTML='<p>先核对资料，再比较取舍。参考日期与缺失信息会保留在结果中。</p>';
  $('model-label').textContent=`${state.meta.model} · 等待资料`;
  $('query-status').textContent='正在按所选日期读取公开资料，10分钟内重复查询使用缓存。';
  $('source-list').innerHTML='<li class="muted">正在核验来源访问策略与路线页。</li>';
  $('history-content').textContent='采集完成后更新记录。';
  setBusy(true);
  try{
    const data=await api('/api/plan',{method:'POST',body:JSON.stringify(query)});
    if(version!==state.version)return;
    state.data=data;
    const route=state.meta.routes.find(r=>r.id===query.route);
    $('results-title').textContent=`${route.origin} → ${route.destination}`;
    $('result-count').textContent=`${data.candidates.length} 个当日参考方案`;
    const good=data.sources.filter(s=>['fetched','cached'].includes(s.status)).length;
    $('query-status').textContent=`${query.departure_date} 出发 · ${good}/${data.sources.length} 个路线页可用 · 生成于 ${dayTime(data.created_at)} · 北京时间`;
    const dates=[...new Set(data.candidates.map(p=>p.source_date))];
    const mismatch=data.candidates.some(p=>!p.date_matches);
    const stale=data.sources.some(s=>s.status==='stale');
    const warnings=[];
    if(!data.presale_open)warnings.push(`所选日期尚未进入通常15天预售期，预计 ${data.sale_date} 进入查询窗口，具体以车站起售时间为准。`);
    if(mismatch)warnings.push(`公开页面参考日期为 ${dates.join('、')}，并非所有方案都对应所选日期。这里只比较参考，不推定当天开行。`);
    else if(data.candidates.length)warnings.push(`资料对应 ${dates.join('、')}，仍是第三方公开参考，尚未通过12306核验。`);
    if(stale)warnings.push('部分来源刷新失败，仍保留上次快照时间。');
    $('date-warning').textContent=warnings.join(' ');$('date-warning').hidden=!warnings.length;
    $('export').disabled=false;
    renderResults();renderSources();loadHistory(query.route,version);
    if(!data.candidates.length){$('advice').innerHTML='<p>没有该日可比较的方案，暂不生成推荐。不会用其他日期的车次补齐。</p>';$('model-label').textContent=`${state.meta.model} · 未调用模型`;return;}
    $('advice').innerHTML='<p>候选已算好，正在请模型比较这些方案。你可以先查看每一项来源。</p>';
    $('model-label').textContent=`${state.meta.model} · 正在分析`;
    setBusy(false);
    const advice=await api(`/api/analyze/${data.id}`,{method:'POST',body:'{}'});
    if(version!==state.version)return;
    data.analysis=advice;renderAdvice();renderResults();
  }catch(err){
    if(version!==state.version)return;
    $('error').hidden=false;$('error').textContent=err.message;
    if(!state.data){
      $('results').innerHTML='<div class="empty"><h3>这次没有取到可用资料</h3><p>请检查本机网络后重新查询。不会用示例时刻或虚构报价填补结果。</p></div>';
      $('result-count').textContent='查询未完成';$('query-status').textContent='查询失败；请调整条件或重试。';
    }
    $('advice').innerHTML='<p>分析暂未完成。已取得的参考方案仍可逐项比较。</p>';
    $('model-label').textContent=`${state.meta.model} · 分析未完成`;
  }finally{if(version===state.version)setBusy(false);}
}
function renderResults(){
  const data=state.data;if(!data)return;
  if(!data.candidates.length){
    const messages={outside_presale:['尚未进入查询窗口',`预计 ${data.sale_date} 起可查询这一天，具体以车站起售时间为准。你可以先选择窗口内的日期了解路线。`],source_unavailable:['暂未取得该日资料','来源暂不可用或返回日期不符，请稍后重试。没有结果不代表当天停运，也不会用其他日期替代。'],no_matching_options:['当日资料中没有符合条件的方案','可调整预算、席别或换乘条件后重查。公开页面覆盖有限，没有结果不代表当天没有列车。']};
    const [title,message]=messages[data.data_status]||messages.no_matching_options;
    $('results').innerHTML=`<div class="empty"><h3>${e(title)}</h3><p>${e(message)}</p></div>`;
    $('show-more').hidden=true;return;
  }
  const openIds=[...document.querySelectorAll('.journey-details:not([hidden])')].map(n=>n.id);
  $('results').innerHTML=data.candidates.slice(0,state.limit).map(p=>journey(p)).join('');
  openIds.forEach(id=>{if($(id)){$(id).hidden=false;document.querySelector(`[aria-controls="${id}"]`).setAttribute('aria-expanded','true');}});
  $('show-more').hidden=state.limit>=data.candidates.length;
  $('show-more').textContent=`显示更多方案（还有 ${Math.max(0,data.candidates.length-state.limit)} 个）`;
  renderComparison();
}
function journey(p){
  const first=p.legs[0], last=p.legs.at(-1), gap=dayGap(first.departure,last.arrival), advice=state.data.analysis;
  const recommended=advice?.candidate_id===p.id;
  const seats=[...new Set(p.legs.map(l=>l.seat))].join(' / ');
  const routeNote=p.legs.length===1?'直达':`${p.legs[0].destination}${p.cross_station?' · 异站':''}换乘`;
  return `<article class="journey${recommended?' recommended':''}" id="journey-${p.id}">
    <div class="journey-header"><div class="journey-label"><span>${e(trainNames(p))}</span>${recommended?'<span class="badge">'+(advice.status==='ok'?'Agent 推荐':'规则首选')+'</span>':''}<span>${e(p.source_date.slice(5))} 参考</span></div><label class="check"><input type="checkbox" data-compare="${p.id}" aria-label="对比 ${e(trainNames(p))} ${e(first.origin)}出发方案" ${state.selected.has(p.id)?'checked':''}>对比</label></div>
    <div class="journey-main"><div class="journey-route"><div><div class="station-time">${e(first.departure.slice(11))}</div><div class="station-name">${e(first.origin)}</div></div><div class="route-line">${p.legs.length===1?'无需换乘':'换乘1次'}<div class="line"></div><span>${p.legs.length===1?'直达':e(p.legs[0].destination)}</span></div><div class="arrival"><div class="station-time">${e(last.arrival.slice(11))}${gap?`<sup>+${gap}天</sup>`:''}</div><div class="station-name">${e(last.destination)}</div></div></div>
    <div class="journey-duration"><div class="duration">${duration(p.duration_minutes)}</div><div class="metric-caption">${p.legs.length===1?'站到站全程':`含换乘 ${duration(p.transfer_minutes)}`}</div></div>
    <div class="fare"><div class="fare-value">${p.rail_fare===null?'待核实':`<small>¥</small>${money(p.rail_fare)}`}</div><div class="metric-caption">铁路参考价${p.total_cost_known?'':' · 有额外费用未知'}</div></div></div>
    <div class="journey-bottom"><span>${e(seats)}</span><span class="${p.risk==='较高'?'risk-high':''}">${p.risk==='无换乘'?'无换乘风险项':`换乘风险${p.risk} · 推测`}</span><span>舒适 ${p.comfort_score}/100 · 推测</span><button class="detail-toggle" aria-expanded="false" aria-controls="detail-${p.id}" data-detail="${p.id}">详情与来源</button></div>
    <div class="journey-details" id="detail-${p.id}" hidden>${p.legs.map((l,i)=>`<div class="leg"><div class="leg-number">${e(l.train)}</div><div><strong>${e(l.origin)} → ${e(l.destination)}</strong><p>${dayTime(l.departure)} 出发 · ${dayTime(l.arrival)} 到达 · ${duration(l.duration_minutes)}</p><p>${e(l.seat)} ¥${money(l.fare)} · ${e(l.train_type)} · ${e(l.fleet)}${l.fuxing_reference?' · 来源标注复兴号，具体担当待核实':''}</p></div></div>${i<p.legs.length-1?`<p class="transfer-note">${e(routeNote)} · 等待 ${duration(p.transfer_minutes)}${p.overnight_transfer?' · 涉及凌晨或跨午夜，需确认车站开放和休息条件':''}${p.cross_station?' · 交通费用未知；预留时间不代表已验证地面交通':''}</p>`:''}`).join('')}
    <div class="detail-note"><p>资料读取：${e(dayTime(p.observed_at))}（北京时间） · 真实上座率未知 · 不含市内交通与餐饮住宿。</p><p>${p.source_urls.map(u=>`<a href="${e(u)}" target="_blank" rel="noopener noreferrer">${sourceProvider(u)}公开路线页 · ${e(sourceName(u))} ↗</a>`).join('')}</p><p>换乘风险与舒适度为规则推测。<a href="#evidence">查看计算依据</a></p></div></div></article>`;
}
function renderAdvice(){
  const a=state.data?.analysis;if(!a)return;
  const p=state.data.candidates.find(c=>c.id===a.candidate_id);
  if(!p){$('advice').innerHTML=`<p>${e(a.message)}</p>`;$('model-label').textContent=`${a.model} · 无候选，未调用模型`;return;}
  $('advice').innerHTML=`<span class="advice-state">${a.status==='ok'?'基于公开参考的推荐':'模型暂不可用 · 规则首选'}</span><p class="advice-route">${e(trainNames(p))}</p><p>${duration(p.duration_minutes)} · 铁路参考 ¥${money(p.rail_fare)}</p><ul>${a.reasons.map(r=>`<li>${e(r)}</li>`).join('')}</ul><p>${p.date_matches?'日期相符，仍需官方核对。':`参考日期 ${e(p.source_date)}，需重新确认所选日期。`}</p><button class="advice-link" data-jump="${p.id}">查看这个方案</button>`;
  $('model-label').textContent=`${a.model} · ${a.status==='ok'?'分析完成':'规则排序可用'}`;
}
function renderComparison(){
  const node=$('comparison'), selected=state.data.candidates.filter(p=>state.selected.has(p.id));
  if(selected.length<1){node.hidden=true;return;}
  node.hidden=false;
  const rows=[['站点',p=>`${e(p.legs[0].origin)} → ${e(p.legs.at(-1).destination)}`],['全程时间',p=>duration(p.duration_minutes)],['铁路参考价',p=>`¥${money(p.rail_fare)}<small>${p.total_cost_known?'不含餐饮及市内交通':'另有交通或休息费用未知'}</small>`],['换乘',p=>p.legs.length===1?'直达':`${duration(p.transfer_minutes)} · ${p.risk}`],['预计舒适度',p=>`${p.comfort_score}/100 · 推测`],['真实上座率',()=> '未知'],['参考日期',p=>e(p.source_date)]];
  node.innerHTML=`<section class="compare-panel"><div class="compare-heading"><h3>并排比较 <span class="muted">${selected.length}/3</span></h3><button class="text-button" data-clear>清空对比</button></div>${selected.length===1?'<p class="muted">再勾选一个方案，就能逐项比较。</p>':''}<table><caption class="muted">所选方案的相同口径对照</caption><thead><tr><th scope="col">比较项</th>${selected.map(p=>`<th scope="col">${e(trainNames(p))}</th>`).join('')}</tr></thead><tbody>${rows.map(([label,f])=>`<tr><th scope="row">${label}</th>${selected.map(p=>`<td>${f(p)}</td>`).join('')}</tr>`).join('')}</tbody></table></section>`;
}
function renderSources(){
  const statuses={fetched:'本次读取',cached:'10分钟内缓存',stale:'旧快照',unavailable:'暂不可用',outside_presale:'未到查询窗口'};
  $('source-list').innerHTML=state.data.sources.map(s=>`<li><span class="source-state">${statuses[s.status]}</span><a href="${e(s.url)}" target="_blank" rel="noopener noreferrer">${sourceProvider(s.url)} · ${e(sourceName(s.url))} ↗</a><p>${s.source_date?`参考日期 ${e(s.source_date)} · 读取 ${e(dayTime(s.observed_at))}`:''}${s.message?` ${e(s.message)}`:''}</p></li>`).join('');
}
function renderReferences(items){
  $('reference-list').innerHTML=items.map(r=>`<li><span class="source-state">${e(r.kind)}${r.status==='verified'?' · 本次可读':r.status==='link_only'?' · 本次未能复核':''}</span><a href="${e(r.url)}" target="_blank" rel="noopener noreferrer">${e(r.title)} ↗</a><p>${e(r.note)}</p><p>${r.published?`发布 ${e(r.published)}`:'页面未标发布日期'}${r.checked_at?` · 复核 ${e(dayTime(r.checked_at))}`:''}</p></li>`).join('');
}
async function loadHistory(route,version){
  try{
    const data=await api(`/api/history/${route}`);if(version!==state.version)return;
    $('history-content').innerHTML=data.items.map(item=>{
      const n=item.observations.length, last=item.observations.at(-1);
      return `<div class="history-row"><a href="${e(item.url)}" target="_blank" rel="noopener noreferrer">${e(sourceName(item.url))}</a><span>${n?`${n} 次观察 · 最近 ${e(dayTime(last.observed_at))} · 页面参考 ${e(last.source_date)}`:'尚无可用观察'}${n===1?' · 首次记录，尚不足以判断历史变化':''}</span></div>`;
    }).join('');
  }catch{$('history-content').textContent='记录暂不可读，请稍后重新查询。';}
}
$('search-form').addEventListener('submit',ev=>{ev.preventDefault();search();});
function updateDateHint(){if(state.meta)$('date-hint').textContent=`按出发日期查询 · 当前通常可查 ${state.meta.today} 至 ${state.meta.presale_last_date}，具体以起售时间为准。`; }
$('search-form').addEventListener('change',()=>{updateDateHint();if(state.data)$('query-status').textContent='查询条件已更改。下方仍显示上次结果，点击“查找并比较”更新。';});
document.addEventListener('click',ev=>{
  const sort=ev.target.closest('[data-sort]');if(sort){state.preference=sort.dataset.sort;document.querySelectorAll('[data-sort]').forEach(b=>b.setAttribute('aria-pressed',String(b===sort)));search();}
  const detail=ev.target.closest('[data-detail]');if(detail){const content=$(`detail-${detail.dataset.detail}`);content.hidden=!content.hidden;detail.setAttribute('aria-expanded',String(!content.hidden));}
  if(ev.target.closest('[data-clear]')){state.selected.clear();renderResults();}
  const jump=ev.target.closest('[data-jump]');if(jump){const p=state.data.candidates.findIndex(c=>c.id===jump.dataset.jump);state.limit=Math.max(state.limit,p+1);renderResults();$(`journey-${jump.dataset.jump}`).scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'center'});}
});
document.addEventListener('change',ev=>{
  const id=ev.target.dataset.compare;if(!id)return;
  if(ev.target.checked){if(state.selected.size>=3){ev.target.checked=false;$('query-status').textContent='最多同时比较3个方案。请先取消一个勾选。';return;}state.selected.add(id);}else state.selected.delete(id);
  renderComparison();
});
$('show-more').addEventListener('click',()=>{state.limit+=12;renderResults();});
$('export').addEventListener('click',()=>{
  if(!state.data)return;
  const blob=new Blob([JSON.stringify(state.data,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=`沿线-${state.data.query.route}-${state.data.query.departure_date}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
async function init(){
  try{
    state.meta=await api('/api/meta');$('departure-date').value=state.meta.default_date;$('departure-date').min=state.meta.today;
    $('model-label').textContent=`${state.meta.model} · 独立分析`;
    renderReferences(state.meta.references);updateDateHint();
    search();
    api('/api/references').then(r=>renderReferences(r.items)).catch(()=>{});
  }catch{ $('error').hidden=false;$('error').textContent='无法连接本地服务。请启动 personal_rail/run.py 后刷新页面。';}
}
init();

async function loadOfficialNews(){
  try{
    const data=await api('/api/news');
    const failed=data.sources.filter(s=>s.status!=='ok');
    $('news-status').textContent=`检查于 ${dayTime(data.checked_at)} · 15分钟缓存。${failed.length?`${failed.length}个栏目暂不可读。`:''}近60天公告，按发布日期排列；不代表已确认影响本次行程。`;
    $('news-list').innerHTML=data.items.length?data.items.map(item=>`<li><a href="${e(item.url)}" target="_blank" rel="noopener noreferrer">${e(item.title)} ↗</a><p>${e(item.publisher)} · 发布 ${e(item.published)}</p></li>`).join(''):'<li class="muted">本次未取得近60天的公告，请打开官方栏目查看。没有结果不代表没有运营调整。</li>';
    $('news-links').innerHTML=data.sources.map(s=>`<a href="${e(s.url)}" target="_blank" rel="noopener noreferrer">${e(s.title)}${s.status==='ok'?'':'（暂不可读）'} ↗</a>`).join('');
  }catch{$('news-status').textContent='官方公告暂时读取失败，请稍后刷新页面重试。';}
}
loadOfficialNews();
