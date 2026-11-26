import{api,go,html,store,toast}from'../state.js';

let currentMonth=new Date().toISOString().slice(0,7);
let viewMode='month';
let rangePreset='90d';
let customSince='';
let customUntil='';
let filterKind='all';
let selectedDate=new Date().toISOString().slice(0,10);

const RANGE_LABELS={
  month:'Mês exibido',
  '90d':'Últimos 3 meses',
  '180d':'Últimos 6 meses',
  '365d':'Último ano',
  maximum:'Todo o histórico',
  custom:'Período personalizado',
};

const WEEKDAYS=['Dom','Seg','Ter','Qua','Qui','Sex','Sáb'];

function monthLabel(ym){
  const [y,m]=ym.split('-').map(Number);
  return new Date(y,m-1,1).toLocaleDateString('pt-BR',{month:'long',year:'numeric'});
}

function shiftMonth(ym,delta){
  const [y,m]=ym.split('-').map(Number);
  const d=new Date(y,m-1+delta,1);
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
}

function monthDays(ym){
  const [y,m]=ym.split('-').map(Number);
  const first=new Date(y,m-1,1);
  const startOffset=first.getDay();
  const daysInMonth=new Date(y,m,0).getDate();
  const cells=[];
  for(let i=0;i<startOffset;i++)cells.push(null);
  for(let d=1;d<=daysInMonth;d++){
    cells.push(`${ym}-${String(d).padStart(2,'0')}`);
  }
  return cells;
}

function weekDaysFor(isoDate){
  const [y,m,d]=isoDate.split('-').map(Number);
  const dt=new Date(y,m-1,d);
  const sun=new Date(y,m-1,d-dt.getDay());
  return Array.from({length:7},(_,i)=>{
    const cur=new Date(sun.getFullYear(),sun.getMonth(),sun.getDate()+i);
    return `${cur.getFullYear()}-${String(cur.getMonth()+1).padStart(2,'0')}-${String(cur.getDate()).padStart(2,'0')}`;
  });
}

function kindBadge(kind){
  if(kind==='video')return{cls:'good',label:'Vídeo'};
  if(kind==='edit')return{cls:'warn',label:'Edição'};
  if(kind==='ad')return{cls:'good',label:'Meta Ad'};
  if(kind==='post')return{cls:'',label:'Post'};
  return{cls:'',label:'Nota'};
}

