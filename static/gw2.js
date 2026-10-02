/* Groundwork 2 — five literal sections, hard gates, findings dropdowns, quotes & cost
   editors, CRE, visuals, guided intake, versions (V1/V2/V3), director dashboard, admin. */
let ME=null,SCHEMA=null,CAN_WRITE=true,PVTAB=null,PERSONAS=null;
const SEC=[['current_situation','Current Situation'],['proposal','Proposal'],['cost','Cost'],['justification','Justification'],['alternatives','Alternatives']];
const money=v=>'$'+Number(v||0).toLocaleString(undefined,{maximumFractionDigits:2});
const num=v=>parseFloat(String(v??'').replace(/[$,]/g,''))||0;
const col=v=>v>=TH?'var(--go)':v>=55?'var(--caution)':'var(--stop)';
const roleRank={analyst:1,director:2,leader:3,finance_admin:4,admin:5};

/* ---------- boot & chrome ---------- */
async function boot(){
  ME=await api('/api/me');SCHEMA=await api('/api/schema');TH=SCHEMA.threshold;
  window.LLM_READY=SCHEMA.llm_ready;window.SCEN_LIST=SCHEMA.demo_scenarios;
  window.SCEN=Object.fromEntries(SCHEMA.demo_scenarios.map(x=>[x.key,x]));
  if(ME.can_preview&&!PERSONAS){try{PERSONAS=await api('/api/preview/personas');}catch(e){PERSONAS={v2:[],v3:[]};}}
  if(ME.preview&&!PVTAB)PVTAB=(PERSONAS&&PERSONAS.v2.some(p=>p.id===ME.acting.id))?'v2':'v3';
  renderChrome();enginePill();renderGuide('voice');renderGuide('write');loadLibrary();
  const r=ME.acting.role;
  if(ME.preview&&roleRank[r]>=2&&r!=='admin'){loadDash();go('dash');}
  else if(ME.preview&&r==='admin'){loadAdmin('users');go('admin');}
  else{await loadList();go('home');}
}
function enginePill(){$('enginePill').textContent=SCHEMA.llm_ready?'model connected':'rules mode';
  $('enginePill').className='pill'+(SCHEMA.llm_ready?' live':'');$('logoutPill').style.display='';}
function renderChrome(){
  const live=ME.edition,tab=ME.preview?PVTAB:live;
  const T=[['v1','V1','Analyst workspace'],['v2','V2','Director & admin'],['v3','V3','Divisions & SSO']];
  $('vtabs').innerHTML=T.map(([k,a,b])=>`<button class="${k===tab?'on':''}" onclick="switchTab('${k}')" title="${b}">${a}<small>${b}</small></button>`).join('');
  $('settingsPill').style.display=ME.is_admin&&!ME.preview?'':'none';
  const nav=[];
  nav.push(`<button onclick="loadList();go('home')">Requests</button>`);
  if(ME.can_dashboard)nav.push(`<button onclick="loadDash();go('dash')">Floor view</button>`);
  if(ME.is_admin&&(ME.preview||live!=='v1'))nav.push(`<button onclick="loadAdmin('users');go('admin')">Admin</button>`);
  if(ME.preview){
    const list=(PERSONAS&&PERSONAS[PVTAB])||[];
    $('previewBar').innerHTML=`<div class="pv-bar"><b>PREVIEW · ${PVTAB.toUpperCase()}</b><span>Acting as</span>
      <select onchange="enterPreview(this.value)">${list.map(p=>`<option value="${p.id}" ${p.id===ME.acting.id?'selected':''}>${esc(p.name)}</option>`).join('')}</select>
      <span>${esc(ME.acting.role_label)} · sample data only</span><span class="grow"></span>${nav.join('')}<button onclick="switchTab('v1')">Exit preview</button></div>`;
  }else if(live!=='v1'&&nav.length>1){
    $('previewBar').innerHTML=`<div class="pv-bar" style="background:#1E2E4B"><b>${live.toUpperCase()}</b><span>${esc(ME.user.name)} · ${esc(ME.user.role_label)}</span><span class="grow"></span>${nav.join('')}</div>`;
  }else $('previewBar').innerHTML='';
}
async function switchTab(t){
  if(t==='v1'){if(ME.preview)await api('/api/preview/exit',{method:'POST'});PVTAB=null;return boot();}
  if(t===ME.edition||(ME.edition==='v3'&&t==='v2')){if(ME.preview){await api('/api/preview/exit',{method:'POST'});PVTAB=null;}await boot();
    if(ME.can_dashboard){loadDash();go('dash');}return;}
  if(!ME.can_preview){toast('Only administrators can preview other versions');return;}
  PVTAB=t;await enterPreview(t==='v2'?'pv_dir':'pv_lead');
}
async function enterPreview(pid){toast('Loading preview…');await api('/api/preview/enter',{method:'POST',body:JSON.stringify({persona:pid})});
  PVTAB=PVTAB||((PERSONAS&&PERSONAS.v2.some(p=>p.id===pid))?'v2':'v3');await boot();}

function go(v){['home','new','method','voice','write','processing','review','qual','coach','result','handoff','dash','admin']
  .forEach(id=>$('view-'+id)&&$('view-'+id).classList.toggle('active',id===v));
  window.scrollTo({top:0,behavior:'smooth'});
  const st={review:1,qual:2,coach:3,result:3,handoff:4}[v];if(st!==undefined)renderRail(v,st);}
const label=s=>({draft:'Draft',need:'Needs information',review:'Almost there',ready:'Ready',handoff:'Sent to Levelpath',
  escalated:'Finance alerted',blocked:'Blocked: required items'}[s]||'Draft');

