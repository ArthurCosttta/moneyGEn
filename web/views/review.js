import{api,go,loadBatch,store,toast,uid}from'../state.js';
import{status}from'../components/status.js';

function options(values,current){return values.map(value=>`<option value="${value}" ${value===current?'selected':''}>${value||'Automática'}</option>`).join('')}

function consentDialog(){
  const dialog=document.createElement('dialog');
  dialog.innerHTML=`<form method="dialog" class="dialog-body"><h2>Confirmar 1 vídeo</h2><p class="lead">O plano foi verificado automaticamente. Esta ação pode consumir créditos e não será repetida automaticamente.</p><label class="check"><input type="checkbox">Revisei prompt, imagem, modelo e pasta.</label><label class="check"><input type="checkbox">Entendo que pode consumir créditos.</label><label class="check"><input type="checkbox">Aceito continuar mesmo sem custo exato.</label><div class="button-row"><button value="cancel">Cancelar</button><button class="primary" value="generate" disabled>Confirmar e gerar agora</button></div></form>`;
  const checks=[...dialog.querySelectorAll('input')],button=dialog.querySelector('.primary');
  checks.forEach(check=>check.addEventListener('change',()=>button.disabled=!checks.every(value=>value.checked)));
  return dialog;
}

export async function renderReview(root,id){
  const batch=await loadBatch(id);
  if(!batch){root.innerHTML='<div class="empty">Nenhum lote para conferir. <a href="#/criar">Crie uma fila.</a></div>';return}
  root.innerHTML=`<section class="page-head"><div><h1>Pronto para gerar.</h1><p class="lead">Confira o essencial. Ao clicar, a verificação acontece automaticamente e a confirmação de gasto abre em seguida.</p></div></section><div class="grid"><section class="panel"><div class="panel-head"><h2>Fila</h2><span id="batch-status"></span></div><div id="review-takes" class="takes"></div></section><aside class="panel"><div class="panel-head"><h2>Configuração</h2><span class="counter">rev. ${batch.revision}</span></div><form id="settings" class="settings"><label>Modelo<select name="model_id">${options(['omni-1.2','veo-fast','veo-lite'],batch.defaults.model_id)}</select></label><label>Formato<select name="aspect">${options(['9:16','16:9','1:1'],batch.defaults.aspect)}</select></label><label>Duração<select name="duration_seconds">${options(['3','4','5','6','8','10','12','15'],String(batch.defaults.duration_seconds??6))}</select></label><label>Resolução<select name="resolution">${options(['','360p','720p'],batch.defaults.resolution||'')}</select></label><label class="wide">Pasta de saída<div class="row"><input name="output_dir"><button id="choose-output" type="button">Escolher</button></div></label><label class="wide">Limite de créditos<input name="max_credits" type="number" min="0" value="${batch.max_credits??''}" placeholder="Opcional"></label></form><div id="preflight"></div><button id="generate-now" class="primary main-action" type="button">Gerar agora</button></aside></div>`;
  root.querySelector('#batch-status').append(status(batch.state));
  const form=root.querySelector('#settings');form.output_dir.value=batch.output_dir||'';
  const takes=root.querySelector('#review-takes');
  for(const[index,item]of store.items.entries()){
    const card=document.createElement('article');card.className='take';
    card.innerHTML=`<span class="take-number">${String(index+1).padStart(2,'0')}</span>${item.asset_id?`<img src="/media/assets/${item.asset_id}" alt="Imagem anexada">`:'<span class="image-placeholder">T2V</span>'}<div><h3>${item.output_name||`Take ${index+1}`}</h3><p></p></div>`;
    card.querySelector('p').textContent=item.prompt;card.append(status(item.state));takes.append(card);
  }
  async function save(){
    const body={expected_revision:batch.revision,defaults:{model_id:form.model_id.value,aspect:form.aspect.value,duration_seconds:Number(form.duration_seconds.value),resolution:form.resolution.value||null},output_dir:form.output_dir.value.trim(),max_credits:form.max_credits.value===''?null:Number(form.max_credits.value)};
    const updated=await api(`/api/batches/${batch.id}`,{method:'PATCH',body});Object.assign(batch,updated);return updated;
  }
  root.querySelector('#choose-output').addEventListener('click',async()=>{try{const result=await api('/api/choose-folder',{method:'POST',body:{initial:form.output_dir.value}});if(result.path)form.output_dir.value=result.path}catch(error){toast(error.message)}});
  root.querySelector('#generate-now').addEventListener('click',async event=>{
    const button=event.currentTarget;button.disabled=true;button.textContent='Verificando…';
    try{
      await save();
      const discovery=await api('/api/preflight',{method:'POST',body:{batch_id:batch.id}});
      if(!discovery.ready)throw new Error(discovery.errors.map(value=>value.message).join('\n')||'Conta ou modelo indisponível.');
      const plan=await api('/api/plan',{method:'POST',body:{batch_id:batch.id,expected_revision:batch.revision}});store.plan=plan;
      if(!plan.valid)throw new Error(plan.errors.map(value=>value.message).join('\n'));
      const check=await api('/api/preflight',{method:'POST',body:{batch_id:batch.id,plan_hash:plan.plan_hash}});
      if(!check.ready)throw new Error(check.errors.map(value=>value.message).join('\n'));
      batch.plan_hash=plan.plan_hash;
      const dialog=consentDialog();root.append(dialog);dialog.showModal();
      dialog.addEventListener('close',async()=>{if(dialog.returnValue!=='generate')return;try{const result=await api('/api/test',{method:'POST',body:{batch_id:batch.id,expected_revision:batch.revision,plan_hash:plan.plan_hash,idempotency_key:uid('test'),item_ids:[store.items[0].id],confirmed:true,risk:true,reviewed:false,unknown_cost_accepted:true}});toast(`Vídeo enfileirado: ${result.execution_id}`);go('videos',batch.id)}catch(error){toast(error.message)}});
    }catch(error){const note=root.querySelector('#preflight');note.className='notice bad';note.textContent=error.message}
    finally{button.disabled=false;button.textContent='Gerar agora'}
  });
}
