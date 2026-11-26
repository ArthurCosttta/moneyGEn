import{api,html,refreshBase,store,toast}from'../state.js';

let activeTab='manager';
let level='campaigns';
let selectedCampaign=null;
let selectedAdset=null;
let datePreset='last_7d';
let cachedAccounts=[];
let cachedRows=[];
let cachedResources={pixels:[],pages:[]};

function fmtMoney(v){
  return Number(v||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
}

function fmtNum(v){
  return Number(v||0).toLocaleString('pt-BR');
}

export function openMetaLoginModal(root,onDone){
  const dialog=document.createElement('dialog');
  dialog.innerHTML=`
    <form class="dialog-body">
      <h2>Conectar / Atualizar Token Meta Ads</h2>
      <p class="lead">Tokens do Graph API Explorer expiram em ~1–2 horas. Para não expirar nunca, gere um <strong>Token de Usuário do Sistema (Permanente)</strong> no Gerenciador de Negócios.</p>
      <div class="notice">
        <strong>Token Permanente (recomendado):</strong><br>
        1. Acesse <em>business.facebook.com/settings/system-users</em>.<br>
        2. Crie/abra um Usuário do Sistema → adicione Contas de Anúncio + Páginas + Pixels.<br>
        3. Clique em <strong>Gerar novo token</strong> com validade <strong>Nunca expira</strong> e permissões <code>ads_read</code>, <code>ads_management</code>, <code>pages_show_list</code>, <code>pages_read_engagement</code>.<br>
        <small class="hint">Ou gere um rápido em <em>developers.facebook.com/tools/explorer</em>.</small>
      </div>
      <label for="ma-token-input">Access Token da Meta</label>
      <input id="ma-token-input" type="password" autocomplete="off" placeholder="EAA..." required>
      <label for="ma-acc-opt">Conta padrão (opcional, ex.: act_123456)</label>
      <input id="ma-acc-opt" type="text" placeholder="Se deixar vazio, seleciona a primeira conta">
      <details style="margin:6px 0">
        <summary class="hint">Estender token curto do Explorer para 60 dias (opcional)</summary>
        <div class="settings" style="margin-top:8px">
          <label>App ID<input id="ma-app-id" type="text" placeholder="ID do App Meta"></label>
          <label>App Secret<input id="ma-app-secret" type="password" placeholder="Chave secreta do App"></label>
        </div>
      </details>
      <div id="ma-login-err"></div>
      <div class="button-row">
        <button type="button" class="cancel">Cancelar</button>
        <button type="submit" class="primary">Validar e conectar</button>
      </div>
    </form>`;
  root.append(dialog);
  dialog.showModal();
  dialog.querySelector('.cancel').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>dialog.remove());

  dialog.querySelector('form').addEventListener('submit',async ev=>{
    ev.preventDefault();
    const btn=dialog.querySelector('[type=submit]');
    const errBox=dialog.querySelector('#ma-login-err');
    errBox.replaceChildren();
    btn.disabled=true;
    btn.textContent='Verificando token…';
    try{
      const token=dialog.querySelector('#ma-token-input').value.trim();
      const accountId=dialog.querySelector('#ma-acc-opt').value.trim()||null;
      const appId=dialog.querySelector('#ma-app-id').value.trim()||null;
      const appSecret=dialog.querySelector('#ma-app-secret').value.trim()||null;
      await api('/api/meta-ads/login',{method:'POST',body:{token,account_id:accountId,app_id:appId,app_secret:appSecret}});
      toast('Meta Ads conectada com sucesso.');
      dialog.close();
      if(onDone)await onDone();
    }catch(err){
      errBox.innerHTML=`<div class="notice bad">${html(err.message)}</div>`;
      btn.disabled=false;
      btn.textContent='Validar e conectar';
    }
  });
}