/* ---------- home ---------- */
async function loadList(){
  const {items}=await api('/api/requests');
  $('reqCount').textContent=items.length+' total';
  const sc=items.filter(i=>i.score>0);
  $('mDraft').textContent=items.filter(i=>!['ready','handoff'].includes(i.status)).length;
  $('mReady').textContent=items.filter(i=>['ready','handoff'].includes(i.status)).length;
  $('mAvg').textContent=sc.length?Math.round(sc.reduce((a,b)=>a+b.score,0)/sc.length):'—';
  const mine=ME.acting.id;
  $('requestList').innerHTML=items.length?items.map(r=>`
    <button class="strip s-${r.status}" onclick="openReq('${r.id}')"><span class="spine"></span><span class="strip-body">
      <span class="strip-top"><span class="callsign">${r.id}</span><span class="dept">${esc(r.department||'')}${r.owner&&r.owner!==mine&&r.owner_name?' · '+esc(r.owner_name):''}</span></span>
      <span class="strip-title">${esc(r.title||'Untitled')}</span>
      <span class="strip-cells"><span class="cell"><span class="k">Score</span><span class="v">${r.score?r.score+' / 100':'—'}</span></span>
        <span class="cell"><span class="k">Quotes</span><span class="v">${r.quotes} of 3</span></span>
        <span class="cell"><span class="k">CRE</span><span class="v">${r.cre==='yes'?'Yes':r.cre==='no'?'No':'Not declared'}</span></span>
        <span class="cell"><span class="k">Updated</span><span class="v">${new Date(r.updated*1000).toLocaleDateString()}</span></span></span></span>
      <span class="strip-right"><span class="chip ${r.status}"><span class="led"></span>${label(r.status)}</span></span></button>`).join('')
    :'<p class="hint">No requests yet. Start one above, import a Word draft, or run the guided demo.</p>';
}
async function loadLibrary(){try{const {items}=await api('/api/training');
  $('libList').innerHTML=items.map(t=>`<button class="lib-card" onclick="openTraining('${t.key}')"><span class="lib-tier ${t.key}">${t.label}</span>
    <span><b>${esc(t.title)}</b><br><span class="hint">Scores ${t.score}/100 · ${t.gates.filter(g=>!g.passed).map(g=>g.label).join(', ')||'all required items met'}</span></span><span class="hint">Open →</span></button>`).join('');}catch(e){}}
async function openTraining(k){state=await api('/api/training/'+k);CAN_WRITE=false;state._training=k;renderQual(state.qualification);go('qual');}
async function importDocx(inp){const f=inp.files[0];if(!f)return;inp.value='';
  go('processing');animateProc();
  const data=await new Promise(r=>{const fr=new FileReader();fr.onload=()=>r(fr.result.split(',')[1]);fr.readAsDataURL(f);});
  try{state=await api('/api/import',{method:'POST',body:JSON.stringify({data,filename:f.name})});CAN_WRITE=true;
    populateReview();renderQual(state.qualification);go('qual');loadList();toast('Imported and scored');}
  catch(e){toast(e.message);go('home');}}
async function openReq(id){state=await api('/api/requests/'+id);CAN_WRITE=state._can_write!==false;populateReview();
  const last=lastRound();
  if(last&&!last.answered&&CAN_WRITE){renderCoach();go('coach');}
  else if(last&&CAN_WRITE){renderResult();go('result');}
  else if(state.qualification){renderQual(state.qualification);go('qual');}
  else go('review');}
function startNew(){state={quotes:[],cost_items:[],exhibits:[],flow_steps:[]};CAN_WRITE=true;
  ['reqTitle','writeInput','transcript','reqFinance','reqRequester'].forEach(i=>$(i)&&($(i).value=''));
  document.querySelectorAll('.type-opt').forEach(o=>o.classList.remove('sel'));window._gi={};renderGuide('voice');renderGuide('write');go('new');}

/* ---------- guided intake ---------- */
window._gi={};
function renderGuide(w){const el=$('guide-'+w);if(!el||!SCHEMA)return;const G=SCHEMA.guide,i=window._gi[w]||0;
  el.innerHTML=`<div class="card pad guide"><div class="g-top"><span class="g-step">GUIDING QUESTION ${i+1} OF ${G.length}</span>
    <span class="g-dots">${G.map((_,k)=>`<span class="${k<i?'done':k===i?'on':''}"></span>`).join('')}</span></div>
    <div class="g-q">${esc(G[i])}</div><div class="btn-row" style="margin:0">
    <button class="btn sm ghost" ${i===0?'disabled':''} onclick="guideStep('${w}',-1)">Back</button>
    <button class="btn sm" ${i===G.length-1?'disabled':''} onclick="guideStep('${w}',1)">Next question</button></div>
    <details><summary class="hint" style="cursor:pointer;margin-top:10px">See all ${G.length} questions</summary><ol class="g-all">${G.map(q=>`<li>${esc(q)}</li>`).join('')}</ol></details></div>`;}
function guideStep(w,d){const G=SCHEMA.guide;window._gi[w]=Math.max(0,Math.min(G.length-1,(window._gi[w]||0)+d));
  if(w==='write'&&d>0){const t=$('writeInput');if(t.value.trim()&&!/\n\n$/.test(t.value))t.value=t.value.trimEnd()+'\n\n';t.focus();}
  renderGuide(w);}

/* ---------- review: five sections + structured editors ---------- */
function populateReview(){
  state.quotes=state.quotes||[];state.cost_items=state.cost_items||[];state.exhibits=state.exhibits||[];state.flow_steps=state.flow_steps||[];
  $('f_title').value=state.title||'';$('reqTitle').value=state.title||'';$('reqDept').value=state.department||'';
  $('reqRequester').value=state.requester||'';$('reqFinance').value=state.finance_contact_email||'';
  $('origQuote').textContent=state.original_description?'“'+state.original_description+'”':'—';
  FIELDS.forEach(f=>{if($('f_'+f))$('f_'+f).value=state[f]??'';});
  $('f_cre_template').value=state.cre_template||'';
  renderQuotes();renderCost();renderCre();renderFlow();renderExhibits();reqBanner();
  const mi=state.missing_information||[];$('unknownsList').innerHTML=mi.map(m=>`<li><span class="dot"></span>${esc(m)}</li>`).join('');
  $('unknownsBox').style.display=mi.length?'block':'none';
  $('structEngine').textContent=state.engine?'Drafted by: '+state.engine+(state.engine_error?' — '+state.engine_error:''):'';
  roBanner('review');document.querySelectorAll('#view-review input,#view-review textarea,#view-review select,#view-review .edcard button')
    .forEach(e=>e.disabled=!CAN_WRITE);$('reviewActions').style.display=CAN_WRITE?'':'none';demoBar('review');}
function roBanner(w){const el=$('roBanner-'+w);if(!el)return;
  el.innerHTML=state._training?`<div class="ro-banner">Training example (${esc(state._training)}) — read-only. <a href="/api/training/${state._training}?download=1">Download the original Word file</a></div>`
   :!CAN_WRITE?`<div class="ro-banner">Read-only for your role${state.owner_name?' — owned by '+esc(state.owner_name):''}. You can leave a review note on the score screen.</div>`:'';}
function collect(){const o={id:state.id,title:val('f_title')||val('reqTitle')||state.title,request_type:state.request_type||'',
  department:val('reqDept')||state.department,requester:val('reqRequester')||state.requester,finance_contact_email:val('reqFinance')||state.finance_contact_email};
  FIELDS.forEach(f=>{if($('f_'+f))o[f]=$('f_'+f).value.trim();});
  Object.assign(o,{quotes:state.quotes,cost_items:state.cost_items,cre_driven:state.cre_driven||'',cre_template:$('f_cre_template').value,
    flow_steps:state.flow_steps.filter(s=>(s.name||'').trim()),exhibits:state.exhibits});return o;}
