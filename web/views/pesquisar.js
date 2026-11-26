import{api,go,html,store,toast}from'../state.js';
import{openPageEditorModal}from'../components/page-editor.js';
import{openFlowModal}from'../components/flow-modal.js';

let activeMode='trending'; // 'trending' | 'creatives' | 'products'
let lastSearch=null;
let cachedTrending=null;
let gptConfig={project_url:'',instructions:'',monitor_keywords:'natal, skincare, organizador'};
let activeStream=null; // single active inline video: { id, url }
let downloadedMap=new Map();
let savedDownloads={media:[],pages:[]};
let competitorScope=null; // { label, page_id, author, domain }
let trendingPage=1;
let resultsPage=1;
const PAGE_SIZE=16;
let trendingTrack='all'; // 'all' | 'ecommerce' | 'youtube_dark'
let selectedCategories=new Set();

const CAT_CATALOG={
  ecommerce:[
    {id:'beleza',label:'Beleza & Skincare'},
    {id:'cozinha',label:'Casa & Cozinha'},
    {id:'tech',label:'Tech & Gadgets'},
    {id:'pet',label:'Pet'},
    {id:'fitness',label:'Fitness & Saúde'},
    {id:'moda',label:'Moda & Acessórios'},
    {id:'natal',label:'Natal & Sazonais'},
  ],
  youtube_dark:[
    {id:'curiosidades',label:'Curiosidades & Fatos'},
    {id:'historia',label:'História & Mistérios'},
    {id:'financas',label:'Finanças & Negócios'},
    {id:'motivacao',label:'Estoicismo & Foco'},
    {id:'terror',label:'Terror & Suspense'},
    {id:'ia',label:'IA & Tecnologia'},
  ],
};

let creativeSources=new Set(['meta_ads','tiktok','instagram','youtube','trends']);
let productSources=new Set(['aliexpress','amazon','mercadolivre','shopify','shopee']);

let filters={
  source:'all',
  mediaType:'all',
  minLikes:0,
  minViews:0,
  minAds:1,
  minSold:0,
  sort:'score',
  text:'',
};

const ICONS={
  search:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>',
  flame:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/></svg>',
  video:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>',
  bag:'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>',
  target:'<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>',
  play:'<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="6 3 20 12 6 21 6 3"/></svg>',
  download:'<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  mic:'<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/></svg>',
  copy:'<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  sparkles:'<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z"/></svg>',
  ext:'<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
  edit:'<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>',
};

function ico(name){return ICONS[name]||''}

function fmtNum(v){
  if(v==null)return '—';
  const n=Number(v);
  if(n>=1_000_000)return `${(n/1_000_000).toFixed(1)}M`;
  if(n>=1_000)return `${(n/1_000).toFixed(1)}k`;
  return String(n);
}

function openTranscriptDialog(data){
  const dlg=document.createElement('dialog');
  dlg.className='sp-dialog';
  dlg.innerHTML=`
    <div class="panel-head" style="margin-bottom:10px">
      <div>
        <h3>${ico('mic')} Transcrição · ${html(data.name||'vídeo')}</h3>
        <small class="hint">Idioma detectado: ${html(data.language||'pt')} · Baixado em /media/spy/media/${html(data.name||'')}</small>
      </div>
      <button type="button" class="quiet" id="tr-close">×</button>
    </div>
    <textarea id="tr-text" rows="10" class="mono">${html(data.text||'Sem fala detectada.')}</textarea>
    <div class="button-row" style="margin-top:10px">
      <button type="button" id="tr-copy">${ico('copy')} Copiar transcrição</button>
      ${data.local_path?`<button type="button" id="tr-edit">${ico('edit')} Abrir no Editor</button>`:''}
      <button type="button" id="tr-gpt" class="primary">${ico('sparkles')} Gerar Oferta + Prompts GPT →</button>
    </div>`;
  document.body.append(dlg);
  dlg.showModal();
  dlg.querySelector('#tr-close').addEventListener('click',()=>dlg.close());
  dlg.addEventListener('close',()=>dlg.remove());
  dlg.querySelector('#tr-copy').addEventListener('click',async()=>{
    try{await navigator.clipboard.writeText(dlg.querySelector('#tr-text').value);toast('Transcrição copiada.')}catch{toast('Selecione e copie com Ctrl+C.')}
  });
  dlg.querySelector('#tr-edit')?.addEventListener('click',()=>{
    dlg.close();
    store.editorTargetPath=data.local_path;
    go('editar');
  });
  dlg.querySelector('#tr-gpt').addEventListener('click',()=>{
    const txt=dlg.querySelector('#tr-text').value;
    dlg.close();
    openOfferGptDialog(document.body,{transcript:txt});
  });
}

