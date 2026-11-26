import{api,go,loadBatch,store,toast,uid}from'../state.js';
import{status}from'../components/status.js';

function remainderDialog(count){
  const dialog=document.createElement('dialog');
  dialog.innerHTML=`<form method="dialog" class="dialog-body"><h2>Gerar mais ${count} vídeo(s)</h2><p class="lead">Nova autorização paga. Falhas anteriores não serão repetidas.</p><label class="check"><input type="checkbox">Assisti e aprovei o teste.</label><label class="check"><input type="checkbox">Revisei os itens restantes.</label><label class="check"><input type="checkbox">Aceito o risco de gasto e custo desconhecido.</label><div class="button-row"><button value="cancel">Cancelar</button><button class="primary" value="approve" disabled>Aprovar restantes</button></div></form>`;
  const checks=[...dialog.querySelectorAll('input')],button=dialog.querySelector('.primary');checks.forEach(check=>check.addEventListener('change',()=>button.disabled=!checks.every(value=>value.checked)));return dialog;
}

function friendlyEvent(event){
  if(event.payload.status==='NEEDS_RECONCILIATION')return'O Google terminou, mas o arquivo local precisa ser verificado. Clique em “Verificar e recuperar vídeo”.';
  const message=event.payload.message||event.event_type;
  if(String(message).trim().startsWith('{'))return'Resposta recebida do Google; aguardando validação local.';
  return message;
}