async function saveDraft(){state=await api('/api/requests',{method:'POST',body:JSON.stringify(collect())});toast('Draft saved · '+state.id);populateReview();loadList();}
async function ensureSaved(){if(!state.id)state=await api('/api/requests',{method:'POST',body:JSON.stringify(collect())});else
  state=await api('/api/requests',{method:'POST',body:JSON.stringify(collect())});}
function reqBanner(){const n=(state.quotes||[]).filter(q=>(q.vendor||'').trim()&&qTotal(q)>0).length;
  $('quoteCount').textContent=n>=3?`${n} quotes ✓`:`${n} of 3 required`;$('quoteCount').className='req-pill'+(n>=3?' ok':'');
  const items=[];if(n<3)items.push(['fail','Three vendor quotes',`${n} on file. Fewer than three is an automatic fail.`]);
  if(!state.cre_driven)items.push(['req','CRE-driven?','Declare yes or no below. CRE-driven requests need the CRE template.']);
  else if(state.cre_driven==='yes'&&!$('f_cre_template').value)items.push(['req','CRE template','Request the CRE template and record it below.']);
  $('reqBanner').innerHTML=items.map(([c,t,m])=>`<div class="gate ${c}"><span class="gi">${c==='fail'?'!':'?'}</span><div><b>${t}${c==='fail'?'<span class="tag">automatic fail</span>':''}</b><span>${m}</span></div></div>`).join('');}

/* quotes */
const qTotal=q=>{const it=(q.items||[]).filter(i=>(i.desc||'').trim()&&num(i.unit)>0);return it.length?it.reduce((a,i)=>a+(num(i.qty)||1)*num(i.unit),0):num(q.total);};
function renderQuotes(){$('quotesEd').innerHTML=(state.quotes||[]).map((q,i)=>`<div class="qcard-ed ${q.selected?'sel':''}">
  <div class="qe-top"><div><label>Vendor</label><input value="${esc(q.vendor||'')}" oninput="qSet(${i},'vendor',this.value)"></div>
    <div><label>Quote date</label><input value="${esc(q.date||'')}" placeholder="2026-03-14" oninput="qSet(${i},'date',this.value)"></div>
    <div><label>Lead time</label><input value="${esc(q.lead_time||'')}" oninput="qSet(${i},'lead_time',this.value)"></div>
    <div><label>Warranty</label><input value="${esc(q.warranty||'')}" oninput="qSet(${i},'warranty',this.value)"></div>
    <button class="xbtn" title="Remove quote" onclick="rmQuote(${i})">×</button></div>
  ${(q.items||[]).map((it,j)=>`<div class="li"><input placeholder="Line item" value="${esc(it.desc||'')}" oninput="qiSet(${i},${j},'desc',this.value)">
    <input placeholder="Qty" value="${esc(it.qty??1)}" oninput="qiSet(${i},${j},'qty',this.value)">
    <input placeholder="Unit $" value="${esc(it.unit??'')}" oninput="qiSet(${i},${j},'unit',this.value)">
    <span class="amt" id="qa_${i}_${j}">${money((num(it.qty)||1)*num(it.unit))}</span><button class="xbtn" onclick="rmQLine(${i},${j})">×</button></div>`).join('')}
  <div class="qe-foot"><span><button class="btn sm ghost" onclick="addQLine(${i})">+ Line item</button>
    ${(q.items||[]).length<2?'<span class="hint" style="margin-left:8px">Itemize: at least two lines</span>':''}</span>
    <label class="chk"><input type="checkbox" ${q.selected?'checked':''} onchange="selQuote(${i},this.checked)">Selected vendor</label>
    <span class="tot" id="qt_${i}">${money(qTotal(q))}</span></div></div>`).join('')||'<p class="hint">No quotes yet.</p>';reqBannerSafe();}
function reqBannerSafe(){if($('f_cre_template'))reqBanner();}
function qSet(i,k,v){state.quotes[i][k]=v;if(k==='vendor')reqBannerSafe();}
function qiSet(i,j,k,v){state.quotes[i].items[j][k]=v;const it=state.quotes[i].items[j];
  $('qa_'+i+'_'+j).textContent=money((num(it.qty)||1)*num(it.unit));$('qt_'+i).textContent=money(qTotal(state.quotes[i]));reqBannerSafe();}
function addQuote(){state.quotes.push({vendor:'',date:'',lead_time:'',warranty:'',items:[{desc:'',qty:1,unit:''},{desc:'',qty:1,unit:''}],selected:false});renderQuotes();}
function rmQuote(i){state.quotes.splice(i,1);renderQuotes();}
function addQLine(i){(state.quotes[i].items=state.quotes[i].items||[]).push({desc:'',qty:1,unit:''});renderQuotes();}
function rmQLine(i,j){state.quotes[i].items.splice(j,1);renderQuotes();}
function selQuote(i,on){state.quotes.forEach((q,k)=>q.selected=on&&k===i);renderQuotes();}
/* cost */
function renderCost(){$('costEd').innerHTML=(state.cost_items||[]).map((it,j)=>`<div class="li"><input placeholder="Description" value="${esc(it.desc||'')}" oninput="ciSet(${j},'desc',this.value)">
  <input placeholder="Qty" value="${esc(it.qty??1)}" oninput="ciSet(${j},'qty',this.value)"><input placeholder="Unit $" value="${esc(it.unit??'')}" oninput="ciSet(${j},'unit',this.value)">
  <span class="amt" id="ca_${j}">${money((num(it.qty)||1)*num(it.unit))}</span><button class="xbtn" onclick="rmCost(${j})">×</button></div>`).join('')||'<p class="hint">No line items yet.</p>';costTotals();}
function ciSet(j,k,v){state.cost_items[j][k]=v;const it=state.cost_items[j];$('ca_'+j).textContent=money((num(it.qty)||1)*num(it.unit));costTotals();}
function addCostLine(){state.cost_items.push({desc:'',qty:1,unit:''});renderCost();}
function rmCost(j){state.cost_items.splice(j,1);renderCost();}
function copySelectedQuote(){const q=(state.quotes||[]).find(x=>x.selected);if(!q){toast('Mark a quote as selected first');return;}
  state.cost_items=JSON.parse(JSON.stringify(q.items||[]));renderCost();toast('Copied '+state.cost_items.length+' lines from '+q.vendor);}
function costTotals(){const sub=(state.cost_items||[]).reduce((a,i)=>a+(num(i.qty)||1)*num(i.unit),0),pct=num($('f_contingency_pct')&&$('f_contingency_pct').value);
  $('costTotal').innerHTML=sub?`Subtotal ${money(sub)}${pct?` · Contingency ${pct}% ${money(sub*pct/100)}`:''} · <b>Total ${money(sub*(1+pct/100))}</b>`:'';}