function openSalesChartDialog(it){
  const sold=Math.max(10,Number(it.sold_count||0)||120);
  const price=Number(it.price_value||0);
  const revMin=price?price*sold:0;
  // Build deterministic 7-period curve around current sold momentum
  const weights=[0.42,0.51,0.58,0.67,0.79,0.91,1.0];
  const labels=['Sem 1','Sem 2','Sem 3','Sem 4','D-7','D-3','Hoje'];
  const vals=weights.map((w,i)=>Math.max(1,Math.round((sold/7)*w*(1+(i%2===0?0.05:-0.04)))));
  const maxV=Math.max(...vals,1);
  const W=520,H=165,padX=28,padY=18;
  const pts=vals.map((v,i)=>{
    const x=padX+(i*(W-padX*2))/(vals.length-1);
    const y=H-padY-((v/maxV)*(H-padY*2));
    return {x:Math.round(x),y:Math.round(y),v,lbl:labels[i]};
  });
  const poly=pts.map(p=>`${p.x},${p.y}`).join(' ');
  const area=`${pts[0].x},${H-padY} ${poly} ${pts[pts.length-1].x},${H-padY}`;

  const dlg=document.createElement('dialog');
  dlg.className='sp-dialog';
  dlg.innerHTML=`
    <div class="panel-head" style="margin-bottom:8px">
      <div>
        <h3>📊 Gráfico de Vendas · ${html(it.source_label||it.source)}</h3>
        <small class="hint">${html(it.title||'')}</small>
      </div>
      <button type="button" class="quiet" id="sc-close">×</button>
    </div>
    <div class="sp-chart-box">
      <svg viewBox="0 0 ${W} ${H}" width="100%" height="185" role="img" aria-label="Curva estimada de vendas">
        <defs>
          <linearGradient id="spGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#4ade80" stop-opacity="0.45"/>
            <stop offset="100%" stop-color="#4ade80" stop-opacity="0.02"/>
          </linearGradient>
        </defs>
        <line x1="${padX}" y1="${H-padY}" x2="${W-padX}" y2="${H-padY}" stroke="#283540" stroke-width="1"/>
        <polygon points="${area}" fill="url(#spGrad)"/>
        <polyline points="${poly}" fill="none" stroke="#4ade80" stroke-width="2.5" stroke-linejoin="round"/>
        ${pts.map(p=>`
          <circle cx="${p.x}" cy="${p.y}" r="4" fill="#9ee7ce"/>
          <text x="${p.x}" y="${p.y-8}" text-anchor="middle" fill="#cbd5e1" font-size="10">${fmtNum(p.v)}</text>
          <text x="${p.x}" y="${H-3}" text-anchor="middle" fill="#64748b" font-size="10">${p.lbl}</text>`).join('')}
      </svg>
    </div>
    <div class="sp-chart-metrics">
      <div><small>Vendas declaradas</small><strong>${html(it.sold_text||`${fmtNum(sold)} vendas`)}</strong></div>
      <div><small>Preço unitário</small><strong>${html(it.price_text||'—')}</strong></div>
      <div><small>Nota</small><strong>${it.rating?`★ ${it.rating}`:'—'}</strong></div>
      <div><small>Volume bruto mín. est.</small><strong>${revMin?revMin.toLocaleString('pt-BR',{style:'currency',currency:'BRL',maximumFractionDigits:0}):'—'}</strong></div>
    </div>
    <small class="hint" style="display:block;margin-top:8px">Nota: curva proporcional ao volume público declarado na origem (${html(it.sold_text||'sem contagem pública')}); marketplaces não expõem série diária histórica gratuita.</small>
    <div class="button-row" style="margin-top:10px">
      <a class="button" href="${html(it.landing_url||it.url)}" target="_blank" rel="noopener">Abrir anúncio/produto ↗</a>
      <button type="button" id="sc-search-ads" class="primary">Buscar criativos desse produto →</button>
    </div>`;
  document.body.append(dlg);
  dlg.showModal();
  dlg.querySelector('#sc-close').addEventListener('click',()=>dlg.close());
  dlg.addEventListener('close',()=>dlg.remove());
  dlg.querySelector('#sc-search-ads').addEventListener('click',()=>{
    const term=(it.title||'').split(/[,|-]/)[0].trim().slice(0,48);
    dlg.close();
    const qi=document.querySelector('#sp-query');
    const cm=document.querySelector('[data-mode="creatives"]');
    if(cm)cm.click();
    if(qi&&term){qi.value=term;document.querySelector('#sp-search-btn')?.click()}
  });
}

function openOfferGptDialog(root,prefill={}){
  const pages=savedDownloads.pages||[];
  const vids=(savedDownloads.media||[]).filter(m=>m.media_type==='video');
  const dlg=document.createElement('dialog');
  dlg.className='sp-dialog';
  dlg.innerHTML=`
    <div class="panel-head" style="margin-bottom:10px">
      <div>
        <h3>${ico('sparkles')} Melhorar Oferta & Montar Fila de Imagens + Vídeos no ChatGPT</h3>
        <small class="hint">Envia página clonada + transcrição do vídeo para sua sessão treinada (ou chat novo) e devolve JSON pronto para o FlowLocal.</small>
      </div>
      <button type="button" class="quiet" id="og-close">×</button>
    </div>
    <div class="cal-add-form">
      <label>Projeto (<code>/g/g-p-...</code>) ou Conversa treinada (<code>/c/...</code>) no ChatGPT (opcional)
        <input id="og-project" value="${html(gptConfig.project_url||'')}" placeholder="Ex.: https://chatgpt.com/c/... ou https://chatgpt.com/g/g-p-... (vazio = novo chat)">
      </label>
      <div class="settings">
        <label>Página clonada (opcional)
          <select id="og-page">
            <option value="">Sem página anexada</option>
            ${pages.map(p=>`<option value="${html(p.name)}" ${prefill.page_filename===p.name?'selected':''}>${html(p.name)}</option>`).join('')}
          </select>
        </label>
        <label>Produto / Objetivo
          <input id="og-goal" value="${html(prefill.product||lastSearch?.query||'')}" placeholder="Ex.: Escova Alisadora — foco em praticidade e garantia">
        </label>
      </div>
      ${vids.some(v=>v.transcript)?`
        <label>Carregar transcrição salva
          <select id="og-saved-tr">
            <option value="">Escolher vídeo transcrito…</option>
            ${vids.filter(v=>v.transcript).map(v=>`<option value="${html(v.name)}">${html(v.name)}</option>`).join('')}
          </select>
        </label>`:''}
      <label>Transcrição / Script do vídeo de referência
        <textarea id="og-tr" rows="4" placeholder="Cole ou selecione a transcrição de um vídeo baixado...">${html(prefill.transcript||'')}</textarea>
      </label>
      <label>Instruções extras do seu método (salvas na sua configuração)
        <textarea id="og-inst" rows="2" placeholder="Ex.: Sempre criar 4 cenas em estúdio + 3 variações de hook UGC para Meta Reels">${html(gptConfig.instructions||'')}</textarea>
      </label>
      <div class="button-row">
        <button type="button" id="og-save-cfg">Salvar sessão treinada</button>
        <button type="button" id="og-copy-prompt">${ico('copy')} Copiar Prompt</button>
        <button type="button" id="og-run" class="primary">${ico('sparkles')} Gerar Oferta + Prompts no GPT →</button>
      </div>
      <div id="og-result" style="margin-top:8px"></div>
    </div>`;
  document.body.append(dlg);
  dlg.showModal();
  dlg.querySelector('#og-close').addEventListener('click',()=>dlg.close());
  dlg.addEventListener('close',()=>dlg.remove());

  dlg.querySelector('#og-saved-tr')?.addEventListener('change',e=>{
    const hit=vids.find(v=>v.name===e.target.value);
    if(hit?.transcript)dlg.querySelector('#og-tr').value=hit.transcript;
  });

  function readPayload(){
    return {
      page_filename:dlg.querySelector('#og-page').value,
      transcript:dlg.querySelector('#og-tr').value,
      product:dlg.querySelector('#og-goal').value,
      project_url:dlg.querySelector('#og-project').value,
      instructions:dlg.querySelector('#og-inst').value,
    };
  }

  dlg.querySelector('#og-save-cfg').addEventListener('click',async()=>{
    try{
      gptConfig=await api('/api/spy/gpt-config',{
        method:'POST',
        body:{
          project_url:dlg.querySelector('#og-project').value,
          instructions:dlg.querySelector('#og-inst').value,
        },
      });
      toast('Configuração da sessão treinada salva.');
    }catch(err){toast(err.message)}
  });

  dlg.querySelector('#og-copy-prompt').addEventListener('click',async()=>{
    try{
      const res=await api('/api/spy/offer-prompt',{method:'POST',body:readPayload()});
      await navigator.clipboard.writeText(res.prompt);
      toast('Prompt copiado! Cole no seu ChatGPT se preferir rodar manual.');
    }catch(err){toast(err.message)}
  });

  dlg.querySelector('#og-run').addEventListener('click',async ev=>{
    const btn=ev.currentTarget;
    const outBox=dlg.querySelector('#og-result');
    btn.disabled=true;
    btn.textContent='Consultando ChatGPT Web…';
    outBox.innerHTML='<div class="hint">Analisando página/vídeo e gerando JSON de imagens + vídeos no ChatGPT… (pode levar 30-90s)</div>';
    try{
      const payload=readPayload();
      await api('/api/spy/gpt-config',{method:'POST',body:{project_url:payload.project_url,instructions:payload.instructions}});
      const res=await api('/api/spy/offer-to-flow',{method:'POST',body:payload});
      const oa=res.offer_analysis||{};
      outBox.innerHTML=`
        <div class="notice good">
          <strong>${html(oa.headline_melhorada||'Oferta e JSON prontos!')}</strong>
          ${oa.mecanismo_unico?`<p class="hint" style="margin:6px 0"><strong>Mecanismo:</strong> ${html(oa.mecanismo_unico)}</p>`:''}
          ${oa.copy_anuncio?`<p class="hint" style="margin:6px 0"><strong>Copy:</strong> ${html(oa.copy_anuncio)}</p>`:''}
          <div class="button-row" style="margin-top:8px">
            <button type="button" id="og-open-flow" class="primary">Abrir Fila de Imagens & Vídeos →</button>
          </div>
        </div>`;
      outBox.querySelector('#og-open-flow').addEventListener('click',()=>{
        dlg.close();
        openFlowModal(root,null,JSON.stringify(res.flow_spec,null,2));
      });
    }catch(err){
      outBox.innerHTML=`<div class="notice bad">${html(err.message)}</div>`;
    }finally{
      btn.disabled=false;
      btn.innerHTML=`${ico('sparkles')} Gerar Oferta + Prompts no GPT →`;
    }
  });
}