export async function renderMetaAds(root){
  const status=store.state?.meta_ads||await api('/api/meta-ads/status');
  if(!status.cli_installed){
    root.innerHTML=`
      <section class="page-head"><div><p class="kicker">05 · meta ads</p><h1>Meta Ads</h1></div></section>
      <div class="notice bad">CLI <code>meta-ads</code> não encontrada no sistema. Execute <code>npm install -g meta-ads</code> e reinicie o servidor.</div>`;
    return;
  }

  if(!status.connected){
    root.innerHTML=`
      <section class="page-head">
        <div>
          <p class="kicker">05 · meta ads</p>
          <h1>Entrar na Meta Ads.</h1>
          <p class="lead">Conecte seu token com <code>ads_read</code> e <code>ads_management</code> para consultar suas campanhas reais e subir anúncios pausados.</p>
        </div>
      </section>
      <div class="panel">
        <div class="panel-head"><h2>Conexão da conta</h2><span class="badge warn">Desconectado</span></div>
        <p class="hint">O token fica salvo apenas localmente na configuração da CLI (<code>~/.config/meta-ads-cli/config.json</code>) e nunca é exibido depois de salvo.</p>
        <div class="button-row">
          <button id="ma-open-login" class="primary" type="button">Entrar na Meta Ads →</button>
        </div>
      </div>`;
    root.querySelector('#ma-open-login').addEventListener('click',()=>openMetaLoginModal(root,async()=>{await refreshBase();await renderMetaAds(root)}));
    return;
  }

  if(store.metaDraft){
    activeTab='upload';
  }

  root.innerHTML=`
    <section class="page-head">
      <div>
        <p class="kicker">05 · meta ads</p>
        <h1>Meta Ads.</h1>
        <p class="lead">Veja campanhas, conjuntos e anúncios da conta conectada ou suba um criativo real sempre em estado PAUSED.</p>
      </div>
      <div class="button-row">
        <button id="ma-tab-manager" type="button" class="${activeTab==='manager'?'primary':''}">Gerenciador</button>
        <button id="ma-tab-upload" type="button" class="${activeTab==='upload'?'primary':''}">+ Subir Anúncio</button>
        <button id="ma-tab-pixels" type="button" class="${activeTab==='pixels'?'primary':''}">Pixels</button>
        <button id="ma-reauth" type="button" class="quiet">Trocar Token</button>
        <button id="ma-logout" type="button" class="quiet danger">Sair do Meta</button>
      </div>
    </section>

    <div class="panel ma-account-bar">
      <div class="row grow">
        <label class="ma-inline-label">Conta
          <select id="ma-account-select"></select>
        </label>
        <label class="ma-inline-label">Período
          <select id="ma-preset-select">
            <option value="today">Hoje</option>
            <option value="yesterday">Ontem</option>
            <option value="last_7d">Últimos 7 dias</option>
            <option value="last_30d">Últimos 30 dias</option>
          </select>
        </label>
        <button id="ma-refresh" type="button">↻ Atualizar</button>
      </div>
    </div>

    <div id="ma-body"></div>`;

  const accSelect=root.querySelector('#ma-account-select');
  const presetSelect=root.querySelector('#ma-preset-select');
  presetSelect.value=datePreset;

  let accountsWarning='';

  async function ensureAccounts(){
    if(!cachedAccounts.length){
      try{
        const res=await api('/api/meta-ads/accounts');
        cachedAccounts=res.items||[];
        accountsWarning=res.warning||'';
      }catch(err){
        toast(err.message);
      }
    }
    accSelect.replaceChildren();
    const cur=(cachedAccounts.some(a=>a.id===status.account_id)?status.account_id:cachedAccounts[0]?.id)||status.account_id||'';
    for(const acc of cachedAccounts){
      const opt=document.createElement('option');
      opt.value=acc.id;
      opt.textContent=`${acc.name||acc.id} (${acc.id})`;
      opt.selected=acc.id===cur;
      accSelect.append(opt);
    }
    if(!cachedAccounts.length&&cur){
      const opt=document.createElement('option');
      opt.value=cur;
      opt.textContent=cur;
      opt.selected=true;
      accSelect.append(opt);
    }
  }

  let accountSummary=null;

  async function loadManager(){
    const body=root.querySelector('#ma-body');
    const acc=accSelect.value||status.account_id;
    if(!acc||(!cachedAccounts.length&&accountsWarning)){
      body.innerHTML=`
        <div class="notice bad">
          <strong>Conta de anúncios não vinculada a este token no Business Manager</strong><br>
          <span>${html(accountsWarning||'Nenhuma conta de anúncios disponível em /me/adaccounts para este token.')}</span>
          <div class="button-row" style="margin-top:10px">
            <button id="ma-recheck-assets" type="button" class="primary">↻ Já atribuí os ativos — Verificar</button>
            <button id="ma-fix-token-empty" type="button">Trocar Token</button>
          </div>
        </div>`;
      body.querySelector('#ma-recheck-assets').addEventListener('click',async()=>{cachedAccounts=[];accountsWarning='';await renderMetaAds(root)});
      body.querySelector('#ma-fix-token-empty').addEventListener('click',()=>openMetaLoginModal(root,async()=>{cachedAccounts=[];accountsWarning='';await refreshBase();await renderMetaAds(root)}));
      return;
    }
    body.innerHTML='<div class="panel"><div class="empty">Consultando Meta Ads…</div></div>';
    try{
      let res;
      const sumPromise=api(`/api/meta-ads/summary?account_id=${encodeURIComponent(acc)}&date_preset=${encodeURIComponent(datePreset)}`).catch(()=>null);
      if(level==='campaigns'){
        res=await api(`/api/meta-ads/campaigns?account_id=${encodeURIComponent(acc)}&date_preset=${encodeURIComponent(datePreset)}`);
      }else if(level==='adsets'){
        const campParam=selectedCampaign?`&campaign_id=${encodeURIComponent(selectedCampaign.id)}`:'';
        res=await api(`/api/meta-ads/adsets?account_id=${encodeURIComponent(acc)}${campParam}&date_preset=${encodeURIComponent(datePreset)}`);
      }else{
        const filterParam=selectedAdset?`&adset_id=${encodeURIComponent(selectedAdset.id)}`:selectedCampaign?`&campaign_id=${encodeURIComponent(selectedCampaign.id)}`:'';
        res=await api(`/api/meta-ads/ads?account_id=${encodeURIComponent(acc)}${filterParam}&date_preset=${encodeURIComponent(datePreset)}`);
      }
      accountSummary=await sumPromise;
      cachedRows=res.items||[];
      renderManager(acc);
    }catch(err){
      const isAuth=err.code==='META_AUTH_REQUIRED'||err.status===401||/expired|oauth/i.test(err.message||'');
      const isAsset=err.code==='META_ASSET_PERMISSION'||err.status===403||/\(#200\)|has NOT grant/i.test(err.message||'');
      if(isAuth||isAsset){
        body.innerHTML=`
          <div class="notice bad">
            <strong>${isAsset?'Falta atribuir a Conta de Anúncios ao Usuário do Sistema no Business Manager.':'Sessão Meta expirada ou inválida.'}</strong><br>
            <span>${html(err.message)}</span>
            <div class="button-row" style="margin-top:10px">
              <button id="ma-recheck-perm" type="button">↻ Verificar novamente</button>
              <button id="ma-fix-token" type="button" class="primary">Atualizar / Trocar Token →</button>
            </div>
          </div>`;
        body.querySelector('#ma-recheck-perm').addEventListener('click',async()=>{cachedAccounts=[];await renderMetaAds(root)});
        body.querySelector('#ma-fix-token').addEventListener('click',()=>openMetaLoginModal(root,async()=>{cachedAccounts=[];await refreshBase();await renderMetaAds(root)}));
      }else{
        body.innerHTML=`<div class="notice bad">${html(err.message)}</div>`;
      }
    }
  }

  function renderManager(acc){
    const body=root.querySelector('#ma-body');
    const totals=cachedRows.reduce((accSum,r)=>{
      const ins=r.insights||{};
      accSum.spend+=Number(ins.spend||0);
      accSum.impressions+=Number(ins.impressions||0);
      accSum.clicks+=Number(ins.clicks||0);
      accSum.purchases+=Number(ins.purchases||0);
      return accSum;
    },{spend:0,impressions:0,clicks:0,purchases:0});

    const sum=accountSummary||{};
    const win=sum.winner;
    const budgetText=sum.active_daily_budget
      ? `${fmtMoney(sum.active_daily_budget)}/dia`
      : sum.active_lifetime_budget
        ? `Vitalício ${fmtMoney(sum.active_lifetime_budget)}`
        : sum.spend_cap
          ? `Limite conta ${fmtMoney(sum.spend_cap)}`
          : 'Sem orçamento ativo';
    const balanceVal=sum.funding_balance??sum.balance??0;
    const balanceNote=sum.funding_display
      ? sum.funding_display
      : `Saldo aberto: ${fmtMoney(balanceVal)} · Gasto total: ${fmtMoney(sum.amount_spent||0)}`;

    body.innerHTML=`
      <div class="ma-kpis">
        <div class="panel"><small class="hint">Orçamento / Saldo da conta</small><strong class="ma-kpi">${html(budgetText)}</strong><small class="hint">${html(balanceNote)}</small></div>
        <div class="panel"><small class="hint">Gasto hoje</small><strong class="ma-kpi">${fmtMoney(sum.today_spend||0)}</strong><small class="hint">${datePreset}: ${fmtMoney(sum.period_spend??totals.spend)}</small></div>
        <div class="panel"><small class="hint">ROI / ROAS (${datePreset})</small><strong class="ma-kpi">${sum.roas?`${sum.roas}x (${sum.roi_pct}%)`:'0.0x'}</strong><small class="hint">${fmtNum(sum.purchases??totals.purchases)} compra(s) · ${fmtNum(totals.clicks)} cliques</small></div>
        <div class="panel"><small class="hint">Criativo vencedor</small><strong class="ma-kpi ma-winner-name">${win?html(win.name):'—'}</strong><small class="hint">${win?`${win.status} · ${win.insights?.purchases||0} compra(s) · ${win.insights?.clicks||0} cl.`:'Sem dados no período'}</small></div>
      </div>

      <div class="ma-levels">
        <div class="row">
          <button type="button" class="${level==='campaigns'?'primary':''}" data-lvl="campaigns">Campanhas</button>
          <button type="button" class="${level==='adsets'?'primary':''}" data-lvl="adsets">Conjuntos</button>
          <button type="button" class="${level==='ads'?'primary':''}" data-lvl="ads">Anúncios</button>
        </div>
        <div class="ma-crumb mono hint">
          <button type="button" class="quiet" id="ma-crumb-root">${html(acc)}</button>
          ${selectedCampaign?` › <button type="button" class="quiet" id="ma-crumb-camp">${html(selectedCampaign.name)}</button>`:''}
          ${selectedAdset?` › <span>${html(selectedAdset.name)}</span>`:''}
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h2>${level==='campaigns'?'Campanhas':level==='adsets'?'Conjuntos de anúncios':'Anúncios'}</h2>
          <span class="counter">${cachedRows.length}</span>
        </div>
        <div class="ma-table-wrap">
          ${cachedRows.length?`
            <table class="ma-table">
              <thead><tr><th>Nome</th><th>Status</th><th>Orç./dia</th><th>Gasto</th><th>Cliques</th><th>Compras</th><th>Ações</th></tr></thead>
              <tbody id="ma-rows"></tbody>
            </table>
          `:'<div class="empty">Nenhum item encontrado neste nível.</div>'}
        </div>
      </div>`;

    body.querySelectorAll('[data-lvl]').forEach(b=>b.addEventListener('click',()=>{
      level=b.dataset.lvl;
      if(level==='campaigns'){selectedCampaign=null;selectedAdset=null}
      if(level==='adsets'){selectedAdset=null}
      loadManager();
    }));
    body.querySelector('#ma-crumb-root')?.addEventListener('click',()=>{selectedCampaign=null;selectedAdset=null;level='campaigns';loadManager()});
    body.querySelector('#ma-crumb-camp')?.addEventListener('click',()=>{selectedAdset=null;level='adsets';loadManager()});

    const tbody=body.querySelector('#ma-rows');
    if(!tbody)return;
    for(const row of cachedRows){
      const ins=row.insights||{};
      const st=String(row.status||row.effective_status||'PAUSED');
      const budget=row.daily_budget?fmtMoney(Number(row.daily_budget)/100):'—';
      const tr=document.createElement('tr');
      tr.innerHTML=`
        <td>
          <button type="button" class="quiet ma-drill">${html(row.name||row.id)}</button>
          <div class="hint mono">${html(row.id)}</div>
        </td>
        <td><span class="badge ${st==='ACTIVE'?'good':st==='PAUSED'?'warn':''}">${html(st)}</span></td>
        <td class="mono">${budget}</td>
        <td class="mono">${fmtMoney(ins.spend)}</td>
        <td class="mono">${fmtNum(ins.clicks)}</td>
        <td class="mono">${fmtNum(ins.purchases)}</td>
        <td>
          <div class="row">
            <button type="button" class="ma-toggle">${st==='ACTIVE'?'Pausar':'Ativar'}</button>
            ${level!=='ads'?`<button type="button" class="quiet ma-upload-here">+ Anúncio</button>`:''}
          </div>
        </td>`;
      tr.querySelector('.ma-drill').addEventListener('click',()=>{
        if(level==='campaigns'){selectedCampaign={id:row.id,name:row.name};selectedAdset=null;level='adsets';loadManager()}
        else if(level==='adsets'){selectedAdset={id:row.id,name:row.name};level='ads';loadManager()}
      });
      tr.querySelector('.ma-toggle').addEventListener('click',async()=>{
        const next=st==='ACTIVE'?'PAUSED':'ACTIVE';
        if(!confirm(`Alterar "${row.name}" para ${next} na sua conta real Meta Ads?`))return;
        try{
          const entityType=level==='campaigns'?'campaign':level==='adsets'?'adset':'ad';
          await api('/api/meta-ads/status',{method:'POST',body:{entity_type:entityType,entity_id:row.id,status:next,confirmed:true}});
          toast(`Status alterado para ${next}.`);
          loadManager();
        }catch(err){toast(err.message)}
      });
      tr.querySelector('.ma-upload-here')?.addEventListener('click',()=>{
        store.metaDraft={
          ...(store.metaDraft||{}),
          campaign_id:level==='campaigns'?row.id:(selectedCampaign?.id||'__new__'),
          adset_id:level==='adsets'?row.id:'__new__',
        };
        activeTab='upload';
        renderMetaAds(root);
      });
      tbody.append(tr);
    }
  }

  async function loadUpload(){
    const body=root.querySelector('#ma-body');
    const acc=accSelect.value||status.account_id;
    body.innerHTML='<div class="panel"><div class="empty">Carregando campanhas, páginas e mídias disponíveis…</div></div>';

    let camps=[],adsets=[],videos=[],libItems=[];
    try{
      const [cRes,aRes,vRes,lRes,rRes]=await Promise.all([
        api(`/api/meta-ads/campaigns?account_id=${encodeURIComponent(acc)}`).catch(()=>({items:[]})),
        api(`/api/meta-ads/adsets?account_id=${encodeURIComponent(acc)}`).catch(()=>({items:[]})),
        api('/api/editor/videos').catch(()=>({items:[]})),
        api('/api/library').catch(()=>({items:[]})),
        api(`/api/meta-ads/resources?account_id=${encodeURIComponent(acc)}`).catch(()=>({pixels:[],pages:[]})),
      ]);
      camps=cRes.items||[];
      adsets=aRes.items||[];
      videos=vRes.items||[];
      libItems=lRes.items||[];
      cachedResources=rRes;
    }catch{}

    const draft=store.metaDraft||{};
    store.metaDraft=null;

    const pagesHtml=(cachedResources.pages||[]).map(p=>`<option value="${html(p.id)}" ${p.id===status.page_id?'selected':''}>${html(p.name)} (${html(p.id)})</option>`).join('');
    const pixelsHtml=`<option value="">Sem pixel explícito</option>`+(cachedResources.pixels||[]).map(px=>`<option value="${html(px.id)}" ${px.id===status.pixel_id?'selected':''}>${html(px.name)} (${html(px.id)})</option>`).join('');

    body.innerHTML=`
      <form id="ma-upload-form" class="grid">
        <section class="panel">
          <div class="panel-head"><h2>Subir Anúncio (sempre criado PAUSED)</h2><span class="badge warn">Seguro</span></div>

          <div class="settings">
            <label>Campanha
              <select id="mu-camp">
                <option value="__new__">+ Criar nova campanha</option>
                ${camps.map(c=>`<option value="${html(c.id)}" ${c.id===draft.campaign_id?'selected':''}>${html(c.name)} (${html(c.status)})</option>`).join('')}
              </select>
            </label>
            <label>Conjunto
              <select id="mu-adset">
                <option value="__new__">+ Criar novo conjunto</option>
                ${adsets.map(s=>`<option value="${html(s.id)}" ${s.id===draft.adset_id?'selected':''}>${html(s.name)} (${html(s.status)})</option>`).join('')}
              </select>
            </label>
          </div>

          <div id="mu-new-camp-box" class="panel ed-subpanel">
            <h3>Nova campanha</h3>
            <div class="settings">
              <label>Nome<input id="mu-camp-name" value="Campanha MoneyGen"></label>
              <label>Objetivo<select id="mu-camp-obj"><option value="OUTCOME_SALES">Vendas (OUTCOME_SALES)</option><option value="OUTCOME_TRAFFIC">Tráfego (OUTCOME_TRAFFIC)</option><option value="OUTCOME_LEADS">Leads (OUTCOME_LEADS)</option><option value="OUTCOME_ENGAGEMENT">Engajamento</option></select></label>
              <label>Orçamento diário (R$)<input id="mu-camp-budget" type="number" min="5" step="1" value="50"></label>
            </div>
          </div>

          <div id="mu-new-adset-box" class="panel ed-subpanel">
            <h3>Novo conjunto</h3>
            <div class="settings">
              <label>Nome do conjunto<input id="mu-adset-name" value="Conjunto Aberto BR"></label>
              <label>País<input id="mu-adset-country" value="BR" maxlength="2"></label>
              <label>Otimização<select id="mu-adset-goal"><option value="LINK_CLICKS">LINK_CLICKS</option><option value="LANDING_PAGE_VIEWS">LANDING_PAGE_VIEWS</option><option value="CONVERSIONS">CONVERSIONS</option><option value="IMPRESSIONS">IMPRESSIONS</option></select></label>
            </div>
          </div>

          <label>Escolher mídia local (Vídeos ou Biblioteca de Imagens)</label>
          <select id="mu-media-select">
            <option value="">Selecionar arquivo abaixo ou informar caminho…</option>
            ${videos.map(v=>`<option value="path:${html(v.path)}">[Vídeo] ${html(v.relative_label||v.name)}</option>`).join('')}
            ${libItems.map(i=>`<option value="asset:${html(i.id)}">[Imagem] ${html(i.name)}</option>`).join('')}
          </select>
          <label>Ou caminho direto do arquivo (.mp4 / .png / .jpg)</label>
          <input id="mu-media-path" value="${html(draft.media_path||'')}" placeholder="C:\\...\\video.mp4">

          <div class="settings">
            <label>Página do Facebook (Page ID)
              ${pagesHtml?`<select id="mu-page-select">${pagesHtml}</select>`:`<input id="mu-page-input" value="${html(status.page_id||'')}" placeholder="ID da Página FB">`}
            </label>
            <label>Pixel
              <select id="mu-pixel">${pixelsHtml}</select>
            </label>
          </div>

          <label>Nome do anúncio<input id="mu-ad-name" value="${html(draft.ad_name||'Criativo 01')}"></label>
          <label>Texto principal<textarea id="mu-primary-text" required placeholder="Texto que aparece acima do vídeo/imagem…"></textarea></label>
          <div class="settings">
            <label>Título<input id="mu-headline" placeholder="Ex.: Frete grátis hoje"></label>
            <label>CTA<select id="mu-cta"><option value="LEARN_MORE">Saiba mais</option><option value="SHOP_NOW">Comprar agora</option><option value="SIGN_UP">Cadastre-se</option><option value="CONTACT_US">Fale conosco</option></select></label>
            <label class="wide">URL de destino<input id="mu-link" type="url" value="${html(status.default_url||'')}" required placeholder="https://seusite.com/oferta"></label>
          </div>
        </section>

        <aside class="panel ma-mockup-aside">
          <div class="panel-head">
            <h2>Mockup do Anúncio</h2>
            <div class="row">
              <button type="button" id="mu-mock-feed" class="primary">Feed FB</button>
              <button type="button" id="mu-mock-reels" class="quiet">Reels 9:16</button>
            </div>
          </div>
          <div id="mu-mockup-stage" class="ma-mock-card is-feed"></div>
          <hr class="cc-sep">
          <p class="hint">Campanha, conjunto e anúncio serão criados sempre em <strong>PAUSED</strong> para você revisar no Gerenciador antes de ativar qualquer gasto.</p>
          <label class="check"><input id="mu-confirm" type="checkbox">Entendo que isto envia a mídia e cria o anúncio pausado na conta real ${html(acc)}.</label>
          <div id="mu-result"></div>
          <div class="button-row">
            <button id="mu-dry-run" type="button">Simular (Dry-run)</button>
            <button id="mu-submit" type="submit" class="primary">Subir Anúncio (PAUSED)</button>
          </div>
        </aside>
      </form>`;

    const campSel=body.querySelector('#mu-camp');
    const adsetSel=body.querySelector('#mu-adset');
    const newCampBox=body.querySelector('#mu-new-camp-box');
    const newAdsetBox=body.querySelector('#mu-new-adset-box');
    const mediaSel=body.querySelector('#mu-media-select');
    const mediaInput=body.querySelector('#mu-media-path');

    if(draft.media_path){
      const match=[...mediaSel.options].find(o=>o.value===`path:${draft.media_path}`);
      if(match)match.selected=true;
    }

    let mockMode='feed';
    const mockStage=body.querySelector('#mu-mockup-stage');
    const ctaMap={LEARN_MORE:'Saiba mais',SHOP_NOW:'Comprar agora',SIGN_UP:'Cadastre-se',CONTACT_US:'Fale conosco'};

    function renderMockup(){
      const pageEl=body.querySelector('#mu-page-select')||body.querySelector('#mu-page-input');
      const pageLabel=pageEl?.selectedOptions?.[0]?.textContent?.replace(/\s*\([^)]+\)$/,'')||pageEl?.value||'Sua Página';
      const primaryText=body.querySelector('#mu-primary-text').value.trim()||'Seu texto principal aparece aqui chamando atenção nos primeiros segundos…';
      const headline=body.querySelector('#mu-headline').value.trim()||'Frete grátis + Garantia hoje';
      const linkUrl=body.querySelector('#mu-link').value.trim()||'https://seusite.com/oferta';
      const ctaLabel=ctaMap[body.querySelector('#mu-cta').value]||'Saiba mais';
      let domain='seusite.com';
      try{domain=new URL(linkUrl).hostname.replace(/^www\./,'')}catch{}

      const mVal=mediaSel.value;
      let mediaHtml='<div class="ma-mock-empty">Selecione um vídeo ou imagem à esquerda para visualizar o criativo aqui.</div>';
      if(mVal.startsWith('asset:')){
        const assetId=mVal.slice(6);
        mediaHtml=`<img class="ma-mock-media" src="/media/assets/${encodeURIComponent(assetId)}" alt="">`;
      }else if(mediaInput.value.trim()){
        const p=mediaInput.value.trim();
        const isImg=/\.(png|jpe?g|webp)$/i.test(p);
        const url=`/media/local?path=${encodeURIComponent(p)}`;
        mediaHtml=isImg?`<img class="ma-mock-media" src="${url}" alt="">`:`<video class="ma-mock-media" src="${url}" controls muted loop playsinline></video>`;
      }

      mockStage.className=`ma-mock-card ${mockMode==='reels'?'is-reels':'is-feed'}`;
      if(mockMode==='reels'){
        mockStage.innerHTML=`
          <div class="ma-reels-box">
            ${mediaHtml}
            <div class="ma-reels-overlay">
              <div class="ma-mock-brand"><span class="ma-mock-avatar">${html(pageLabel.slice(0,1).toUpperCase())}</span><div><strong>${html(pageLabel)}</strong><small>Patrocinado</small></div></div>
              <p class="ma-mock-copy">${html(primaryText)}</p>
              <button type="button" class="ma-mock-cta">${html(ctaLabel)} →</button>
            </div>
          </div>`;
      }else{
        mockStage.innerHTML=`
          <div class="ma-mock-head">
            <span class="ma-mock-avatar">${html(pageLabel.slice(0,1).toUpperCase())}</span>
            <div><strong>${html(pageLabel)}</strong><small>Patrocinado · 🌐</small></div>
          </div>
          <p class="ma-mock-copy">${html(primaryText)}</p>
          <div class="ma-feed-media">${mediaHtml}</div>
          <div class="ma-mock-foot">
            <div><small>${html(domain.toUpperCase())}</small><strong>${html(headline)}</strong></div>
            <span class="ma-mock-cta">${html(ctaLabel)}</span>
          </div>
          <div class="ma-mock-social"><span>👍 Curtir</span><span>💬 Comentar</span><span>↗ Compartilhar</span></div>`;
      }
    }

    body.querySelector('#mu-mock-feed').addEventListener('click',()=>{
      mockMode='feed';
      body.querySelector('#mu-mock-feed').className='primary';
      body.querySelector('#mu-mock-reels').className='quiet';
      renderMockup();
    });
    body.querySelector('#mu-mock-reels').addEventListener('click',()=>{
      mockMode='reels';
      body.querySelector('#mu-mock-reels').className='primary';
      body.querySelector('#mu-mock-feed').className='quiet';
      renderMockup();
    });
    for(const evName of ['input','change']){
      body.querySelector('#ma-upload-form').addEventListener(evName,renderMockup);
    }
    renderMockup();

    function syncVisibility(){
      newCampBox.hidden=campSel.value!=='__new__';
      newAdsetBox.hidden=adsetSel.value!=='__new__';
    }
    campSel.addEventListener('change',syncVisibility);
    adsetSel.addEventListener('change',syncVisibility);
    syncVisibility();

    mediaSel.addEventListener('change',()=>{
      if(mediaSel.value.startsWith('path:')){
        mediaInput.value=mediaSel.value.slice(5);
      }else if(mediaSel.value.startsWith('asset:')){
        mediaInput.value='';
      }
    });

    function buildPayload(dryRun){
      const mVal=mediaSel.value;
      const assetId=mVal.startsWith('asset:')?mVal.slice(6):null;
      const pageEl=body.querySelector('#mu-page-select')||body.querySelector('#mu-page-input');
      return {
        dry_run:dryRun,
        confirmed:body.querySelector('#mu-confirm').checked,
        account_id:acc,
        campaign_id:campSel.value,
        adset_id:adsetSel.value,
        new_campaign:{
          name:body.querySelector('#mu-camp-name').value.trim(),
          objective:body.querySelector('#mu-camp-obj').value,
          daily_budget_brl:Number(body.querySelector('#mu-camp-budget').value||50),
        },
        new_adset:{
          name:body.querySelector('#mu-adset-name').value.trim(),
          country:body.querySelector('#mu-adset-country').value.trim(),
          optimization_goal:body.querySelector('#mu-adset-goal').value,
        },
        asset_id:assetId,
        media_path:assetId?null:mediaInput.value.trim(),
        page_id:pageEl?pageEl.value.trim():'',
        pixel_id:body.querySelector('#mu-pixel').value.trim(),
        ad_name:body.querySelector('#mu-ad-name').value.trim(),
        primary_text:body.querySelector('#mu-primary-text').value.trim(),
        headline:body.querySelector('#mu-headline').value.trim(),
        cta_type:body.querySelector('#mu-cta').value,
        link_url:body.querySelector('#mu-link').value.trim(),
      };
    }

    async function send(dryRun){
      const resBox=body.querySelector('#mu-result');
      resBox.innerHTML='<div class="notice">Executando…</div>';
      try{
        const out=await api('/api/meta-ads/upload',{method:'POST',body:buildPayload(dryRun)});
        resBox.innerHTML=`<div class="notice good"><strong>${dryRun?'Simulação aprovada':'Anúncio criado como PAUSED!'}</strong><pre class="mono" style="white-space:pre-wrap;margin-top:8px">${html(JSON.stringify(out,null,2))}</pre></div>`;
        toast(dryRun?'Simulação concluída sem alterações.':'Anúncio criado na Meta como PAUSED.');
      }catch(err){
        resBox.innerHTML=`<div class="notice bad">${html(err.message)}</div>`;
      }
    }

    body.querySelector('#mu-dry-run').addEventListener('click',()=>send(true));
    body.querySelector('#ma-upload-form').addEventListener('submit',ev=>{ev.preventDefault();send(false)});
  }

  async function loadPixels(){
    const body=root.querySelector('#ma-body');
    const acc=accSelect.value||status.account_id;
    body.innerHTML='<div class="panel"><div class="empty">Carregando Pixels da conta…</div></div>';
    try{
      const res=await api(`/api/meta-ads/resources?account_id=${encodeURIComponent(acc)}`);
      const pixels=res.pixels||[];
      body.innerHTML=`
        <div class="grid">
          <section class="panel">
            <div class="panel-head"><h2>Pixels da conta ${html(acc)}</h2><span class="counter">${pixels.length}</span></div>
            <div id="ma-pixels-list" class="stack"></div>
          </section>
          <aside class="panel">
            <div class="panel-head"><h2>+ Criar novo Pixel</h2></div>
            <form id="ma-pixel-form" class="stack">
              <label>Nome do Pixel<input id="ma-pixel-name" required placeholder="Ex.: Pixel Loja Principal"></label>
              <button type="submit" class="primary">Criar Pixel na Meta</button>
            </form>
            <div id="ma-pixel-msg"></div>
          </aside>
        </div>`;

      const list=body.querySelector('#ma-pixels-list');
      if(!pixels.length){
        list.innerHTML='<div class="empty">Nenhum Pixel encontrado nesta conta. Crie um ao lado.</div>';
      }else{
        for(const px of pixels){
          const card=document.createElement('article');
          card.className='panel ed-subpanel';
          const fallbackCode=`<!-- Meta Pixel Code -->\n<script>\n!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');\nfbq('init', '${px.id}');\nfbq('track', 'PageView');\n</script>`;
          const snippet=px.code||fallbackCode;
          card.innerHTML=`
            <div class="panel-head">
              <div><strong>${html(px.name||px.id)}</strong><br><small class="hint mono">ID: ${html(px.id)} · Último disparo: ${html(px.last_fired_time||'Nunca')}</small></div>
              <button type="button" class="quiet ma-copy-px">Copiar código</button>
            </div>
            <details><summary class="hint">Visualizar código do Pixel</summary><pre class="mono" style="white-space:pre-wrap;font-size:11px;margin-top:8px">${html(snippet)}</pre></details>`;
          card.querySelector('.ma-copy-px').addEventListener('click',async()=>{
            try{await navigator.clipboard.writeText(snippet);toast('Código do Pixel copiado.')}catch{toast('Não foi possível copiar.')}
          });
          list.append(card);
        }
      }

      body.querySelector('#ma-pixel-form').addEventListener('submit',async ev=>{
        ev.preventDefault();
        const btn=ev.currentTarget.querySelector('button[type=submit]');
        btn.disabled=true;
        try{
          const created=await api('/api/meta-ads/pixels',{method:'POST',body:{account_id:acc,name:body.querySelector('#ma-pixel-name').value.trim()}});
          toast(`Pixel criado: ${created.name} (${created.id})`);
          await loadPixels();
        }catch(err){
          body.querySelector('#ma-pixel-msg').innerHTML=`<div class="notice bad">${html(err.message)}</div>`;
          btn.disabled=false;
        }
      });
    }catch(err){
      const isAuth=err.code==='META_AUTH_REQUIRED'||err.status===401||/expired|oauth|token/i.test(err.message||'');
      if(isAuth){
        body.innerHTML=`
          <div class="notice bad">
            <strong>Sessão Meta expirada ou inválida.</strong><br>
            <span>${html(err.message)}</span>
            <div class="button-row" style="margin-top:10px">
              <button id="ma-fix-token" type="button" class="primary">Atualizar / Reconectar Token →</button>
            </div>
          </div>`;
        body.querySelector('#ma-fix-token').addEventListener('click',()=>openMetaLoginModal(root,async()=>{cachedAccounts=[];await refreshBase();await renderMetaAds(root)}));
      }else{
        body.innerHTML=`<div class="notice bad">${html(err.message)}</div>`;
      }
    }
  }

  await ensureAccounts();
  accSelect.addEventListener('change',async()=>{
    selectedCampaign=null;selectedAdset=null;level='campaigns';
    await api('/api/meta-ads/defaults',{method:'POST',body:{account_id:accSelect.value}});
    await refreshBase();
    if(activeTab==='manager')await loadManager();
    else if(activeTab==='pixels')await loadPixels();
    else await loadUpload();
  });
  presetSelect.addEventListener('change',()=>{datePreset=presetSelect.value;if(activeTab==='manager')loadManager()});
  root.querySelector('#ma-refresh').addEventListener('click',()=>activeTab==='manager'?loadManager():activeTab==='pixels'?loadPixels():loadUpload());
  root.querySelector('#ma-tab-manager').addEventListener('click',()=>{activeTab='manager';renderMetaAds(root)});
  root.querySelector('#ma-tab-upload').addEventListener('click',()=>{activeTab='upload';renderMetaAds(root)});
  root.querySelector('#ma-tab-pixels').addEventListener('click',()=>{activeTab='pixels';renderMetaAds(root)});
  root.querySelector('#ma-reauth').addEventListener('click',()=>openMetaLoginModal(root,async()=>{cachedAccounts=[];await refreshBase();await renderMetaAds(root)}));
  root.querySelector('#ma-logout').addEventListener('click',async()=>{
    if(!confirm('Remover o token salvo da Meta Ads?'))return;
    await api('/api/meta-ads/logout',{method:'POST',body:{}});
    cachedAccounts=[];cachedRows=[];
    await refreshBase();
    await renderMetaAds(root);
  });

  if(activeTab==='manager')await loadManager();
  else if(activeTab==='pixels')await loadPixels();
  else await loadUpload();
}