/* CRE */
function setCre(v){state.cre_driven=v;renderCre();}
function renderCre(){document.querySelectorAll('#creSeg button').forEach(b=>b.classList.toggle('on',b.dataset.v===state.cre_driven));
  $('creTpl').style.display=state.cre_driven==='yes'?'block':'none';
  const h=(state.qualification&&state.qualification.cre&&state.qualification.cre.hint)||'';
  $('creHint').textContent=!state.cre_driven&&h?(h==='explicit'?'The draft mentions Corporate Real Estate.':'This looks like facility or building work.')+' Please confirm.':'';reqBannerSafe();}
/* flow */
function renderFlow(){if(!state.flow_steps.length)state.flow_steps=[{name:''},{name:''},{name:''}];
  $('flowEd').innerHTML=state.flow_steps.map((s,i)=>`<div class="step-row"><span class="n">${String(i+1).padStart(2,'0')}</span>
    <input placeholder="Step" value="${esc(s.name||'')}" oninput="state.flow_steps[${i}].name=this.value">
    <input placeholder="Time (e.g. 45 min)" value="${esc(s.duration||'')}" oninput="state.flow_steps[${i}].duration=this.value">
    <input class="vol" placeholder="Volume" value="${esc(s.volume||'')}" oninput="state.flow_steps[${i}].volume=this.value">
    <label class="chk"><input type="checkbox" ${s.bottleneck?'checked':''} onchange="state.flow_steps[${i}].bottleneck=this.checked">Bottleneck</label>
    <button class="xbtn" onclick="state.flow_steps.splice(${i},1);renderFlow()">×</button></div>`).join('');}
function addStep(){state.flow_steps.push({name:''});renderFlow();}
async function previewFlow(){if(state.flow_steps.filter(s=>(s.name||'').trim()).length<2){toast('Add at least two steps');return;}
  await ensureSaved();$('flowPreview').innerHTML=`<img alt="Process flow" src="/api/flow.png?id=${state.id}&t=${Date.now()}">`;}
/* exhibits & images */
function renderExhibits(){const ex=state.exhibits||[];
  $('exhibitList').innerHTML=ex.length?ex.map(e=>`<div class="ex-item">${e.image_id?`<img src="/api/images/${e.image_id}" alt="">`:'<span></span>'}
    <div><div class="t"><span class="ex-kind ${e.kind}">${esc(e.kind)}</span>${esc(e.label)} — ${esc(e.title)}</div>
    <div class="c">${esc(e.citation||'')}${e.date?' · '+esc(e.date):''}</div></div>
    ${e.image_id&&CAN_WRITE?`<button class="xbtn" title="Remove" onclick="rmImage('${e.image_id}')">×</button>`:'<span></span>'}</div>`).join('')
    :'<p class="hint">No photos or exhibits yet.</p>';
  const b=SCHEMA.image_budget;$('genBudget').textContent=`Budget: ${b.per_request} example images per request, ${b.monthly_cap} per month.`;}
async function uploadPhoto(inp){const f=inp.files[0];if(!f)return;inp.value='';await ensureSaved();
  const data=await new Promise(r=>{const fr=new FileReader();fr.onload=()=>r(fr.result.split(',')[1]);fr.readAsDataURL(f);});
  const title=prompt('Caption for this exhibit','Site photo: '+f.name.replace(/\.[^.]+$/,''));if(title===null)return;
  try{state=await api('/api/images/upload',{method:'POST',body:JSON.stringify({id:state.id,data,mime:f.type,title,kind:/quote|letter|bid/i.test(title)?'quote':'photo'})});
    renderExhibits();toast('Added as '+state.exhibits.slice(-1)[0].label);}catch(e){toast(e.message);}}
function toggleGen(){if(!SCHEMA.image_ready){toast('Example images are off. An admin can enable them in Settings → Example images.');return;}
  $('genBox').style.display=$('genBox').style.display==='none'?'block':'none';}
async function genImage(){const s=val('genSubject');if(s.length<4){toast('Describe what the example should show');return;}
  await ensureSaved();toast('Generating example image…');
  try{state=await api('/api/images/generate',{method:'POST',body:JSON.stringify({id:state.id,subject:s})});renderExhibits();toast('Example image added');}catch(e){toast(e.message);}}
function toggleRef(){$('refBox').style.display=$('refBox').style.display==='none'?'block':'none';}
async function refSearch(){const q=val('refQuery');if(q.length<3)return;$('refResults').innerHTML='<p class="hint">Searching Wikimedia Commons…</p>';
  try{const {items}=await api('/api/references?q='+encodeURIComponent(q));window._refs=items;
    $('refResults').innerHTML=items.length?items.map((c,i)=>`<div class="ref-card"><img src="${esc(c.thumb)}" alt=""><div>${esc(c.title).slice(0,60)}<br>${esc(c.artist).slice(0,40)} · ${esc(c.license)}</div>
      <button onclick="useRef(${i})">Use as reference</button></div>`).join(''):'<p class="hint">No freely-licensed matches. Try simpler words.</p>';}
  catch(e){$('refResults').innerHTML=`<p class="hint">${esc(e.message)}</p>`;}}
async function useRef(i){await ensureSaved();try{state=await api('/api/images/reference',{method:'POST',body:JSON.stringify({id:state.id,candidate:window._refs[i]})});
  renderExhibits();toast('Reference photo added with citation');}catch(e){toast(e.message);}}
async function rmImage(iid){state=await api('/api/images/delete',{method:'POST',body:JSON.stringify({id:state.id,image_id:iid})});renderExhibits();}

/* ---------- score view ---------- */
async function qualify(){toast('Scoring…');state=await api('/api/qualify',{method:'POST',body:JSON.stringify(collect())});
  CAN_WRITE=state._can_write!==false;populateReview();renderQual(state.qualification);go('qual');loadList();}