export async function renderPesquisar(root){
  await Promise.all([
    api('/api/spy/gpt-config').then(c=>{gptConfig=c}).catch(()=>{}),
    api('/api/spy/downloads').then(d=>{savedDownloads=d}).catch(()=>{}),
  ]);

  root.innerHTML=`
    <section class="page-head">
      <div>
        <h1>Pesquisar & Em Alta.</h1>
        <p class="lead">Radar de tendências, produtos validados e espionagem limpa de criativos.</p>
      </div>
      <div class="sp-mode-tabs">
        <button type="button" class="sp-mode-btn ${activeMode==='trending'?'primary':''}" data-mode="trending">${ico('flame')} Em Alta</button>
        <button type="button" class="sp-mode-btn ${activeMode==='creatives'?'primary':''}" data-mode="creatives">${ico('video')} Criativos & Vídeos</button>
        <button type="button" class="sp-mode-btn ${activeMode==='products'?'primary':''}" data-mode="products">${ico('bag')} Produtos Validados</button>
        <button type="button" class="sp-mode-btn" id="sp-open-gpt-offer">${ico('sparkles')} Oferta & Prompts GPT</button>
      </div>
    </section>

    <form id="sp-search-form" class="panel" style="padding:12px 16px;margin-bottom:14px">
      <div class="sp-search-shell">
        <input id="sp-query" value="${html(lastSearch?.query||'')}" placeholder="Busque produto, nicho, @concorrente ou URL do anúncio/loja…">
        <select id="sp-country" style="width:90px">
          <option value="BR">BR</option>
          <option value="US">US</option>
        </select>
        <button id="sp-search-btn" class="primary" type="submit">${ico('search')} Pesquisar</button>
      </div>
      <div class="sp-source-pills" id="sp-source-pills"></div>
    </form>

    <div id="sp-comp-slot"></div>
    <div id="sp-filters-slot"></div>
    <div id="sp-main-stage"></div>

    <details class="panel" style="margin-top:16px" id="sp-vault-details">
      <summary style="cursor:pointer;font-weight:600">Arquivos salvos (${(savedDownloads.pages||[]).length} páginas · ${(savedDownloads.media||[]).length} mídias)</summary>
      <div class="settings" style="margin-top:12px">
        <div>
          <h4>Páginas clonadas</h4>
          <div id="sp-saved-pages"></div>
          <div class="row" style="margin-top:8px">
            <input id="sp-clone-direct-url" placeholder="https://sitedoconcorrente.com.br/oferta">
            <button type="button" id="sp-clone-direct-btn">${ico('copy')} Clonar</button>
          </div>
        </div>
        <div>
          <h4>Vídeos baixados & Transcrições</h4>
          <div id="sp-saved-media"></div>
        </div>
      </div>
    </details>`;

  const qInput=root.querySelector('#sp-query');
  const countrySelect=root.querySelector('#sp-country');
  const pillsBox=root.querySelector('#sp-source-pills');
  const compSlot=root.querySelector('#sp-comp-slot');
  const filtersSlot=root.querySelector('#sp-filters-slot');
  const mainStage=root.querySelector('#sp-main-stage');

  root.querySelector('#sp-open-gpt-offer').addEventListener('click',()=>openOfferGptDialog(root));

  root.querySelectorAll('[data-mode]').forEach(btn=>{
    btn.addEventListener('click',async()=>{
      activeMode=btn.dataset.mode;
      root.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('primary',b.dataset.mode===activeMode));
      filters.source='all';
      renderSourcePills();
      if(activeMode==='trending'){
        renderFiltersBar();
        await renderTrendingHome();
      }else if(lastSearch){
        renderFiltersBar();
        renderSearchResults();
      }else{
        renderFiltersBar();
        mainStage.innerHTML=`<div class="panel"><div class="empty">Digite um produto ou termo acima e clique em <strong>Pesquisar</strong>.</div></div>`;
      }
    });
  });

  function renderSourcePills(){
    const defs=activeMode==='products'
      ?[['aliexpress','AliExpress'],['amazon','Amazon BR'],['mercadolivre','Mercado Livre'],['shopify','Shopify'],['shopee','Shopee']]
      :[['meta_ads','Meta Ads'],['tiktok','TikTok'],['instagram','Instagram Reels'],['youtube','YT Shorts'],['aliexpress','AliExpress'],['amazon','Amazon BR'],['trends','Trends']];
    const activeSet=activeMode==='products'?productSources:creativeSources;
    pillsBox.innerHTML=defs.map(([id,lbl])=>`
      <button type="button" class="sp-source-pill ${activeSet.has(id)?'is-on':''}" data-src="${id}">${html(lbl)}</button>`).join('');
    pillsBox.querySelectorAll('[data-src]').forEach(b=>{
      b.addEventListener('click',()=>{
        const id=b.dataset.src;
        if(activeSet.has(id)&&activeSet.size>1)activeSet.delete(id);
        else activeSet.add(id);
        b.classList.toggle('is-on',activeSet.has(id));
      });
    });
  }

  function renderCompetitorBar(){
    if(!competitorScope){compSlot.replaceChildren();return}
    compSlot.innerHTML=`
      <div class="sp-competitor-bar">
        <span>${ico('target')} Pesquisando só no concorrente: <strong>${html(competitorScope.label)}</strong></span>
        <button type="button" class="quiet" id="sp-clear-comp">✕ Mostrar todos</button>
      </div>`;
    compSlot.querySelector('#sp-clear-comp').addEventListener('click',async()=>{
      competitorScope=null;
      renderCompetitorBar();
      if(qInput.value.trim())await runSearch(qInput.value.trim());
    });
  }

  function filteredItems(){
    const items=lastSearch?.items||[];
    const qf=filters.text.trim().toLowerCase();
    const res=items.filter(it=>{
      if(filters.source!=='all'&&it.source!==filters.source)return false;
      if(filters.mediaType!=='all'&&it.media_type!==filters.mediaType)return false;
      if((it.likes||0)<filters.minLikes)return false;
      if((it.views||0)<filters.minViews)return false;
      if((it.ad_count||1)<filters.minAds)return false;
      if((it.sold_count||0)<filters.minSold)return false;
      if(qf&&!`${it.title||''} ${it.text||''} ${it.author||''} ${it.domain||''}`.toLowerCase().includes(qf))return false;
      return true;
    });
    res.sort((a,b)=>{
      if(filters.sort==='likes')return (b.likes||0)-(a.likes||0);
      if(filters.sort==='views')return (b.views||0)-(a.views||0);
      if(filters.sort==='sold')return (b.sold_count||0)-(a.sold_count||0);
      if(filters.sort==='ads')return (b.ad_count||1)-(a.ad_count||1);
      return (b.validation_score||0)-(a.validation_score||0);
    });
    return res;
  }

  function renderFiltersBar(){
    if(!lastSearch||activeMode==='trending'){filtersSlot.replaceChildren();return}
    const isProd=activeMode==='products'||(filters.source!=='all'&&['aliexpress','amazon','mercadolivre','shopify','shopee'].includes(filters.source));
    filtersSlot.innerHTML=`
      <div class="panel sp-floating-filters" style="margin-bottom:12px">
        <div class="sp-filters-grid">
          <label>Fonte
            <select id="f-src">
              <option value="all">Todas (${(lastSearch.items||[]).length})</option>
              <option value="meta_ads">Meta Ads</option>
              <option value="tiktok">TikTok</option>
              <option value="instagram">Instagram</option>
              <option value="youtube">YT Shorts</option>
              <option value="aliexpress">AliExpress</option>
              <option value="amazon">Amazon BR</option>
              <option value="mercadolivre">Mercado Livre</option>
              <option value="shopify">Shopify</option>
              <option value="shopee">Shopee</option>
            </select>
          </label>
          ${isProd?`
            <label>Vendidos mín.
              <select id="f-sold">
                <option value="0">Todos</option>
                <option value="100">100+ vendidos</option>
                <option value="500">500+ vendidos</option>
                <option value="1000">1.000+ vendidos</option>
              </select>
            </label>
          `:`
            <label>Mínimo Likes
              <select id="f-likes">
                <option value="0">Qualquer</option>
                <option value="1000">1.000+ likes</option>
                <option value="10000">10.000+ likes</option>
                <option value="50000">50.000+ likes</option>
              </select>
            </label>
            <label>Mínimo Views
              <select id="f-views">
                <option value="0">Qualquer</option>
                <option value="10000">10k+ views</option>
                <option value="100000">100k+ views</option>
                <option value="500000">500k+ views</option>
              </select>
            </label>
            <label>Qtd. Anúncios
              <select id="f-ads">
                <option value="1">1+</option>
                <option value="2">2+ variações</option>
                <option value="5">5+ variações</option>
              </select>
            </label>
          `}
          <label>Ordenar
            <select id="f-sort">
              <option value="score">Mais validados</option>
              <option value="likes">Mais likes</option>
              <option value="views">Mais views</option>
              <option value="sold">Mais vendidos</option>
              <option value="ads">Mais anúncios</option>
            </select>
          </label>
          <label>Filtrar texto
            <input id="f-text" value="${html(filters.text)}" placeholder="Filtrar…">
          </label>
        </div>
      </div>`;
    filtersSlot.querySelector('#f-src').value=filters.source;
    filtersSlot.querySelector('#f-sort').value=filters.sort;
    if(filtersSlot.querySelector('#f-sold'))filtersSlot.querySelector('#f-sold').value=String(filters.minSold);
    if(filtersSlot.querySelector('#f-likes'))filtersSlot.querySelector('#f-likes').value=String(filters.minLikes);
    if(filtersSlot.querySelector('#f-views'))filtersSlot.querySelector('#f-views').value=String(filters.minViews);
    if(filtersSlot.querySelector('#f-ads'))filtersSlot.querySelector('#f-ads').value=String(filters.minAds);

    const bind=(id,fn)=>filtersSlot.querySelector(id)?.addEventListener('change',e=>{fn(e.target.value);resultsPage=1;renderSearchResults()});
    bind('#f-src',v=>{filters.source=v;renderFiltersBar()});
    bind('#f-sold',v=>{filters.minSold=Number(v)||0});
    bind('#f-likes',v=>{filters.minLikes=Number(v)||0});
    bind('#f-views',v=>{filters.minViews=Number(v)||0});
    bind('#f-ads',v=>{filters.minAds=Number(v)||1});
    bind('#f-sort',v=>{filters.sort=v});
    filtersSlot.querySelector('#f-text')?.addEventListener('input',e=>{filters.text=e.target.value;resultsPage=1;renderSearchResults()});
  }

  function renderVault(){
    const pgBox=root.querySelector('#sp-saved-pages');
    const mdBox=root.querySelector('#sp-saved-media');
    const pages=savedDownloads.pages||[];
    const media=savedDownloads.media||[];
    root.querySelector('#sp-vault-details summary').textContent=`Arquivos salvos (${pages.length} páginas · ${media.length} mídias)`;

    pgBox.innerHTML=pages.length?pages.slice(0,10).map(p=>`
      <div class="row" style="justify-content:space-between;padding:5px 0;border-bottom:1px solid #162028">
        <span class="mono" style="font-size:11px;overflow:hidden;text-overflow:ellipsis">${html(p.name)}</span>
        <div class="row">
          <button type="button" class="sp-icon-btn sp-pg-edit" data-fname="${html(p.name)}" data-url="${html(p.download_url)}">${ico('edit')} Editar</button>
          <button type="button" class="sp-icon-btn sp-pg-gpt" data-fname="${html(p.name)}">${ico('sparkles')} GPT</button>
        </div>
      </div>`).join(''):'<p class="hint">Nenhuma página clonada ainda.</p>';

    mdBox.innerHTML=media.length?media.slice(0,10).map(m=>`
      <div class="row" style="justify-content:space-between;padding:5px 0;border-bottom:1px solid #162028">
        <span class="mono" style="font-size:11px;overflow:hidden;text-overflow:ellipsis">${html(m.name)}</span>
        <div class="row">
          ${m.media_type==='video'?`<button type="button" class="sp-icon-btn sp-md-tr" data-name="${html(m.name)}" data-path="${html(m.local_path)}" data-tr="${html(m.transcript||'')}">${ico('mic')} ${m.transcript?'Ver texto':'Transcrever'}</button>`:''}
          <button type="button" class="sp-icon-btn sp-md-edit" data-path="${html(m.local_path)}">${ico('edit')}</button>
        </div>
      </div>`).join(''):'<p class="hint">Nenhuma mídia baixada ainda.</p>';

    pgBox.querySelectorAll('.sp-pg-edit').forEach(b=>b.addEventListener('click',()=>openPageEditorModal(root,{filename:b.dataset.fname,download_url:b.dataset.url},async()=>{savedDownloads=await api('/api/spy/downloads');renderVault()})));
    pgBox.querySelectorAll('.sp-pg-gpt').forEach(b=>b.addEventListener('click',()=>openOfferGptDialog(root,{page_filename:b.dataset.fname})));
    mdBox.querySelectorAll('.sp-md-edit').forEach(b=>b.addEventListener('click',()=>{store.editorTargetPath=b.dataset.path;go('editar')}));
    mdBox.querySelectorAll('.sp-md-tr').forEach(b=>b.addEventListener('click',async()=>{
      if(b.dataset.tr){openTranscriptDialog({name:b.dataset.name,local_path:b.dataset.path,text:b.dataset.tr});return}
      b.disabled=true;
      try{
        const res=await api('/api/spy/download-transcribe',{method:'POST',body:{local_filename:b.dataset.name}});
        savedDownloads=await api('/api/spy/downloads');renderVault();
        openTranscriptDialog(res);
      }catch(err){toast(err.message);b.disabled=false}
    }));
  }

  function cardMetricBadge(it){
    if(it.sold_text)return it.sold_text;
    if(it.likes>=1000)return `♥ ${fmtNum(it.likes)}`;
    if(it.views>=1000)return `▶ ${fmtNum(it.views)}`;
    if(it.ad_count>1)return `${it.ad_count} anúncios`;
    if(it.price_text)return it.price_text;
    return '';
  }

  function renderCardHtml(it){
    const isPlaying=activeStream&&activeStream.id===it.id;
    const metric=cardMetricBadge(it);
    const hasVideo=it.media_type==='video'||Boolean(it.video_url);
    const dl=downloadedMap.get(it.id);
    const isMarketplace=['aliexpress','amazon','mercadolivre','shopee'].includes(it.source);
    const canCompetitor=!isMarketplace&&Boolean(it.page_id||(it.author&&it.author!=='Anunciante Meta'&&it.author!=='TikTok'&&it.author!=='Instagram')||it.domain);
    const priceLine=it.price_text?`<strong>${html(it.price_text)}</strong>`:'';
    const prodMeta=[it.sold_text||'',it.rating?`★ ${it.rating}`:'',it.category_label||''].filter(Boolean);
    const subParts=isMarketplace
      ? (prodMeta.length?prodMeta:[it.author||it.domain||'Produto'])
      : [it.author||it.domain||'',it.likes!=null?`♥ ${fmtNum(it.likes)}`:null,it.views!=null?`👁 ${fmtNum(it.views)}`:null].filter(Boolean);

    return `
      <article class="sp-card-light" data-id="${html(it.id)}">
        <div>
          ${isPlaying?`
            <div class="sp-inline-video-wrap">
              <button type="button" class="sp-inline-close" data-act="close-video" data-id="${html(it.id)}">✕ Fechar</button>
              <video controls autoplay playsinline src="${html(activeStream.url)}"></video>
            </div>
          `:`
            <div class="sp-card-thumb-wrap">
              ${it.thumbnail?`<img src="${html(it.thumbnail)}" alt="" loading="lazy">`:`<div class="empty">${html(it.source_label)}</div>`}
              <span class="sp-card-source-tag">${html(it.source_label||it.source)}</span>
              ${metric?`<span class="sp-card-metric-tag">${html(metric)}</span>`:''}
            </div>
          `}
          <div class="sp-card-body">
            <div class="sp-card-title">${html(it.title||it.text||'Criativo')}</div>
            <div class="sp-card-sub">
              <span>${html(subParts.join(' · ')||'—')}</span>
              ${priceLine}
            </div>
          </div>
        </div>
        <div class="sp-icon-bar">
          ${isMarketplace||it.media_type==='product'?`<button type="button" class="sp-icon-btn" title="Ver gráfico de vendas" data-act="chart" data-id="${html(it.id)}">📊 Gráfico</button>`:''}
          ${hasVideo?`<button type="button" class="sp-icon-btn" title="Ver vídeo (1 por vez)" data-act="play" data-id="${html(it.id)}">${ico('play')} Vídeo</button>`:''}
          ${hasVideo?`<button type="button" class="sp-icon-btn" title="Baixar e Transcrever (Whisper)" data-act="transcribe" data-id="${html(it.id)}">${ico('download')}+${ico('mic')}</button>`:''}
          ${canCompetitor?`<button type="button" class="sp-icon-btn" title="Pesquisar só este concorrente" data-act="comp" data-id="${html(it.id)}">${ico('target')}</button>`:''}
          ${(it.landing_url||it.url)?`<button type="button" class="sp-icon-btn" title="Clonar página e editar" data-act="clone" data-id="${html(it.id)}">${ico('copy')} Clonar</button>`:''}
          ${dl?.local_path?`<button type="button" class="sp-icon-btn primary" title="Abrir no Editor" data-act="edit" data-id="${html(it.id)}">${ico('edit')}</button>`:''}
          <a class="button sp-icon-btn" title="Abrir link original" target="_blank" rel="noopener" href="${html(it.landing_url||it.url)}">${ico('ext')}</a>
        </div>
      </article>`;
  }

  function wireCards(container,items){
    const byId=new Map(items.map(i=>[i.id,i]));
    container.querySelectorAll('[data-act]').forEach(btn=>{
      btn.addEventListener('click',async()=>{
        const it=byId.get(btn.dataset.id);
        const act=btn.dataset.act;
        if(act==='close-video'){
          activeStream=null;
          if(activeMode==='trending')renderTrendingBody();else renderSearchResults();
          return;
        }
        if(!it)return;
        if(act==='chart'){openSalesChartDialog(it);return}
        if(act==='play'){
          const cached=downloadedMap.get(it.id);
          if(cached?.media_url){
            activeStream={id:it.id,url:cached.media_url};
            if(activeMode==='trending')renderTrendingBody();else renderSearchResults();
            return;
          }
          btn.disabled=true;
          try{
            const res=await api('/api/spy/resolve-stream',{method:'POST',body:{url:it.video_url||it.url}});
            // Strictly keep ONLY ONE active stream open at a time
            activeStream={id:it.id,url:res.stream_url};
            if(activeMode==='trending')renderTrendingBody();else renderSearchResults();
          }catch(err){toast(err.message);btn.disabled=false}
        }else if(act==='transcribe'){
          btn.disabled=true;
          const prevHtml=btn.innerHTML;
          btn.textContent='Baixando + Whisper…';
          try{
            const res=await api('/api/spy/download-transcribe',{method:'POST',body:{url:it.url,video_url:it.video_url,title:it.title,media_type:'video'}});
            downloadedMap.set(it.id,res);
            activeStream={id:it.id,url:res.media_url};
            savedDownloads=await api('/api/spy/downloads');renderVault();
            renderSearchResults();
            openTranscriptDialog(res);
          }catch(err){toast(err.message);btn.disabled=false;btn.innerHTML=prevHtml}
        }else if(act==='comp'){
          competitorScope={
            label:it.author||it.domain||it.page_id,
            page_id:it.page_id||'',
            author:it.author||'',
            domain:it.domain||'',
          };
          if(activeMode==='trending')activeMode='creatives';
          root.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('primary',b.dataset.mode===activeMode));
          renderSourcePills();
          renderCompetitorBar();
          await runSearch(qInput.value.trim()||competitorScope.label,{competitor:competitorScope});
        }else if(act==='clone'){
          const target=it.landing_url||it.url;
          btn.disabled=true;
          try{
            const cap=await api('/api/spy/capture',{method:'POST',body:{url:target}});
            savedDownloads=await api('/api/spy/downloads');renderVault();
            openPageEditorModal(root,cap,async()=>{savedDownloads=await api('/api/spy/downloads');renderVault()});
          }catch(err){toast(err.message);btn.disabled=false}
        }else if(act==='edit'){
          const dl=downloadedMap.get(it.id);
          if(dl?.local_path){store.editorTargetPath=dl.local_path;go('editar')}
        }
      });
    });
  }

  function renderPagerTabs(totalItems,curPage,cls){
    const totalPages=Math.max(1,Math.ceil(totalItems/PAGE_SIZE));
    if(totalPages<=1)return '';
    const btns=Array.from({length:totalPages},(_,i)=>{
      const p=i+1;
      const start=(p-1)*PAGE_SIZE+1;
      const end=Math.min(totalItems,p*PAGE_SIZE);
      return `<button type="button" class="sp-source-pill ${cls} ${p===curPage?'is-on':''}" data-page="${p}">Guia ${p} (${start}–${end})</button>`;
    }).join('');
    return `<div class="sp-kw-bar" style="margin:10px 0">${btns}</div>`;
  }

  function renderTrendingBody(){
    const tr=cachedTrending||{keywords:[],products:[],searches:[],hot_products:[],hot_videos:[]};
    const kws=(tr.keywords||[]).join(', ');
    const hotAll=tr.hot_products||[];
    const hotVids=tr.hot_videos||[];
    const totalPages=Math.max(1,Math.ceil(hotAll.length/PAGE_SIZE));
    if(trendingPage>totalPages)trendingPage=1;
    const hotSlice=hotAll.slice((trendingPage-1)*PAGE_SIZE,trendingPage*PAGE_SIZE);
    const pager=renderPagerTabs(hotAll.length,trendingPage,'tr-page-btn');
    const ecomCats=CAT_CATALOG.ecommerce;
    const darkCats=CAT_CATALOG.youtube_dark;

    mainStage.innerHTML=`
      <div class="panel" style="padding:14px 16px;margin-bottom:14px">
        <div class="panel-head" style="margin-bottom:8px;flex-wrap:wrap;gap:8px">
          <div>
            <h3>${ico('flame')} Em Alta por Categorias &amp; Palavras-Chave</h3>
            <small class="hint">Selecione múltiplas categorias de E-commerce e/ou YouTube Dark para filtrar produtos, vídeos virais e tendências.</small>
          </div>
          <div class="row" style="gap:6px">
            <button type="button" class="sp-source-pill tr-track-btn ${trendingTrack==='all'?'is-on':''}" data-track="all">🔥 Tudo</button>
            <button type="button" class="sp-source-pill tr-track-btn ${trendingTrack==='ecommerce'?'is-on':''}" data-track="ecommerce">🛒 E-commerce</button>
            <button type="button" class="sp-source-pill tr-track-btn ${trendingTrack==='youtube_dark'?'is-on':''}" data-track="youtube_dark">🖤 YouTube Dark</button>
          </div>
        </div>

        ${trendingTrack!=='youtube_dark'?`
          <div class="sp-kw-bar" style="margin-bottom:8px">
            <small class="hint" style="min-width:92px">🛒 E-commerce:</small>
            ${ecomCats.map(c=>`<button type="button" class="sp-source-pill tr-cat-btn ${selectedCategories.has(c.id)?'is-on':''}" data-cat="${html(c.id)}">${html(c.label)}</button>`).join('')}
          </div>`:''}

        ${trendingTrack!=='ecommerce'?`
          <div class="sp-kw-bar" style="margin-bottom:10px">
            <small class="hint" style="min-width:92px">🖤 YT Dark:</small>
            ${darkCats.map(c=>`<button type="button" class="sp-source-pill tr-cat-btn ${selectedCategories.has(c.id)?'is-on':''}" data-cat="${html(c.id)}">${html(c.label)}</button>`).join('')}
          </div>`:''}

        <div class="sp-search-shell">
          <input id="tr-kw-input" value="${html(kws)}" placeholder="Palavras-chave extras: natal, skincare, organizador, cozinha">
          <button type="button" id="tr-kw-save" class="primary">Aplicar Filtros &amp; Atualizar Em Alta</button>
        </div>
        <div class="sp-kw-bar" style="margin-top:10px">
          ${(tr.searches||[]).slice(0,10).map(t=>`<button type="button" class="sp-source-pill tr-chip" data-q="${html(t)}">📈 ${html(t)}</button>`).join('')}
          ${(tr.products||[]).slice(0,10).map(t=>`<button type="button" class="sp-source-pill tr-chip" data-q="${html(t)}">🔥 ${html(t)}</button>`).join('')}
        </div>
      </div>

      ${trendingTrack!=='youtube_dark'&&hotAll.length?`
        <div class="panel-head" style="margin-bottom:6px">
          <h3>${ico('bag')} Produtos mais vendidos em alta (${hotAll.length})</h3>
          <span class="hint">Guia ${trendingPage} de ${totalPages} · clique em 📊 Gráfico para abrir o histórico de vendas</span>
        </div>
        ${pager}
        <div class="sp-cards-clean" id="tr-hot-grid">
          ${hotSlice.map(p=>renderCardHtml(p)).join('')}
        </div>
        ${pager}
      `:!cachedTrending?'<div class="panel"><div class="empty">Carregando produtos, tendências e vídeos em alta…</div></div>':trendingTrack==='ecommerce'?'<div class="panel"><div class="empty">Nenhum produto carregado ainda.</div></div>':''}

      ${trendingTrack!=='ecommerce'&&hotVids.length?`
        <div class="panel-head" style="margin:18px 0 8px">
          <h3>${ico('video')} Vídeos / Canais Dark em alta (${hotVids.length})</h3>
        </div>
        <div class="sp-cards-clean" id="tr-dark-grid">
          ${hotVids.slice(0,16).map(v=>renderCardHtml(v)).join('')}
        </div>
      `:''}`;

    async function refreshTrending(){
      const nextKw=mainStage.querySelector('#tr-kw-input')?.value.trim()??'';
      const btn=mainStage.querySelector('#tr-kw-save');
      if(btn){btn.disabled=true;btn.textContent='Atualizando…'}
      try{
        const cats=[...selectedCategories].join(',');
        cachedTrending=await api(`/api/spy/trending?country=${encodeURIComponent(countrySelect.value)}&keywords=${encodeURIComponent(nextKw)}&track=${encodeURIComponent(trendingTrack)}&categories=${encodeURIComponent(cats)}`);
        trendingPage=1;
        renderTrendingBody();
      }catch(err){toast(err.message);if(btn){btn.disabled=false;btn.textContent='Aplicar Filtros & Atualizar Em Alta'}}
    }

    mainStage.querySelector('#tr-kw-save').addEventListener('click',refreshTrending);
    mainStage.querySelectorAll('.tr-track-btn').forEach(btn=>btn.addEventListener('click',()=>{
      trendingTrack=btn.dataset.track;
      renderTrendingBody();
    }));
    mainStage.querySelectorAll('.tr-cat-btn').forEach(btn=>btn.addEventListener('click',()=>{
      const id=btn.dataset.cat;
      if(selectedCategories.has(id))selectedCategories.delete(id);
      else selectedCategories.add(id);
      btn.classList.toggle('is-on',selectedCategories.has(id));
    }));
    mainStage.querySelectorAll('.tr-page-btn').forEach(btn=>btn.addEventListener('click',()=>{
      trendingPage=Number(btn.dataset.page)||1;
      renderTrendingBody();
    }));
    mainStage.querySelectorAll('.tr-chip').forEach(btn=>btn.addEventListener('click',()=>{
      qInput.value=btn.dataset.q;
      activeMode='creatives';
      root.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('primary',b.dataset.mode===activeMode));
      renderSourcePills();
      runSearch(btn.dataset.q);
    }));
    const hotGrid=mainStage.querySelector('#tr-hot-grid');
    if(hotGrid)wireCards(hotGrid,hotSlice);
    const darkGrid=mainStage.querySelector('#tr-dark-grid');
    if(darkGrid)wireCards(darkGrid,hotVids.slice(0,16));
  }

  async function renderTrendingHome(){
    renderTrendingBody();
    if(!cachedTrending){
      const target=mainStage.querySelector('#tr-hot-grid')||mainStage;
      try{
        const cats=[...selectedCategories].join(',');
        cachedTrending=await api(`/api/spy/trending?country=${encodeURIComponent(countrySelect.value)}&track=${encodeURIComponent(trendingTrack)}&categories=${encodeURIComponent(cats)}`);
      }catch{cachedTrending={keywords:[],products:[],searches:[],hot_products:[],hot_videos:[]}}
      if(root.isConnected&&activeMode==='trending')renderTrendingBody();
    }
  }

  function renderSearchResults(){
    if(!lastSearch){mainStage.innerHTML='<div class="panel"><div class="empty">Faça uma busca acima.</div></div>';return}
    const items=filteredItems();
    const totalPages=Math.max(1,Math.ceil(items.length/PAGE_SIZE));
    if(resultsPage>totalPages)resultsPage=1;
    const slice=items.slice((resultsPage-1)*PAGE_SIZE,resultsPage*PAGE_SIZE);
    const pager=renderPagerTabs(items.length,resultsPage,'sp-res-page-btn');
    const srcStatus=Object.entries(lastSearch.sources||{}).map(([k,v])=>`<span class="badge ${v.ok?'good':'warn'}">${html(k)}: ${v.count??(v.ok?'OK':'—')}</span>`).join(' ');
    const clusters=lastSearch.clusters||[];

    mainStage.innerHTML=`
      <div class="row" style="justify-content:space-between;margin-bottom:10px;flex-wrap:wrap">
        <div class="row" style="flex-wrap:wrap">${srcStatus}</div>
        <span class="hint">${items.length} de ${lastSearch.total} item(ns) · Guia ${resultsPage}/${totalPages}</span>
      </div>
      ${clusters.length?`
        <div class="sp-kw-bar" style="margin-bottom:12px">
          <small class="hint">Concorrentes recorrentes:</small>
          ${clusters.slice(0,5).map(c=>`
            <button type="button" class="sp-source-pill cl-comp" data-lbl="${html(c.label)}" data-kind="${html(c.kind)}" data-pid="${html(c.page_id||'')}">
              ${ico('target')} ${html(c.label)} (${c.count})
            </button>`).join('')}
        </div>`:''}
      ${pager}
      <div class="sp-cards-clean" id="sp-results-grid">
        ${slice.length?slice.map(renderCardHtml).join(''):'<div class="empty">Nenhum item corresponde aos filtros selecionados. Reduza o filtro mínimo para ver mais resultados.</div>'}
      </div>
      ${pager}`;

    mainStage.querySelectorAll('.sp-res-page-btn').forEach(btn=>btn.addEventListener('click',()=>{
      resultsPage=Number(btn.dataset.page)||1;
      renderSearchResults();
    }));

    mainStage.querySelectorAll('.cl-comp').forEach(btn=>btn.addEventListener('click',()=>{
      competitorScope={
        label:btn.dataset.lbl,
        page_id:btn.dataset.pid||'',
        author:btn.dataset.kind==='author'?btn.dataset.lbl:'',
        domain:btn.dataset.kind==='domain'?btn.dataset.lbl:'',
      };
      renderCompetitorBar();
      runSearch(qInput.value.trim()||competitorScope.label,{competitor:competitorScope});
    }));

    const grid=mainStage.querySelector('#sp-results-grid');
    wireCards(grid,slice);
  }

  async function runSearch(query,opts={}){
    const btn=root.querySelector('#sp-search-btn');
    btn.disabled=true;
    btn.textContent='Buscando…';
    if(activeMode==='trending'){
      activeMode='creatives';
      root.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('primary',b.dataset.mode===activeMode));
      renderSourcePills();
    }
    mainStage.innerHTML='<div class="panel"><div class="empty">Buscando nas fontes selecionadas…</div></div>';
    try{
      const srcs=[...(activeMode==='products'?productSources:creativeSources)];
      const body={
        query,
        country:countrySelect.value,
        limit:activeMode==='products'?96:28,
        mode:activeMode,
        sources:srcs,
      };
      if(opts.competitor){
        if(opts.competitor.page_id)body.competitor_page_id=opts.competitor.page_id;
        if(opts.competitor.author)body.competitor_author=opts.competitor.author;
        if(opts.competitor.domain)body.competitor_domain=opts.competitor.domain;
      }
      lastSearch=await api('/api/spy/search',{method:'POST',body});
      resultsPage=1;
      activeStream=null;
      renderFiltersBar();
      renderSearchResults();
    }catch(err){
      toast(err.message);
      mainStage.innerHTML=`<div class="notice bad">${html(err.message)}</div>`;
    }finally{
      btn.disabled=false;
      btn.innerHTML=`${ico('search')} Pesquisar`;
    }
  }

  root.querySelector('#sp-search-form').addEventListener('submit',ev=>{
    ev.preventDefault();
    const q=qInput.value.trim();
    if(!q){toast('Digite um termo para pesquisar.');return}
    competitorScope=null;
    renderCompetitorBar();
    runSearch(q);
  });

  root.querySelector('#sp-clone-direct-btn').addEventListener('click',async()=>{
    const url=root.querySelector('#sp-clone-direct-url').value.trim();
    if(!url){toast('Cole a URL da página para clonar.');return}
    try{
      const cap=await api('/api/spy/capture',{method:'POST',body:{url}});
      savedDownloads=await api('/api/spy/downloads');renderVault();
      openPageEditorModal(root,cap,async()=>{savedDownloads=await api('/api/spy/downloads');renderVault()});
    }catch(err){toast(err.message)}
  });

  renderSourcePills();
  renderCompetitorBar();
  renderVault();
  if(activeMode==='trending'){
    renderFiltersBar();
    renderTrendingHome();
  }else if(lastSearch){
    renderFiltersBar();
    renderSearchResults();
  }
}