export async function renderCalendario(root){
  root.innerHTML=`
    <section class="page-head">
      <div>
        <p class="kicker">06 · calendário</p>
        <h1>Calendário Correlacionado.</h1>
        <p class="lead">Veja por dia seus lotes de vídeo, edições CapCut, postagens agendadas e campanhas do Meta Ads conectados num só fluxo.</p>
      </div>
      <div class="button-row">
        <button id="cal-prev" type="button">◀</button>
        <strong id="cal-month-lbl" class="cal-month-lbl"></strong>
        <button id="cal-next" type="button">▶</button>
        <button id="cal-today" type="button" class="quiet">Hoje</button>
        <button id="cal-mode" type="button">${viewMode==='month'?'Visão Semana':'Visão Mês'}</button>
      </div>
    </section>

    <div id="cal-kpis" class="ma-kpis"></div>

    <div class="panel cal-filter-bar">
      <div class="row grow" style="flex-wrap:wrap;gap:6px">
        <button type="button" data-k="all" class="${filterKind==='all'?'primary':''}">Tudo</button>
        <button type="button" data-k="video" class="${filterKind==='video'?'primary':''}">Lotes de Vídeo</button>
        <button type="button" data-k="edit" class="${filterKind==='edit'?'primary':''}">Edições CapCut</button>
        <button type="button" data-k="ad" class="${filterKind==='ad'?'primary':''}">Meta Ads</button>
        <button type="button" data-k="post" class="${filterKind==='post'?'primary':''}">Posts / Agendados</button>
      </div>
      <div class="row" style="flex-wrap:wrap;gap:6px">
        <select id="cal-range">
          <option value="month">Mês exibido</option>
          <option value="90d">Últimos 3 meses</option>
          <option value="180d">Últimos 6 meses</option>
          <option value="365d">Último ano</option>
          <option value="maximum">Todo o histórico</option>
          <option value="custom">Personalizado…</option>
        </select>
        <span id="cal-custom-dates" class="row" style="gap:4px" hidden>
          <input id="cal-since" type="date" value="${html(customSince)}">
          <span>até</span>
          <input id="cal-until" type="date" value="${html(customUntil)}">
          <button id="cal-apply-custom" type="button" class="primary">Aplicar</button>
        </span>
        <button id="cal-refresh" type="button" class="quiet">↻ Sincronizar</button>
      </div>
    </div>

    <div class="cal-layout">
      <section class="panel">
        <div class="cal-weekdays">${WEEKDAYS.map(w=>`<span>${w}</span>`).join('')}</div>
        <div id="cal-grid" class="cal-grid"></div>
        <div id="cal-period-list" style="margin-top:14px"></div>
      </section>

      <aside class="panel cal-drawer">
        <div class="panel-head">
          <h2>Dia <span id="cal-sel-date" class="mono">${html(selectedDate)}</span></h2>
          <span id="cal-day-count" class="counter">0</span>
        </div>
        <div id="cal-day-events" class="cal-day-events"></div>

        <hr class="cc-sep">
        <h3>+ Agendar / Correlacionar neste dia</h3>
        <form id="cal-add-form" class="cal-add-form">
          <label>Título<input id="ca-title" required placeholder="Ex.: Hook 01 Produto X"></label>
          <div class="settings">
            <label>Tipo<select id="ca-kind"><option value="ad">Anúncio Meta</option><option value="post">Postagem</option><option value="edit">Edição</option><option value="video">Lote de Vídeo</option><option value="note">Nota</option></select></label>
            <label>Canal<input id="ca-platform" value="Meta Ads"></label>
            <label>Status<select id="ca-status"><option value="PLANNED">Planejado</option><option value="EDITING">Editando</option><option value="READY">Pronto</option><option value="PUBLISHED">Publicado</option></select></label>
            <label>Lote vinculado<select id="ca-batch"><option value="">Nenhum</option>${(store.batches||[]).map(b=>`<option value="${html(b.id)}">${html(b.name)}</option>`).join('')}</select></label>
          </div>
          <label>Campanha Meta (opcional)<select id="ca-camp"><option value="">Nenhuma</option></select></label>
          <label>Observações / Link<textarea id="ca-notes" rows="2" placeholder="Ex.: Subir conjunto aberto BR, testar 3 hooks…"></textarea></label>
          <button type="submit" class="primary">Salvar no calendário</button>
        </form>
      </aside>
    </div>`;

  const monthLbl=root.querySelector('#cal-month-lbl');
  const kpiBox=root.querySelector('#cal-kpis');
  const gridBox=root.querySelector('#cal-grid');
  const dayDateEl=root.querySelector('#cal-sel-date');
  const dayCountEl=root.querySelector('#cal-day-count');
  const dayEventsEl=root.querySelector('#cal-day-events');
  const periodListEl=root.querySelector('#cal-period-list');
  const campSel=root.querySelector('#ca-camp');
  const rangeSel=root.querySelector('#cal-range');
  const customDatesBox=root.querySelector('#cal-custom-dates');
  const sinceInp=root.querySelector('#cal-since');
  const untilInp=root.querySelector('#cal-until');
  rangeSel.value=rangePreset;
  customDatesBox.hidden=rangePreset!=='custom';

  let calData={events:[],kpis:{},meta_campaigns:[]};

  async function load(){
    monthLbl.textContent=monthLabel(currentMonth);
    gridBox.innerHTML='<div class="empty">Carregando eventos e métricas…</div>';
    try{
      let qs=`month=${encodeURIComponent(currentMonth)}&range_preset=${encodeURIComponent(rangePreset)}`;
      if(rangePreset==='custom'&&customSince&&customUntil){
        qs+=`&since=${encodeURIComponent(customSince)}&until=${encodeURIComponent(customUntil)}`;
      }
      calData=await api(`/api/calendar?${qs}`);
      renderAll();
    }catch(err){
      gridBox.innerHTML=`<div class="notice bad">${html(err.message)}</div>`;
    }
  }

  function renderAll(){
    const k=calData.kpis||{};
    const rangeTag=calData.range_preset==='custom'&&calData.since&&calData.until
      ? `${calData.since} → ${calData.until}`
      : (RANGE_LABELS[calData.range_preset||rangePreset]||'Período');
    kpiBox.innerHTML=`
      <div class="panel"><small class="hint">Lotes (${html(rangeTag)})</small><strong class="ma-kpi">${k.videos||0}</strong></div>
      <div class="panel"><small class="hint">Edições CapCut</small><strong class="ma-kpi">${k.edits||0}</strong></div>
      <div class="panel"><small class="hint">Anúncios Meta</small><strong class="ma-kpi">${k.ads||0}</strong></div>
      <div class="panel"><small class="hint">Gasto Meta (${html(rangeTag)})</small><strong class="ma-kpi">${Number(k.meta_spend||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'})}</strong></div>`;

    campSel.innerHTML='<option value="">Nenhuma</option>'+(calData.meta_campaigns||[]).map(c=>`<option value="${html(c.id)}">${html(c.name)} (${html(c.status)})</option>`).join('');

    const visibleEvents=(calData.events||[]).filter(e=>filterKind==='all'||e.kind===filterKind||(filterKind==='post'&&e.source==='manual'));
    const byDate={};
    for(const ev of visibleEvents){
      byDate[ev.date]=byDate[ev.date]||[];
      byDate[ev.date].push(ev);
    }

    if(periodListEl){
      periodListEl.innerHTML=`
        <details open>
          <summary style="cursor:pointer;font-size:12px;color:var(--muted)">Histórico do período (${visibleEvents.length} evento(s) · ${html(calData.since||'')} até ${html(calData.until||'')})</summary>
          <div style="display:grid;gap:6px;margin-top:8px;max-height:240px;overflow:auto">
            ${visibleEvents.slice(0,80).map(ev=>{
              const b=kindBadge(ev.kind);
              return `<button type="button" class="quiet cal-jump-day" data-day="${html(ev.date)}" style="justify-content:space-between;display:flex;width:100%;text-align:left;padding:6px 10px">
                <span><span class="badge ${b.cls}">${html(b.label)}</span> ${html(ev.title)}</span>
                <small class="mono hint">${html(ev.date)}</small>
              </button>`;
            }).join('')||'<div class="hint">Nenhum evento encontrado neste intervalo.</div>'}
          </div>
        </details>`;
      periodListEl.querySelectorAll('.cal-jump-day').forEach(btn=>btn.addEventListener('click',()=>{
        selectedDate=btn.dataset.day;
        currentMonth=selectedDate.slice(0,7);
        monthLbl.textContent=monthLabel(currentMonth);
        renderAll();
      }));
    }

    const cells=viewMode==='month'?monthDays(currentMonth):weekDaysFor(selectedDate);
    gridBox.className=`cal-grid ${viewMode==='week'?'is-week':''}`;
    gridBox.replaceChildren();

    const todayIso=new Date().toISOString().slice(0,10);
    for(const iso of cells){
      const cell=document.createElement('button');
      cell.type='button';
      if(!iso){
        cell.className='cal-cell is-pad';
        cell.disabled=true;
        gridBox.append(cell);
        continue;
      }
      const list=byDate[iso]||[];
      cell.className=`cal-cell ${iso===selectedDate?'is-selected':''} ${iso===todayIso?'is-today':''}`;
      const dayNum=iso.slice(8,10);
      const pills=list.slice(0,3).map(ev=>{
        const b=kindBadge(ev.kind);
        return `<span class="cal-pill ${b.cls}">${html(b.label)}: ${html(ev.title)}</span>`;
      }).join('');
      const more=list.length>3?`<small class="hint">+${list.length-3} mais</small>`:'';
      cell.innerHTML=`<span class="cal-day-num mono">${dayNum}</span><div class="cal-cell-pills">${pills}${more}</div>`;
      cell.addEventListener('click',()=>{selectedDate=iso;renderAll()});
      gridBox.append(cell);
    }

    dayDateEl.textContent=selectedDate;
    const dayList=byDate[selectedDate]||[];
    dayCountEl.textContent=String(dayList.length);
    dayEventsEl.replaceChildren();
    if(!dayList.length){
      dayEventsEl.innerHTML='<div class="empty">Nenhum vídeo, edição ou anúncio neste dia ainda.</div>';
      return;
    }
    for(const ev of dayList){
      const card=document.createElement('article');
      card.className='cal-event-card';
      const b=kindBadge(ev.kind);
      card.innerHTML=`
        <div class="panel-head">
          <span class="badge ${b.cls}">${html(b.label)} · ${html(ev.status)}</span>
          <small class="hint">${html(ev.platform)}</small>
        </div>
        <strong>${html(ev.title)}</strong>
        ${ev.subtitle?`<p class="hint">${html(ev.subtitle)}</p>`:''}
        <div class="button-row">
          ${ev.batch_id?`<button type="button" class="quiet cal-go-batch">Abrir lote →</button>`:''}
          ${ev.media_path?`<button type="button" class="quiet cal-go-edit">Editar →</button>`:''}
          ${ev.media_path?`<button type="button" class="primary cal-go-meta">Subir no Meta →</button>`:''}
          ${ev.source==='manual'?`<button type="button" class="danger quiet cal-del">Remover</button>`:''}
        </div>`;
      card.querySelector('.cal-go-batch')?.addEventListener('click',()=>go('videos',ev.batch_id));
      card.querySelector('.cal-go-edit')?.addEventListener('click',()=>{
        store.editorTarget={path:ev.media_path,folder:''};
        go('editar');
      });
      card.querySelector('.cal-go-meta')?.addEventListener('click',()=>{
        store.metaDraft={media_path:ev.media_path,ad_name:ev.title.replace(/\.mp4$/i,'')};
        go('meta-ads');
      });
      card.querySelector('.cal-del')?.addEventListener('click',async()=>{
        await api(`/api/calendar/items/${encodeURIComponent(ev.id)}`,{method:'DELETE',body:{}});
        toast('Item removido do calendário.');
        load();
      });
      dayEventsEl.append(card);
    }
  }

  root.querySelector('#cal-prev').addEventListener('click',()=>{
    currentMonth=shiftMonth(currentMonth,-1);
    selectedDate=`${currentMonth}-01`;
    load();
  });
  root.querySelector('#cal-next').addEventListener('click',()=>{
    currentMonth=shiftMonth(currentMonth,1);
    selectedDate=`${currentMonth}-01`;
    load();
  });
  root.querySelector('#cal-today').addEventListener('click',()=>{
    selectedDate=new Date().toISOString().slice(0,10);
    currentMonth=selectedDate.slice(0,7);
    load();
  });
  root.querySelector('#cal-mode').addEventListener('click',ev=>{
    viewMode=viewMode==='month'?'week':'month';
    ev.currentTarget.textContent=viewMode==='month'?'Visão Semana':'Visão Mês';
    renderAll();
  });
  root.querySelectorAll('[data-k]').forEach(btn=>btn.addEventListener('click',()=>{
    filterKind=btn.dataset.k;
    root.querySelectorAll('[data-k]').forEach(b=>b.className=b.dataset.k===filterKind?'primary':'');
    renderAll();
  }));
  root.querySelector('#cal-refresh').addEventListener('click',load);
  rangeSel.addEventListener('change',()=>{
    rangePreset=rangeSel.value;
    customDatesBox.hidden=rangePreset!=='custom';
    if(rangePreset!=='custom'){
      load();
    }else if(!sinceInp.value||!untilInp.value){
      const today=new Date();
      const prev=new Date(today.getFullYear(),today.getMonth()-3,today.getDate());
      sinceInp.value=prev.toISOString().slice(0,10);
      untilInp.value=today.toISOString().slice(0,10);
    }
  });
  root.querySelector('#cal-apply-custom').addEventListener('click',()=>{
    customSince=sinceInp.value;
    customUntil=untilInp.value;
    if(!customSince||!customUntil){toast('Escolha a data inicial e a data final.');return}
    load();
  });

  root.querySelector('#cal-add-form').addEventListener('submit',async ev=>{
    ev.preventDefault();
    try{
      await api('/api/calendar/items',{method:'POST',body:{
        scheduled_date:selectedDate,
        title:root.querySelector('#ca-title').value.trim(),
        kind:root.querySelector('#ca-kind').value,
        platform:root.querySelector('#ca-platform').value.trim(),
        status:root.querySelector('#ca-status').value,
        batch_id:root.querySelector('#ca-batch').value||null,
        meta_campaign_id:root.querySelector('#ca-camp').value||'',
        notes:root.querySelector('#ca-notes').value.trim(),
      }});
      root.querySelector('#ca-title').value='';
      root.querySelector('#ca-notes').value='';
      toast(`Agendado em ${selectedDate}.`);
      load();
    }catch(err){toast(err.message)}
  });

  await load();
}