function renderQual(r){
  const pct=r.overall/100,len=Math.PI*82,c=r.blocked?'var(--stop)':col(r.overall);
  $('gauge').innerHTML=`<svg viewBox="0 0 200 120" width="200" height="120"><path d="M18 104 A82 82 0 0 1 182 104" fill="none" stroke="var(--surface-2)" stroke-width="14" stroke-linecap="round"/>
    <path d="M18 104 A82 82 0 0 1 182 104" fill="none" stroke="${c}" stroke-width="14" stroke-linecap="round" stroke-dasharray="${len}" stroke-dashoffset="${len*(1-pct)}" style="transition:stroke-dashoffset 1s"/></svg>
    <div class="score"><span class="num" style="color:${c}">${r.overall}</span><span class="den"> / 100</span></div>`;
  const T={blocked:['Blocked — required items missing','Fix the required items above. They fail the request no matter the score.'],
    ready:['Ready for Finance',`Clears the ${TH}-point bar with every required item met.`],
    review:['Close, not yet approval-ready','The findings under each section are what stands between this and approval.'],
    need:['Not ready for Finance','This reads as a summary. Start with the largest finding in each section.']}[r.status]||['',''];
  $('verdictTitle').textContent=T[0];$('verdictText').textContent=T[1];
  $('gateBox').innerHTML=(r.gates||[]).map(g=>{const cls=g.passed?'pass':g.severity==='auto_fail'?'fail':'req';
    return `<div class="gate ${cls}"><span class="gi">${g.passed?'✓':'!'}</span><div><b>${esc(g.label)}${!g.passed&&g.severity==='auto_fail'?'<span class="tag">automatic fail</span>':!g.passed?'<span class="tag" style="color:#8A5A08">required</span>':''}</b><span>${esc(g.message)}</span></div>
    ${!g.passed&&CAN_WRITE&&!state._training?`<button class="btn sm ghost" style="margin-left:auto" onclick="fixGate('${g.id}')">Fix</button>`:''}</div>`;}).join('');
  const cre=r.cre||{};
  $('creCallout').innerHTML=cre.declared==='yes'?`<div class="cre-call yes"><b>CRE-DRIVEN</b><span>This request uses the Corporate Real Estate template — status: <b>${esc(cre.template||'not requested')}</b>.</span></div>`
    :cre.declared==='no'?`<div class="cre-call no"><b>NOT CRE-DRIVEN</b><span>Declared by the requester.</span></div>`
    :`<div class="cre-call unknown"><b>CRE NOT DECLARED</b><span>${cre.hint==='explicit'?'The draft mentions Corporate Real Estate. ':cre.hint==='likely'?'This looks like facility or building work. ':''}The requester must declare whether it's CRE-driven.</span></div>`;
  $('secCards').innerHTML=r.categories.map((s,i)=>{const p=Math.round(100*s.earned/s.weight),f=s.findings||[];
    return `<div class="seccard"><div class="sec-top"><span class="sec-num">${i+1}</span><span class="sec-name">${esc(s.name)}</span><span class="sec-score" style="color:${col(p)}">${s.earned} / ${s.weight}</span></div>
    <div class="bar"><span style="width:0%;background:${col(p)}" data-w="${p}"></span></div>
    ${(s.strengths||[]).length?`<div class="sec-strong">✓ ${s.strengths.map(esc).join(' · ')}</div>`:''}
    ${f.length?`<details class="why" ${p<60?'open':''}><summary>What went wrong in your draft and why <span class="n">${f.length}</span></summary>
      ${f.map(x=>`<div class="finding ${x.severity}"><div class="f-top"><b>${esc(x.title)}</b>${x.points_lost?`<span class="f-pts">−${x.points_lost} pts</span>`:''}</div>
      <div class="f-why">${esc(x.why)}</div><div class="f-ev">From your draft: ${x.evidence&&!/^Not found/.test(x.evidence)?'“'+esc(x.evidence)+'”':esc(x.evidence||'Not found in the draft.')}</div>
      <div class="f-fix"><b>Fix:</b> ${esc(x.fix)}</div></div>`).join('')}</details>`:'<div class="sec-strong">Nothing to fix in this section.</div>'}</div>`;}).join('');
  requestAnimationFrame(()=>setTimeout(()=>document.querySelectorAll('#secCards .bar span').forEach(s=>s.style.width=s.dataset.w+'%'),50));
  if(state._training){$('coachCta').innerHTML='';}else renderCoachCta();
  renderNotes();
  const rw=r.rewrite_suggestions||{},keys=Object.keys(rw);window._rw=rw;
  $('rewriteBox').innerHTML=(keys.length||r.reviewer_summary)?`<div class="rw-panel"><h3 class="sec-h">Suggested wording</h3><p class="hint">Uses only facts already in your draft. Nothing is applied until you choose it.</p>
    ${r.reviewer_summary?`<div class="rw-item"><div class="k">Reviewer summary</div><div class="v">${esc(r.reviewer_summary)}</div></div>`:''}
    ${keys.map(k=>`<div class="rw-item"><div class="k">${esc((SEC.find(x=>x[0]===k)||[k,k])[1])}</div><div class="v">${esc(rw[k])}</div>${CAN_WRITE?`<button class="btn sm ghost" onclick="useRw('${k}',this)">Use this wording</button>`:''}</div>`).join('')}</div>`:'';
  $('qualEngine').textContent='Scored by: '+(r.engine||'rules')+(r.rules_score!==undefined?` · rules baseline ${r.rules_score}`:'')+(r.engine_error?' — '+r.engine_error:'');
  demoBar('qual');roBanner('qual');}
function useRw(k,btn){$('f_'+k).value=window._rw[k];btn.textContent='Applied — re-score to see the change';btn.disabled=true;}
function fixGate(id){populateReview();go('review');setTimeout(()=>{const el=id==='three_quotes'?$('quotesCard'):$('creSeg');el&&el.scrollIntoView({behavior:'smooth',block:'center'});},300);}
function renderNotes(){const n=state.review_notes||[],canNote=ME&&(ME.edition!=='v1'||ME.preview)&&roleRank[ME.acting.role]>=2&&!state._training&&state.id&&state.owner!==ME.acting.id;
  $('noteBox').innerHTML=(n.length||canNote)?`<div class="rw-panel"><h3 class="sec-h">Review notes</h3>${n.map(x=>`<div class="rw-item"><div class="k">${esc(x.name||'Reviewer')} · ${new Date(x.ts*1000).toLocaleString()}</div><div class="v">${esc(x.note)}</div></div>`).join('')||'<p class="hint">No notes yet.</p>'}
    ${canNote?`<textarea id="noteText" rows="2" placeholder="Leave guidance for the requester"></textarea><div class="btn-row"><button class="btn sm" onclick="addNote()">Add note</button></div>`:''}</div>`:'';}
async function addNote(){try{state=await api('/api/review-note',{method:'POST',body:JSON.stringify({id:state.id,note:val('noteText')})});renderNotes();toast('Note added');}catch(e){toast(e.message);}}

