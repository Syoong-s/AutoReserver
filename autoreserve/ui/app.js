'use strict';
const token = document.querySelector('meta[name="reserve-token"]').content;
const $ = id => document.getElementById(id);
const labels = {pending:'待监测',submitted:'已完成',preview:'预览可用',rejected:'等待重试',unknown:'需核对订单',submit_intent:'需核对订单',needs_verification:'需人工验证',expired:'日期已过',conflict:'时段重复',needs_attention:'需人工处理',access_limited:'访问受限，已暂停',completed:'全部完成',stopped:'已停止',error:'需要处理'};
let config, runtime, dirty = false, lastVersion = null, toastTimer;
const expanded = new Set();
const builders = new Map();
const extraCourts = new Set();
const hours = Array.from({length:15},(_,i) => `${String(i+7).padStart(2,'0')}:00-${String(i+8).padStart(2,'0')}:00`);

// ==========================================
// Function: Escape external labels before rendering HTML.
// Method: Encode every HTML-significant character.
// ==========================================
function esc(value) { return String(value ?? '').replace(/[&<>"']/g,c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

// ==========================================
// Function: Call the local authenticated dashboard API.
// Method: Attach the per-process token and surface structured errors.
// ==========================================
async function api(path, data) {
  const options = {headers:{'X-Reserve-Token':token}};
  if (data !== undefined) Object.assign(options,{method:'POST',body:JSON.stringify(data),headers:{...options.headers,'Content-Type':'application/json'}});
  const response = await fetch(path,options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `请求失败 (${response.status})`);
  return result;
}

// ==========================================
// Function: Display a local action failure without losing draft edits.
// Method: Use a persistent accessible error panel.
// ==========================================
function error(message) { $('errorBox').textContent=message; $('errorBox').hidden=false; }

// ==========================================
// Function: Briefly confirm a completed user action.
// Method: Replace the previous toast and restart its dismissal timer.
// ==========================================
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,2600); }

// ==========================================
// Function: Mark edits as unsaved.
// Method: Update save hints without resetting the current form.
// ==========================================
function changed() { dirty=true; $('saveTop').textContent='保存配置 · 有修改'; $('saveHint').textContent='开始预约前会自动保存当前配置'; updateStats(); }

// ==========================================
// Function: Generate tomorrow's local calendar date.
// Method: Format local fields rather than converting through UTC.
// ==========================================
function tomorrow() { const d=new Date(); d.setDate(d.getDate()+1); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; }

// ==========================================
// Function: Return the latest durable or live status for one goal.
// Method: Match stable goal IDs from the server response.
// ==========================================
function goalState(id) { return runtime?.goals?.find(g=>g.id===id) || {status:'pending',detail:{}}; }

// ==========================================
// Function: Protect goals with an existing submission record.
// Method: Allow reordering but require new goals for changed booking requirements.
// ==========================================
function recorded(id) { return !['pending','preview'].includes(goalState(id).status); }

// ==========================================
// Function: Return selectable courts and per-goal draft selections.
// Method: Merge live court names with configured and manually entered names.
// ==========================================
function builder(goal) {
  if (!builders.has(goal.id)) builders.set(goal.id,{courts:new Set([goal.candidates[0]?.court || runtime?.snapshot?.columns?.[0] || '场地1']),slots:new Set(['14:00-15:00']),bundle:false});
  return builders.get(goal.id);
}

// ==========================================
// Function: List courts without inventing names absent from user input.
// Method: Merge the latest snapshot, current candidates, and explicit custom entries.
// ==========================================
function courtsFor(goal) { return [...new Set([...(runtime?.snapshot?.columns || []),...goal.candidates.map(t=>t.court),...extraCourts,...builder(goal).courts])]; }

// ==========================================
// Function: Create an independent required date goal.
// Method: Use a new stable ID and open its multi-select editor.
// ==========================================
function addGoal() {
  if (runtime?.busy) return;
  const goal={id:crypto.randomUUID(),name:`预约目标 ${config.goals.length+1}`,candidates:[]};
  goal.draftDate=tomorrow(); config.goals.push(goal); expanded.add(goal.id); changed(); renderGoals();
  document.querySelector(`[data-goal="${goal.id}"]`)?.scrollIntoView({behavior:'smooth',block:'center'});
}

// ==========================================
// Function: Move a goal or alternative up or down in priority.
// Method: Swap array positions while keeping stable goal identities.
// ==========================================
function move(array,index,delta) { const next=index+delta; if(next<0 || next>=array.length) return; [array[index],array[next]]=[array[next],array[index]]; changed(); renderGoals(); }

// ==========================================
// Function: Expand many selected courts and hours into prioritized candidates.
// Method: Use individual alternatives or one contiguous group per court.
// ==========================================
function addCandidates(goal) {
  const draft=builder(goal), date=goal.candidates[0]?.date || goal.draftDate || tomorrow();
  const slots=[...draft.slots].sort();
  if (!draft.courts.size || !slots.length) return error('请至少选择一个场地和一个时段');
  if (draft.bundle && slots.some((s,i)=>i && hours.indexOf(s)!==hours.indexOf(slots[i-1])+1)) return error('合并成同一候选时，所选时段必须连续');
  for(const court of draft.courts) for(const group of draft.bundle ? [slots] : slots.map(s=>[s])) {
    const item={date,court,slots:group};
    if(!goal.candidates.some(t=>t.court===court && JSON.stringify([...t.slots].sort())===JSON.stringify([...group].sort()))) goal.candidates.push(item);
  }
  $('errorBox').hidden=true; changed(); renderGoals(); toast('已添加候选，可用上下箭头调整优先级');
}

// ==========================================
// Function: Render editable goals and their ordered alternatives.
// Method: Build escaped cards, then bind explicit element actions.
// ==========================================
function renderGoals() {
  $('emptyGoals').hidden=!!config.goals.length;
  $('goals').innerHTML=config.goals.map((goal,index)=>{
    const state=goalState(goal.id), locked=recorded(goal.id), date=goal.candidates[0]?.date || goal.draftDate || tomorrow();
    const open=expanded.has(goal.id), draft=builder(goal);
    const candidates=goal.candidates.map((t,i)=>`<div class="candidate-row" data-candidate="${i}"><span class="candidate-number">${String(i+1).padStart(2,'0')}</span><div class="candidate-content"><span class="court-chip">${esc(t.court)}</span><span>${esc(t.slots.join(' / '))}</span>${i===0?'<span class="preferred">优先</span>':''}</div><button class="icon-button candidate-order" data-move="-1" title="提高候选优先级" aria-label="提高候选优先级" ${i===0?'disabled':''}>↑</button><button class="icon-button candidate-order" data-move="1" title="降低候选优先级" aria-label="降低候选优先级" ${i===goal.candidates.length-1?'disabled':''}>↓</button>${!locked?'<button class="icon-button candidate-order" data-remove title="移除候选" aria-label="移除候选">×</button>':''}</div>`).join('');
    return `<article class="goal-card" data-goal="${esc(goal.id)}"><div class="goal-header"><span class="goal-priority">${String(index+1).padStart(2,'0')}</span><div class="goal-title"><strong>${esc(goal.name || '预约目标')}</strong><small>${esc(date)} · ${goal.candidates.length} 个备选</small></div><span class="pill ${state.status==='submitted'?'':state.status==='pending'?'muted':'warning'}" data-status>${esc(labels[state.status]||state.status)}</span><div class="goal-controls"><button class="icon-button" data-goal-up aria-label="提高目标优先级" ${index===0?'disabled':''}>↑</button><button class="icon-button" data-goal-down aria-label="降低目标优先级" ${index===config.goals.length-1?'disabled':''}>↓</button><button class="icon-button" data-edit aria-label="编辑目标">${open?'−':'＋'}</button>${!locked?'<button class="icon-button" data-delete aria-label="删除目标">×</button>':''}</div></div><div class="goal-options">${candidates || '<p class="field-hint">展开下方设置，批量添加场地和时段备选。</p>'}</div>${open?`<div class="editor"><div class="field-row"><div><label class="field-label">目标名称</label><input type="text" data-name value="${esc(goal.name)}" maxlength="120"></div><div><label class="field-label">预约日期</label><input type="date" data-date value="${esc(date)}" ${locked?'disabled':''}></div></div>${!locked?`<label class="field-label">选择场地 <span>可多选</span></label><div class="chip-group courts-group">${courtsFor(goal).map(c=>`<label class="choice-chip"><input type="checkbox" data-court="${esc(c)}" ${draft.courts.has(c)?'checked':''}>${esc(c)}</label>`).join('')}</div><div class="custom-court"><input type="text" data-custom-court placeholder="输入其他场地的完整名称" aria-label="其他场地名称"><button class="button secondary compact" data-add-court>＋ 添加</button></div><label class="field-label">选择时段 <span>可多选</span></label><div class="chip-group slots-group">${hours.map(s=>`<label class="choice-chip"><input type="checkbox" data-slot="${s}" ${draft.slots.has(s)?'checked':''}>${s}</label>`).join('')}</div><div class="builder-footer"><label class="inline-checkbox"><input type="checkbox" data-bundle ${draft.bundle?'checked':''}>连续时段合并为同一候选</label><button class="button primary compact" data-add-candidates>＋ 添加候选组合</button></div>`:'<p class="field-hint">此目标已有提交记录。可调整优先级；预约其他日期或时段请添加新目标。</p>'}</div>`:''}<div class="goal-status-line ${state.status==='submitted'?'complete':''}" data-status-line>${statusText(state)}</div></article>`;
  }).join('');
  for(const article of $('goals').children) {
    const goal=config.goals.find(g=>g.id===article.dataset.goal), index=config.goals.indexOf(goal), draft=builder(goal);
    article.querySelector('[data-goal-up]').onclick=()=>move(config.goals,index,-1);
    article.querySelector('[data-goal-down]').onclick=()=>move(config.goals,index,1);
    article.querySelector('[data-edit]').onclick=()=>{expanded.has(goal.id)?expanded.delete(goal.id):expanded.add(goal.id);renderGoals();};
    const del=article.querySelector('[data-delete]'); if(del) del.onclick=()=>{config.goals.splice(index,1);changed();renderGoals();};
    for(const row of article.querySelectorAll('[data-candidate]')) {
      const i=Number(row.dataset.candidate);
      row.querySelectorAll('[data-move]').forEach(b=>b.onclick=()=>move(goal.candidates,i,Number(b.dataset.move)));
      const remove=row.querySelector('[data-remove]'); if(remove) remove.onclick=()=>{goal.candidates.splice(i,1);changed();renderGoals();};
    }
    const name=article.querySelector('[data-name]'); if(name) name.onchange=()=>{goal.name=name.value;changed();renderGoals();};
    const date=article.querySelector('[data-date]'); if(date) date.onchange=()=>{goal.draftDate=date.value;goal.candidates.forEach(t=>t.date=date.value);changed();renderGoals();};
    article.querySelectorAll('[data-court]').forEach(c=>c.onchange=()=>c.checked?draft.courts.add(c.dataset.court):draft.courts.delete(c.dataset.court));
    article.querySelectorAll('[data-slot]').forEach(c=>c.onchange=()=>c.checked?draft.slots.add(c.dataset.slot):draft.slots.delete(c.dataset.slot));
    const bundle=article.querySelector('[data-bundle]'); if(bundle) bundle.onchange=()=>draft.bundle=bundle.checked;
    const add=article.querySelector('[data-add-candidates]'); if(add) add.onclick=()=>addCandidates(goal);
    const court=article.querySelector('[data-add-court]'); if(court) court.onclick=()=>{const name=article.querySelector('[data-custom-court]').value.trim();if(name){extraCourts.add(name);draft.courts.add(name);renderGoals();}};
  }
  updateStats(); applyBusy();
}

// ==========================================
// Function: Describe completion without equating preview with an actual order.
// Method: Prefer durable order ID or actionable error details.
// ==========================================
function statusText(state) {
  if(state.status==='submitted') return `✓ 已提交订单 ${esc(state.detail.order_id || '')} · 未执行付款`;
  if(state.status==='preview') return '◎ 已找到可用候选 · 预览没有提交订单';
  if(state.status==='pending' && state.last_checked_at) {
    const active=runtime?.busy && runtime.operation==='run';
    const time=new Date(state.last_checked_at).toLocaleTimeString('zh-CN',{hour12:false});
    return `◉ ${active?'持续监测中':'监测已暂停'} · 最近检查 ${esc(time)} · 共 ${state.check_count || 0} 次`;
  }
  if(state.status==='pending' && runtime?.busy && runtime.operation==='run') return '◉ 已加入共同监测，等待全局读取时机';
  if(state.detail?.message) return esc(state.detail.message);
  return '↗ 按上方备选顺序预约，成功一个即完成此目标';
}

// ==========================================
// Function: Update plan summary counters.
// Method: Count only submitted goal records as actual completion.
// ==========================================
function updateStats() {
  if(!config) return;
  const done=config.goals.filter(g=>goalState(g.id).status==='submitted').length;
  $('totalCount').innerHTML=`${config.goals.length}<span> 个</span>`;
  $('doneCount').innerHTML=`${done}<span> / ${config.goals.length}</span>`;
  $('candidateCount').innerHTML=`${config.goals.reduce((n,g)=>n+g.candidates.length,0)}<span> 组</span>`;
  $('completionHint').textContent=done===config.goals.length && done?'全部目标已完成':done?'继续处理剩余目标':'等待开始预约';
}

// ==========================================
// Function: Populate settings from the saved configuration.
// Method: Present startup times explicitly in UTC+8.
// ==========================================
function renderSettings() {
  $('startAt').value=config.start_at?new Date(new Date(config.start_at).getTime()+8*3600000).toISOString().slice(0,19):'';
  $('pollInterval').value=config.poll_interval_seconds;
  $('jitterPercent').value=Math.round((config.poll_jitter_ratio ?? 0.2)*100);
  $('maxBackoff').value=config.backoff_max_seconds ?? 600;
  $('maxPrice').value=config.max_total_price;
  $('untilComplete').checked=config.monitor_until_complete;
  $('maxPoll').value=config.max_poll_seconds;
  $('timeLimitField').hidden=config.monitor_until_complete;
  $('showBrowser').checked=!config.headless;
}

// ==========================================
// Function: Build a strict serializable config from the visible form.
// Method: Strip draft-only fields and retain existing browser/session settings.
// ==========================================
function formConfig() {
  return {...config,targets:[],goals:config.goals.map(g=>({id:g.id,name:g.name,candidates:g.candidates})),
    start_at:$('startAt').value?$('startAt').value+'+08:00':null,
    timezone:'Asia/Shanghai',poll_interval_seconds:Number($('pollInterval').value),
    poll_jitter_ratio:Number($('jitterPercent').value)/100,backoff_max_seconds:Number($('maxBackoff').value),
    max_poll_seconds:Number($('maxPoll').value),max_total_price:String($('maxPrice').value),
    monitor_until_complete:$('untilComplete').checked,headless:!$('showBrowser').checked};
}

// ==========================================
// Function: Persist all visible settings before any booking action.
// Method: Validate on the server and retain draft edits if saving fails.
// ==========================================
async function save() {
  const data=formConfig(); await api('/api/config',data); config=data;dirty=false;
  $('saveTop').textContent='保存配置 ↗';$('saveHint').textContent='配置已保存，按你的优先级执行';$('errorBox').hidden=true;
}

// ==========================================
// Function: Start a graphical operation after saving its configuration.
// Method: Send an explicit action, never auto-submit on page load.
// ==========================================
async function action(name) {
  try {
    $('errorBox').hidden=true;
    if(['run','preview'].includes(name)) await save();
    if(name==='login' && dirty) await save();
    await api('/api/action',{action:name,date:$('scanDate').value || null});
    await refresh();
  } catch(e) { error(e.message); }
}

// ==========================================
// Function: Render the latest read-only availability snapshot.
// Method: Show original courts and hourly states with a compact accessible table.
// ==========================================
function renderAvailability(shot) {
  if(!shot) return;
  if(!$('scanDate').value) $('scanDate').value=shot.active_date;
  if(!shot.columns?.length) { $('availability').textContent='该日期暂无可预约场地';return; }
  $('availability').className='';
  $('availability').innerHTML=`<table class="availability-table"><caption>${esc(shot.active_date)} 的最近空位</caption><thead><tr><th>时段</th>${shot.columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${shot.rows.map((row,i)=>`<tr><th>${esc(shot.times[i])}</th>${row.map(s=>`<td class="${esc(s)}" title="${s==='available'?'可预约':s==='selected'?'已选':'不可预约'}">${s==='available'?'●':s==='selected'?'✓':'—'}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

// ==========================================
// Function: Keep editing and concurrent actions consistent with worker ownership.
// Method: Disable mutation controls during browser actions, leaving stop available.
// ==========================================
function applyBusy() {
  const busy=!!runtime?.busy;
  for(const id of ['addGoal','emptyAdd','saveTop','loginTop','preview','start','refreshSlots','startAt','pollInterval','jitterPercent','maxBackoff','maxPrice','untilComplete','maxPoll','showBrowser','scanDate']) $(id).disabled=busy;
  $('goals').querySelectorAll('button,input').forEach(e=>{if(busy){if(!e.disabled)e.dataset.busyDisabled='1';e.disabled=true;}else if(e.dataset.busyDisabled){e.disabled=false;delete e.dataset.busyDisabled;}});
  $('captureLogin').hidden=!(busy && runtime.operation==='login');$('stop').hidden=!busy;
  $('start').hidden=busy;$('preview').hidden=busy;
  $('activityTag').textContent=busy?'运行中':runtime?.result?labels[runtime.result.status]||runtime.result.status:'待命';
  $('activityTag').className='pill'+(busy?'':' muted');
}

// ==========================================
// Function: Poll status without replacing unsaved fields or moving keyboard focus.
// Method: Re-render config only on clean external updates, update status separately.
// ==========================================
async function refresh() {
  const state=await api('/api/state'); const previousBusy=runtime?.busy; runtime=state;
  if(!config || (!dirty && lastVersion!==state.config_version)) {
    config=state.config;lastVersion=state.config_version;
    if(!expanded.size && config.goals[0] && !config.venue) expanded.add(config.goals[0].id);
    renderSettings();renderGoals();
  }
  $('venueName').textContent=config.venue || '上海交通大学体育场馆';
  $('venueHint').textContent=config.venue?`${config.sport} · 登录和场馆配置保存在本机`:'首次使用请先登录，进入具体场馆后保存。';
  $('loginStatus').textContent=state.logged_in?'已保存登录':'尚未登录';$('loginStatus').className='pill'+(state.logged_in?'':' muted');
  $('runMessage').textContent=state.message;
  $('logs').innerHTML=state.logs.length?state.logs.slice(-60).reverse().map(l=>`<div class="log-entry"><time>${esc(new Date(l.time).toLocaleTimeString('zh-CN',{hour12:false}))}</time><span>${esc(l.message)}</span></div>`).join(''):'<p class="log-empty">执行记录会显示在这里</p>';
  for(const article of $('goals').children) {
    const s=goalState(article.dataset.goal),badge=article.querySelector('[data-status]'),line=article.querySelector('[data-status-line]');
    const watching=state.busy && state.operation==='run' && s.status==='pending';
    badge.textContent=watching?(s.watch_state==='checking'?'检查中':'监测中'):(labels[s.status]||s.status);badge.className='pill'+(s.status==='pending' && !watching?' muted':s.status==='submitted'||watching?'':' warning');
    line.innerHTML=statusText(s);line.className='goal-status-line'+(s.status==='submitted'?' complete':'');
  }
  const monitor=state.monitor || {};
  if(state.busy && state.operation==='run' && monitor.next_read_at) {
    const seconds=Math.max(0,Math.ceil(monitor.next_read_at-Date.now()/1000));
    $('monitorHint').textContent=monitor.access_denied?'网站拒绝访问，正在暂停监测':monitor.cooling_down?`${monitor.reason} · 剩余约 ${seconds} 秒`:`全部目标共享访问节奏 · 下次读取约 ${seconds} 秒后 · 已读取 ${monitor.reads || 0} 次`;
  } else $('monitorHint').textContent=`全局基础间隔 ${config.poll_interval_seconds} 秒，额外随机延迟 0–${Math.round((config.poll_jitter_ratio ?? 0.2)*100)}%；异常自动退避。`;
  renderAvailability(state.snapshot); updateStats();applyBusy();
  if(previousBusy && !state.busy) { renderGoals(); if(state.result?.status==='error') error(state.result.message); }
}

// ==========================================
// Function: Show results for all required goals.
// Method: Include the exact selected alternative and persisted order identifier.
// ==========================================
function showResults() {
  $('resultsContent').innerHTML=config.goals.map(g=>{const s=goalState(g.id),d=s.detail;return `<div class="result-item"><strong>${esc(g.name)} · ${esc(labels[s.status]||s.status)}</strong><p>${esc(d.date||g.candidates[0]?.date||'')} ${esc(d.court||'')} ${esc((d.slots||[]).join(' / '))}</p>${d.order_id?`<code>订单号：${esc(d.order_id)}</code>`:''}<p>${esc(d.message||'')}</p></div>`;}).join('')||'<p>还没有预约目标。</p>';
  $('resultsDialog').showModal();
}

$('addGoal').onclick=addGoal;$('emptyAdd').onclick=addGoal;
$('saveTop').onclick=()=>save().then(()=>{toast('配置已保存');refresh();}).catch(e=>error(e.message));
$('loginTop').onclick=()=>action('login');$('captureLogin').onclick=()=>action('capture');
$('preview').onclick=()=>action('preview');$('start').onclick=()=>action('run');$('stop').onclick=()=>action('stop');
$('refreshSlots').onclick=()=>action('scan');$('historyNav').onclick=showResults;$('closeResults').onclick=()=>$('resultsDialog').close();
for(const id of ['startAt','pollInterval','jitterPercent','maxBackoff','maxPrice','untilComplete','maxPoll','showBrowser']) $(id).onchange=()=>{changed();$('timeLimitField').hidden=$('untilComplete').checked;};
refresh().catch(e=>error(e.message));
setInterval(()=>refresh().catch(e=>error('连接本机服务失败：'+e.message)),1200);