export async function renderVideos(root,id){
  const batch=await loadBatch(id);
  if(!batch){root.innerHTML='<div class="empty">Nenhum lote. <a href="#/criar">Criar vídeo</a></div>';return}
  const events=await api(`/api/events?batch_id=${encodeURIComponent(batch.id)}&limit=200`);store.lastEventId=events.last_event_id;
  const valid=store.items.filter(item=>item.artifact_id);
  const remaining=store.items.filter(item=>['VALIDATED','READY'].includes(item.state));
  const executionByItem=new Map();for(const event of events.events){if(event.item_id&&event.execution_id)executionByItem.set(event.item_id,event.execution_id)}
  root.innerHTML=`<section class="page-head"><div><h1>Seus vídeos.</h1><p class="lead">Resultados prontos e ações que precisam de você.</p></div><select id="batch-select" aria-label="Lote"></select></section><section class="panel centered-panel"><div class="panel-head"><div><h2 id="batch-name"></h2><span class="hint mono">${batch.id}</span></div><span id="batch-state"></span></div><dl class="summary"><div><dt>Saída</dt><dd id="output-path"></dd></div><div><dt>Prontos</dt><dd>${valid.length} de ${store.items.length}</dd></div></dl><div class="button-row"><button id="open-output">Abrir pasta</button><button id="export" ${valid.length?'':'disabled'}>Baixar todos em ZIP</button><button id="pause">Pausar</button><button id="resume" ${batch.state==='PAUSED'?'':'disabled'}>Continuar</button></div><div id="export-state"></div></section><section><div class="panel-head section-gap"><h2>Vídeos</h2><span class="counter">${valid.length}</span></div><div id="video-grid" class="video-grid"></div></section><div class="grid section-gap"><section class="panel"><div class="panel-head"><h2>Fila</h2><span class="counter">${store.items.length}</span></div><div id="item-list" class="takes"></div></section><aside class="panel"><div class="panel-head"><h2>O que aconteceu</h2></div><div id="events" class="timeline"></div></aside></div><div class="action-footer"><button id="approve" class="primary" ${valid.length&&remaining.length?'':'disabled'}>${remaining.length?`Gerar ${remaining.length} restante(s)`:'Nada pendente'}</button></div>`;
  root.querySelector('#batch-name').textContent=batch.name;root.querySelector('#output-path').textContent=batch.output_dir||'—';root.querySelector('#batch-state').append(status(batch.state));
  const select=root.querySelector('#batch-select');for(const value of store.batches){const option=document.createElement('option');option.value=value.id;option.textContent=value.name;option.selected=value.id===batch.id;select.append(option)}select.addEventListener('change',()=>go('videos',select.value));
  const videoGrid=root.querySelector('#video-grid');if(!valid.length)videoGrid.innerHTML='<div class="empty">Nenhum vídeo validado ainda.</div>';
  for(const item of valid.slice(0,12)){const card=document.createElement('article');card.className='video-card';card.innerHTML=`<video controls preload="metadata" src="/media/artifacts/${item.artifact_id}"></video><div class="video-body"><h3></h3><p></p><a class="button" href="/media/artifacts/${item.artifact_id}" download>Baixar MP4</a></div>`;card.querySelector('h3').textContent=item.output_name||'Vídeo';card.querySelector('p').textContent=item.prompt;videoGrid.append(card)}
  const itemList=root.querySelector('#item-list');
  for(const[index,item]of store.items.entries()){
    const card=document.createElement('div');card.className='take';card.innerHTML=`<span class="take-number">${String(index+1).padStart(2,'0')}</span>${item.asset_id?`<img src="/media/assets/${item.asset_id}" alt="">`:'<span class="image-placeholder">T2V</span>'}<div><h3></h3><p></p></div><div class="take-actions"></div>`;card.querySelector('h3').textContent=item.output_name||`Take ${index+1}`;card.querySelector('p').textContent=item.prompt;const actions=card.querySelector('.take-actions');actions.append(status(item.state));
    if(item.state==='NEEDS_RECONCILIATION'){const recover=document.createElement('button');recover.className='primary recover';recover.textContent='Verificar e recuperar vídeo';recover.addEventListener('click',async()=>{recover.disabled=true;recover.textContent='Verificando…';try{const result=await api('/api/reconcile',{method:'POST',body:{execution_id:executionByItem.get(item.id)}});toast(result.recovered?'Vídeo recuperado sem nova geração.':'Vídeo já estava pronto.');window.dispatchEvent(new Event('flowlocal-refresh'))}catch(error){toast(error.message);recover.disabled=false;recover.textContent='Verificar e recuperar vídeo'}});actions.append(recover)}
    itemList.append(card);
  }
  const timeline=root.querySelector('#events');if(!events.events.length)timeline.innerHTML='<div class="empty">Sem atividade.</div>';for(const event of events.events.toReversed().slice(0,12)){const node=document.createElement('div');node.className='event';node.innerHTML='<strong></strong><time></time>';node.querySelector('strong').textContent=friendlyEvent(event);node.querySelector('time').textContent=new Date(event.created_at).toLocaleString('pt-BR');timeline.append(node)}
  root.querySelector('#open-output').addEventListener('click',()=>api('/api/open-output',{method:'POST',body:{batch_id:batch.id}}).catch(error=>toast(error.message)));
  root.querySelector('#pause').addEventListener('click',()=>api('/api/stop',{method:'POST',body:{batch_id:batch.id}}).then(()=>toast('Pausa solicitada.')).catch(error=>toast(error.message)));
  root.querySelector('#resume').addEventListener('click',()=>api('/api/resume',{method:'POST',body:{batch_id:batch.id,plan_hash:batch.plan_hash}}).then(()=>window.dispatchEvent(new Event('flowlocal-refresh'))).catch(error=>toast(error.message)));
  root.querySelector('#approve').addEventListener('click',()=>{const dialog=remainderDialog(remaining.length);root.append(dialog);dialog.showModal();dialog.addEventListener('close',async()=>{if(dialog.returnValue!=='approve')return;try{await api('/api/batch',{method:'POST',body:{batch_id:batch.id,expected_revision:batch.revision,plan_hash:batch.plan_hash,idempotency_key:uid('remaining'),item_ids:remaining.map(item=>item.id),confirmed:true,risk:true,reviewed:true,unknown_cost_accepted:true}});toast('Restantes enfileirados.');window.dispatchEvent(new Event('flowlocal-refresh'))}catch(error){toast(error.message)}})});
  root.querySelector('#export').addEventListener('click',async()=>{try{const result=await api('/api/exports',{method:'POST',body:{batch_id:batch.id,artifact_ids:valid.map(item=>item.artifact_id)}});const node=root.querySelector('#export-state');node.className='notice';node.textContent='Preparando ZIP…';for(let i=0;i<30;i++){await new Promise(resolve=>setTimeout(resolve,500));const state=await api(`/api/exports/${result.export_id}`);if(state.state==='SUCCEEDED'){node.className='notice good';node.innerHTML=`ZIP pronto. <a class="button" href="${state.download_url}">Baixar</a>`;break}if(state.state==='FAILED')throw new Error(state.error_code)}}catch(error){toast(error.message)}});
}

export async function openVideosModal(host,batchId){
  const dlg=document.createElement('dialog');
  dlg.className='hub-dialog';
  dlg.innerHTML=`
    <div class="hub-dialog-head">
      <strong>Vídeos Gerados</strong>
      <button type="button" class="quiet" id="hub-vid-close">Fechar ×</button>
    </div>
    <div class="hub-dialog-body"></div>`;
  host.append(dlg);
  dlg.showModal();
  const body=dlg.querySelector('.hub-dialog-body');
  let alive=true;
  const poll=setInterval(async()=>{
    if(!alive||!store.current?.id)return;
    try{
      const r=await api(`/api/events?batch_id=${encodeURIComponent(store.current.id)}&after_event_id=${store.lastEventId||0}&limit=20`);
      if(r.events?.length)await renderVideos(body,store.current.id);
    }catch{}
  },2200);
  dlg.querySelector('#hub-vid-close').addEventListener('click',()=>{alive=false;clearInterval(poll);dlg.close();dlg.remove()});
  dlg.addEventListener('close',()=>{alive=false;clearInterval(poll);dlg.remove()});
  await renderVideos(body,batchId);
}