/* ---------- guided rounds (gate-aware) ---------- */
function renderCoachCta(){
  const s=state.score||0,rs=rounds(),open=openRound(),q=state.qualification||{},blocked=q.blocked;let cls,h,p,b;
  if(!CAN_WRITE){$('coachCta').innerHTML='';return;}
  if(s>=TH&&!blocked){cls='go';h=`Passing — ${s} clears the ${TH}-point bar`;p='Every required item is met. Hand it off, or tighten it with ten more questions first.';
    b=`<button class="btn" onclick="handoff()">Continue to handoff ${ARROW}</button>`+(rs.length<2&&!open?'<button class="btn ghost" onclick="startRound()">Strengthen it with 10 questions</button>':'');}
  else if(open){cls='warn';h=`Round ${open.round} questions are waiting`;p='Pick up where you left off.';b=`<button class="btn amber" onclick="renderCoach();go('coach')">Resume round ${open.round}</button>`;}
  else if(!rs.length){cls=blocked?'stop':'warn';h=blocked&&s>=TH?'Strong draft, but required items are missing':`Let's get you from ${s} to ${TH}`;
    p=(blocked?'The required items above have to be cleared regardless of score. ':'')+'Ten questions about things you already know (counts, dates, quotes, the vendor you picked) will fill the gaps. Groundwork writes your answers into the draft and re-scores.';
    b=`<button class="btn amber lg" onclick="startRound()">Answer 10 guided questions</button><button class="btn ghost" onclick="populateReview();go('review')">Edit the draft myself</button>`;}
  else if(rs.length===1){cls='warn';h='Second chance: 10 new questions';p=`Round 1 got you to ${s}. The next 10 questions are all different and aim at what is still open.`;
    b=`<button class="btn amber lg" onclick="startRound()">Generate 10 new questions</button><button class="btn ghost" onclick="renderResult();go('result')">See round 1 results</button>`;}
  else{cls='stop';h='Your finance partner has been alerted';p='Two rounds did not clear the bar, so Finance has the details and can help close the gaps.';
    b=`<button class="btn" onclick="renderResult();go('result')">See results &amp; the alert</button>`;}
  $('coachCta').innerHTML=`<div class="cta ${cls}">${roundDots()}<h3>${h}</h3><p>${p}</p><div class="btn-row">${b}</div></div>`;}
function renderCoach(){
  const r=openRound();if(!r){renderResult();go('result');return;}demoBar('coach');
  $('coachEyebrow').textContent=`Round ${r.round} of 2 · Guided questions`;
  $('coachTitle').textContent=r.round===1?`10 questions to get you to ${TH}`:'Second chance — 10 new questions';
  $('coachLede').textContent=r.round===1?`You're at ${r.score_before}. Answer what you can and skip what you don't know. Specific answers with numbers move the score most.`
    :`You're at ${r.score_before}. None of these repeat round 1.`;
  const ra=(r.required_actions||[]).map(a=>`<div class="gate ${a.severity==='auto_fail'?'fail':'req'}"><span class="gi">!</span><div><b>${esc(a.label)}${a.severity==='auto_fail'?'<span class="tag">automatic fail</span>':''}</b><span>${esc(a.message)}</span></div>
    <button class="btn sm ghost" style="margin-left:auto" onclick="fixGate('${a.id}')">Fix</button></div>`).join('');
  $('qList').innerHTML=(ra?`<p class="eyebrow">Required before this can pass</p>${ra}<p class="eyebrow" style="margin-top:18px">Your 10 questions</p>`:'')+
    r.questions.map((q,i)=>`<div class="qcard" id="qc_${q.id}"><div class="qtop"><span class="qnum">${String(i+1).padStart(2,'0')}</span><span class="qcat">${esc(q.category)}</span><span class="qfield">→ ${esc(q.field_label||q.field)}</span></div>
      <div class="qtext">${esc(q.question)}</div>${q.why?`<div class="qwhy">${esc(q.why)}</div>`:''}
      <textarea id="a_${q.id}" rows="2" placeholder="${esc(q.example?'e.g. '+q.example:'Your answer')}" oninput="qProg()"></textarea></div>`).join('');
  $('coachEngine').textContent='Questions by: '+r.engine+(r.engine_error?' — '+r.engine_error:'');
  $('coachProgWrap').style.display='flex';$('submitAnswers').disabled=false;qProg();}
function renderResult(){
  const r=lastRound();if(!r||!r.answered){renderQual(state.qualification);go('qual');return;}
  demoBar('result');const co=state.coaching||{},d=r.score_after-r.score_before,ic='<div class="rico"><svg viewBox="0 0 24 24" fill="none">';
  $('rsEyebrow').textContent=`Round ${r.round} results`;
  $('rsBefore').textContent=r.score_before;$('rsBefore').style.color=col(r.score_before);
  $('rsAfter').textContent=r.score_after;$('rsAfter').style.color=r.blocked?'var(--stop)':col(r.score_after);
  $('rsAfterLabel').textContent=`After round ${r.round}`;$('rsDelta').textContent=(d>=0?'+':'')+d+' pts';
  $('rsDelta').style.background=d>0?'var(--go-soft)':'var(--surface-2)';$('rsDelta').style.color=d>0?'#0E6B41':'var(--ink-3)';
  $('rsOrigin').textContent=(r.round>1&&co.original_score!=null?`Started at ${co.original_score} · `:'')+`needs ${r.threshold} and every required item`;
  const open=(r.required_actions||[]).map(a=>a.label).join(', ');
  $('rsBanner').innerHTML=r.passed?`<div class="readiness ok">${ic}<path d="M5 13l4 4L19 7" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg></div><div><h3>Passing — ${r.score_after} clears ${r.threshold}</h3><p>The edits below got you over the bar, and every required item is met.</p></div></div>`
    :r.round<2?`<div class="readiness need">${ic}<path d="M12 8v5M12 16h.01" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg></div><div><h3>${r.score_after>=r.threshold&&open?'Score clears the bar, but required items are open':'Still below the bar — '+r.score_after+' of '+r.threshold}</h3><p>${open?'Open: '+esc(open)+'. ':''}You get a second chance: 10 different questions aimed at what is left.</p></div></div>`
    :`<div class="readiness stop">${ic}<path d="M12 8v5M12 16h.01" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg></div><div><h3>Not cleared after two rounds — ${r.score_after} of ${r.threshold}</h3><p>${open?'Still open: '+esc(open)+'. ':''}Your finance partner has been alerted with the details so they can help.</p></div></div>`;
  const b0=Object.fromEntries((r.categories_before||[]).map(c=>[c.name,c.score]));
  $('rsCats').innerHTML=(r.categories_after||[]).map(c=>{const b=b0[c.name]??0,dd=c.score-b;
    return `<div class="cm-row"><div class="cat-top"><span class="cat-name">${esc(c.name)} <span class="hint">· ${c.weight} pts</span></span><span class="cat-val">${b} → ${c.score}${dd?` <span style="color:${dd>0?'var(--go)':'var(--stop)'}">(${dd>0?'+':''}${dd})</span>`:''}</span></div>
      <div class="cm-bars"><span class="b" style="width:${b}%"></span><span class="a" style="width:${b}%;background:${col(c.score)}" data-w="${c.score}"></span></div></div>`;}).join('');
  requestAnimationFrame(()=>setTimeout(()=>document.querySelectorAll('#rsCats .a').forEach(s=>s.style.width=s.dataset.w+'%'),60));
  window._edits=r.edits||[];renderEdits(false);
  const qm=Object.fromEntries(r.questions.map((q,i)=>[q.id,{...q,n:i+1}]));
  const nu=(r.not_usable||[]).map(id=>qm[id]).filter(Boolean),sk=(r.skipped||[]).map(id=>qm[id]).filter(Boolean);
  $('rsGaps').innerHTML=nu.length+sk.length?`<div class="card pad gaps" style="margin-bottom:16px"><h3 class="sec-h">Didn't move the score</h3><p class="hint" style="margin:0">${r.passed?'Not needed to pass, but worth closing before review.':r.round<2?'Round 2 approaches these from different angles.':'Your finance partner can help here.'}</p><ul>`+
    nu.map(q=>`<li>Q${q.n} · ${esc(q.question)} <span class="hint">— too vague to use</span></li>`).join('')+sk.map(q=>`<li>Q${q.n} · ${esc(q.question)} <span class="hint">— skipped</span></li>`).join('')+'</ul></div>':'';
  renderEscalation();
  $('rsActions').innerHTML=r.passed?`<button class="btn" style="flex:1" onclick="handoff()">Continue to handoff ${ARROW}</button><button class="btn ghost" onclick="populateReview();go('review')">Review the updated draft</button>`
    :r.round<2?`<button class="btn amber lg" style="flex:1" onclick="startRound()">Generate 10 new questions</button><button class="btn ghost" onclick="populateReview();go('review')">Edit the draft myself</button>`
    :`<button class="btn ghost" onclick="populateReview();go('review')">Keep editing the draft</button><button class="btn" style="flex:1" onclick="loadList();go('home')">Save &amp; return home</button>`;}
async function handoff(){const r=await api('/api/handoff',{method:'POST',body:JSON.stringify({id:state.id})});const ok=r.ok;
  $('handoffTitle').textContent=ok?'Passed qualification':'Saved — not ready yet';
  $('handoffText').textContent=ok?'Export the five-point document or the JSON payload.':(r.gates||[]).length?'Required items are still open: '+r.gates.map(g=>g.label).join(', ')+'.':`Score ${r.score} is below the ${r.threshold} threshold.`;
  $('handoffBanner').className='readiness '+(ok?'ok':'need');$('bannerTitle').textContent=ok?'Ready for Finance':'Needs more information';
  $('bannerText').textContent=ok?'Every field, quote and exhibit carries into the export.':'Exports are marked DRAFT until the request passes.';
  $('handoffStamp').style.background=ok?'var(--go-soft)':'var(--caution-soft)';$('handoffStamp').style.color=ok?'var(--go)':'var(--caution)';
  $('dlDocx').disabled=false;go('handoff');loadList();}

/* ---------- V2+: floor dashboard ---------- */
async function loadDash(){let d;try{d=await api('/api/dashboard');}catch(e){toast(e.message);return;}const s=d.summary;
  $('dashEyebrow').textContent=(ME.preview?'Preview · ':'')+ME.acting.role_label+' view';$('dashTitle').textContent='Floor overview';
  $('dashLede').textContent=`Every request in ${d.scope}. Analysts only see their own work; this view is read-only on theirs, with review notes.`;
  $('dashTiles').innerHTML=[['Requests',s.total],['Ready',s.ready],['Blocked',s.by_status.blocked||0],['Finance alerted',s.escalated],['Avg. score',s.avg_score??'—']]
    .map(([l,n])=>`<div class="metric"><div class="n">${n}</div><div class="l">${l}</div></div>`).join('');
  $('dashTiles').style.gridTemplateColumns='repeat(auto-fit,minmax(120px,1fr))';
  $('dashGates').innerHTML=Object.keys(s.gates_failed).length?`<div class="gate-tiles">${Object.entries(s.gates_failed).map(([g,n])=>`<span>${esc(g)} missing · ${n}</span>`).join('')}</div>`:'';
  const v3=ME.acting.role==='leader'||ME.acting.role==='finance_admin'||ME.acting.role==='admin';
  $('dashTable').className='gt';
  $('dashTable').innerHTML=`<tr><th>Request</th><th>Owner</th>${v3?'<th>Division / group</th>':'<th>Group</th>'}<th>Score</th><th>Status</th><th>Rounds</th><th>Quotes</th><th>CRE</th><th>Notes</th><th>Updated</th></tr>`+
    d.items.map(i=>`<tr class="click" onclick="openReq('${i.id}')"><td><b>${i.id}</b><br>${esc(i.title||'')}</td><td>${esc(i.owner_name||'—')}</td>
      <td>${v3?esc((i.division_name||'—')+' / '):''}${esc(i.group_name||'—')}</td><td style="color:${i.status==='blocked'?'var(--stop)':col(i.score)};font-weight:700">${i.score||'—'}</td>
      <td><span class="chip ${i.status}"><span class="led"></span>${label(i.status)}</span></td><td>${i.rounds}/2</td><td>${i.quotes}/3</td>
      <td>${i.cre==='yes'?'Yes':i.cre==='no'?'No':'—'}</td><td>${i.review_notes||''}</td><td>${new Date(i.updated*1000).toLocaleDateString()}</td></tr>`).join('');}

/* ---------- V2+: admin ---------- */
const ROLE_L={analyst:'Analyst',director:'Director',leader:'Division leader',finance_admin:'Finance admin',admin:'Platform admin'};
async function loadAdmin(tab){window._atab=tab;const tabs=[['users','People & roles'],['org','Organization'],['audit','Audit log'],['usage','Usage & budget']];
  if(ME.edition==='v3'||PVTAB==='v3')tabs.push(['idp','Identity (SSO)']);
  $('adminTabs').innerHTML=tabs.map(([k,l])=>`<button class="${k===tab?'on':''}" onclick="loadAdmin('${k}')">${l}</button>`).join('');
  const B=$('adminBody');
  try{
  if(tab==='users'){const d=await api('/api/admin/users');window._org=d.org;const grp=d.org.groups,div=Object.fromEntries(d.org.divisions.map(x=>[x.id,x.name]));
    const roles=d.roles.filter(r=>PVTAB!=='v2'||['analyst','director','admin'].includes(r));
    B.innerHTML=`<div class="card dash-table"><div class="tbl-wrap"><table class="gt"><tr><th>Name</th><th>Email</th><th>Role</th><th>Group</th><th>Active</th><th></th></tr>${d.items.map(u=>`<tr>
      <td><b>${esc(u.name)}</b><br><span class="hint">${esc(u.source)}</span></td><td>${esc(u.email)}</td>
      <td><select id="ur_${u.id}">${roles.map(r=>`<option value="${r}" ${r===u.role?'selected':''}>${ROLE_L[r]}</option>`).join('')}</select></td>
      <td><select id="ug_${u.id}">${grp.map(g=>`<option value="${g.id}" ${g.id===u.group_id?'selected':''}>${esc(g.name)} (${esc(div[g.division_id]||'')})</option>`).join('')}</select></td>
      <td><input type="checkbox" id="ua_${u.id}" ${u.active?'checked':''} style="width:auto"></td><td><button class="btn sm ghost" onclick="saveUser('${u.id}')">Save</button></td></tr>`).join('')}</table></div></div>
      ${ME.edition==='v3'&&!ME.preview?'<p class="hint" style="margin-top:12px">In V3, people are added in Entra ID and assigned an app role. They appear here on first sign-in; roles follow Entra.</p>':
      `<div class="idp"><h3 class="sec-h">Add a person</h3><div class="row"><div class="sfield"><label>Name</label><input id="nu_name"></div><div class="sfield"><label>Work email</label><input id="nu_email"></div></div>
       <div class="row"><div class="sfield"><label>Role</label><select id="nu_role">${roles.map(r=>`<option value="${r}">${ROLE_L[r]}</option>`).join('')}</select></div>
       <div class="sfield"><label>Group</label><select id="nu_group">${grp.map(g=>`<option value="${g.id}">${esc(g.name)}</option>`).join('')}</select></div></div>
       ${ME.preview?'':'<div class="sfield"><label>Temporary password (12+ characters)</label><input id="nu_pw" type="password"></div>'}
       <button class="btn sm" onclick="addUser()">Add person</button></div>`}`;}
  if(tab==='org'){const d=await api('/api/admin/users');const o=d.org;
    B.innerHTML=`<div class="card pad">${o.divisions.map(dv=>`<div class="sub-h">${esc(dv.name)}</div><ul class="g-all">${o.groups.filter(g=>g.division_id===dv.id).map(g=>`<li>${esc(g.name)}</li>`).join('')||'<li class="hint">No groups</li>'}</ul>`).join('')}
      <div class="row" style="margin-top:14px"><div class="sfield"><label>Division</label><input id="og_div" placeholder="e.g. Tech Ops Finance"></div><div class="sfield"><label>Group</label><input id="og_grp" placeholder="e.g. Capital Planning"></div></div>
      <button class="btn sm" onclick="addOrg()">Add division / group</button><p class="hint" style="margin-top:10px">${ME.edition==='v1'&&!ME.preview?'':'V2 runs one group under one director. V3 adds divisions, each with leaders and groups.'}</p></div>`;}
  if(tab==='audit'){const d=await api('/api/admin/audit');
    B.innerHTML=`<div class="card dash-table"><div class="tbl-wrap"><table class="gt"><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Detail</th></tr>${d.items.map(a=>`<tr><td>${new Date(a.ts*1000).toLocaleString()}</td><td>${esc(a.user_name||a.user_id)}</td><td>${esc(a.action)}</td><td>${esc(a.target)}</td><td>${esc(a.detail)}</td></tr>`).join('')||'<tr><td colspan="5" class="hint">Nothing yet.</td></tr>'}</table></div></div>`;}
  if(tab==='usage'){const u=await api('/api/admin/usage');
    B.innerHTML=`<div class="card pad"><div class="kv"><span>Text model</span><span class="${u.llm_ready?'ok':'no'}">${u.llm_ready?esc(u.provider+' · '+u.model):'Not connected (rules engine)'}</span>
      <span>Example images</span><span class="${u.image_ready?'ok':'no'}">${u.image_ready?'Enabled':'Off'}</span>
      <span>Images this month</span><span>${u.images_this_month} of ${u.monthly_cap}</span><span>Per-request limit</span><span>${u.per_request}</span></div>
      ${!ME.preview?'<button class="btn sm" style="margin-top:14px" onclick="openSettings()">Open settings &amp; API keys</button>':'<p class="hint" style="margin-top:12px">Settings and API keys are disabled in preview.</p>'}</div>`;}
  if(tab==='idp'){B.innerHTML=`<div class="idp"><h3 class="sec-h">Microsoft Entra ID single sign-on</h3><div class="kv">
      <span>Edition</span><span>${ME.edition.toUpperCase()}${ME.preview?' (previewing V3)':''}</span><span>SSO configured</span><span class="${ME.sso?'ok':'no'}">${ME.sso?'Yes':'No'}</span></div>
      <p class="hint" style="margin-top:12px">Register Groundwork as an app in Entra ID, create one app role per Groundwork role, assign people or groups to roles, then set these environment variables:</p>
      <pre>GW_EDITION=v3
GW_OIDC_ISSUER=https://login.microsoftonline.com/&lt;tenant-id&gt;/v2.0
GW_OIDC_CLIENT_ID=&lt;application id&gt;
GW_OIDC_CLIENT_SECRET=&lt;secret, from Key Vault&gt;
GW_BREAKGLASS_EMAIL=&lt;one emergency admin&gt;
GW_ROLE_MAP={"Groundwork.Analyst":{"role":"analyst","division":"Cargo Finance","group":"Cargo FP&amp;A"},
             "Groundwork.Director":{"role":"director","division":"Cargo Finance","group":"Cargo FP&amp;A"},
             "Groundwork.FinanceAdmin":{"role":"finance_admin"},"Groundwork.Admin":{"role":"admin"}}</pre>
      <p class="hint">People without a mapped role are refused at sign-in. Roles are re-read from Entra on every sign-in, so removing someone there removes their access here.</p></div>`;}
  }catch(e){B.innerHTML=`<p class="hint">${esc(e.message)}</p>`;}}
async function saveUser(id){try{await api('/api/admin/users/update',{method:'POST',body:JSON.stringify({id,role:$('ur_'+id).value,group_id:$('ug_'+id).value,
  division_id:(window._org.groups.find(g=>g.id===$('ug_'+id).value)||{}).division_id,active:$('ua_'+id).checked})});toast('Saved');}catch(e){toast(e.message);}}
async function addUser(){const g=$('nu_group').value;try{await api('/api/admin/users',{method:'POST',body:JSON.stringify({name:val('nu_name'),email:val('nu_email'),role:$('nu_role').value,
  group_id:g,division_id:(window._org.groups.find(x=>x.id===g)||{}).division_id,password:$('nu_pw')?$('nu_pw').value:''})});toast('Added');loadAdmin('users');}catch(e){toast(e.message);}}
async function addOrg(){try{await api('/api/admin/org',{method:'POST',body:JSON.stringify({division:val('og_div'),group:val('og_grp')})});loadAdmin('org');}catch(e){toast(e.message);}}
