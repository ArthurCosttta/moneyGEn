import{api,fileBase64,go,html,store,toast,uid}from'../state.js';
import{openFlowModal}from'../components/flow-modal.js';
import{openReviewModal}from'./review.js';
import{openVideosModal}from'./videos.js';
import{openEditorModal}from'./editar.js';

let draft={};
try{draft=JSON.parse(localStorage.getItem('flowlocal-draft')||'{}')}catch{}
let assets=Array.isArray(draft.assets)?draft.assets:[];
let rows=[];
let importPreview=null;
let saveTimer;
let lastQueueKey=null;

function prompts(value,splitMode='line'){
  if(splitMode==='line'){
    return value.split(/\r?\n/).map(line=>line.trim()).filter(line=>line&&line!=='---');
  }
  return value.split(/\r?\n/).reduce((all,line)=>{if(line==='---')all.push([]);else all.at(-1).push(line);return all},[[]]).map(lines=>lines.join('\n')).filter(x=>x.trim());
}

function settingsFrom(form){
  return{model_id:form.model_id.value,aspect:form.aspect.value,duration_seconds:Number(form.duration_seconds.value),resolution:form.resolution.value||null};
}

function applySettings(form,value={}){
  form.model_id.value=value.model_id||'omni-1.2';
  form.aspect.value=value.aspect||'9:16';
  form.duration_seconds.value=String(value.duration_seconds??6);
  form.resolution.value=value.resolution||'';
}

function persist(form){
  clearTimeout(saveTimer);
  saveTimer=setTimeout(()=>{
    localStorage.setItem('flowlocal-draft',JSON.stringify({name:form.name.value,text:form.prompts.value,split_mode:form.split_mode?.value||'line',settings:settingsFrom(form),assets}));
    const ds=document.querySelector('#draft-state');
    if(ds)ds.textContent='Rascunho salvo.';
  },300);
}

function manualRows(form){
  return prompts(form.prompts.value,form.split_mode?.value||'line').map((prompt,index)=>({id:uid('take'),kind:assets.length?'i2v':'t2v',prompt,asset_id:assets.length?assets[index%assets.length].id:null,output_name:null,overrides:{},title:`Take ${index+1}`,source_index:index,_manual:true}));
}

function renderAttachments(root,form){
  const list=root.querySelector('#attached-images');
  list.replaceChildren();
  if(!assets.length){list.textContent='Nenhuma imagem anexada.';list.className='hint';return}
  list.className='attached-images';
  for(const asset of assets){
    const chip=document.createElement('span');chip.className='attached-image';
    const image=document.createElement('img');image.src=asset.preview_url||`/media/assets/${asset.id}`;image.alt='';
    const name=document.createElement('b');name.textContent=asset.name||'Imagem anexada';
    const remove=document.createElement('button');remove.type='button';remove.className='quiet';remove.setAttribute('aria-label',`Remover ${asset.name||'imagem'}`);remove.textContent='×';
    remove.addEventListener('click',()=>{assets=assets.filter(value=>value.id!==asset.id);renderAttachments(root,form);renderQueue(root,form);persist(form)});
    chip.append(image,name,remove);list.append(chip);
  }
}

function parseJsonKind(text){
  const trimmed=(text||'').trim();
  if(!trimmed.startsWith('{')&&!trimmed.startsWith('['))return null;
  try{
    const parsed=JSON.parse(trimmed);
    if(parsed?.flow_spec&&Array.isArray(parsed.flow_spec.nodes))return {kind:'flow',spec:parsed.flow_spec,raw:JSON.stringify(parsed.flow_spec,null,2)};
    if(Array.isArray(parsed?.nodes))return {kind:'flow',spec:parsed,raw:trimmed};
    if(Array.isArray(parsed?.items))return {kind:'manifest',doc:parsed};
  }catch{}
  return null;
}

function renderQueue(root,form){
  const rawPrompt=form.prompts.value.trim();
  const queueKey=JSON.stringify([rawPrompt,form.split_mode?.value||'line',assets.map(x=>x.id),importPreview?.preview_hash||null]);
  if(queueKey===lastQueueKey)return;
  lastQueueKey=queueKey;
  const list=root.querySelector('#take-list');
  const parsedJson=parseJsonKind(rawPrompt);
  if(parsedJson?.kind==='flow'){
    const count=parsedJson.spec.nodes.length;
    root.querySelector('#take-count').textContent=`${count} node(s) no JSON`;
    list.innerHTML=`<div class="notice good"><strong>JSON de fluxo detectado (${html(parsedJson.spec.name||'Fluxo')})</strong><br><small>Este JSON define ${count} node(s). Clique abaixo para anexar as fotos nos slots e iniciar a fila.</small><div class="button-row" style="margin-top:8px"><button type="button" id="launch-pasted-flow" class="primary">Abrir fila de nodes deste JSON →</button></div></div>`;
    list.querySelector('#launch-pasted-flow').addEventListener('click',()=>openFlowModal(root,async batchId=>{await storeReload();openVideosModal(root,batchId)},parsedJson.raw));
    rows=[];
    return;
  }
  if(parsedJson?.kind==='manifest'){
    root.querySelector('#take-count').textContent=`${parsedJson.doc.items.length} item(ns) no JSON`;
    list.innerHTML=`<div class="notice good"><strong>JSON de lote detectado (${html(parsedJson.doc.name||'Lote')})</strong><br><small>Clique abaixo para validar os itens e carregar direto na fila.</small><div class="button-row" style="margin-top:8px"><button type="button" id="import-pasted-manifest" class="primary">Carregar este JSON na fila →</button></div></div>`;
    list.querySelector('#import-pasted-manifest').addEventListener('click',async()=>{
      try{
        await applyManifestDocs([{name:'colado.json',document:parsedJson.doc}],root,form);
        form.prompts.value='';
        persist(form);
      }catch(err){toast(err.message)}
    });
    rows=[];
    return;
  }
  const manual=manualRows(form);
  rows=[...(importPreview?.valid_items||[]),...manual];
  root.querySelector('#take-count').textContent=`${rows.length} take(s)`;
  list.replaceChildren();
  if(!rows.length){list.innerHTML='<div class="empty">Cole seus prompts (1 por linha) ou suba/cole um JSON estruturado.</div>';return}
  rows.forEach((row,index)=>{const card=document.createElement('article');card.className='take';const asset=assets.find(x=>x.id===row.asset_id);card.innerHTML=`<span class="take-number">${String(index+1).padStart(2,'0')}</span>${asset?`<img src="/media/assets/${asset.id}" alt="">`:'<span class="image-placeholder">T2V</span>'}<div><h3>Take ${index+1}</h3><p></p></div>`;card.querySelector('p').textContent=row.prompt;list.append(card)});
}

async function applyManifestDocs(documents,root,form){
  importPreview=await api('/api/import-manifest',{method:'POST',body:{documents,assets:assets.map(x=>({reference:x.name,asset_id:x.id}))}});
  if(importPreview.name&&!form.name.value.trim())form.name.value=importPreview.name;
  if(importPreview.defaults)applySettings(form,importPreview.defaults);
  const note=root.querySelector('#import-note');
  if(note){
    note.className=importPreview.errors.length||importPreview.unresolved_images.length?'notice bad':'notice good';
    note.textContent=`Prévia JSON: ${importPreview.valid_indexes.length} válido(s), ${importPreview.errors.length} erro(s).`;
  }
  renderQueue(root,form);
}

async function attachFiles(files,root,form){
  const button=root.querySelector('#attach-label');button.setAttribute('aria-busy','true');
  try{
    for(const file of files){const staged=await api('/api/stage',{method:'POST',body:{name:file.name,content:await fileBase64(file)}});addAsset({...staged,name:file.name})}
    renderAttachments(root,form);renderQueue(root,form);persist(form);toast(`${files.length} imagem(ns) anexada(s).`);
  }catch(error){toast(error.message)}finally{button.removeAttribute('aria-busy');root.querySelector('#prompt-images').value=''}
}

function addAsset(asset){
  if(!assets.some(value=>value.id===asset.id))assets.push(asset);
}

function createImageDialog(root,form){
  let references=[];
  const dialog=document.createElement('dialog');
  dialog.innerHTML=`<form class="dialog-body image-create-dialog"><h2>Criar imagem</h2><p class="lead">Descreva a imagem para gerar no ChatGPT Web e anexar automaticamente.</p><label for="image-prompt">Prompt da imagem</label><textarea id="image-prompt" name="prompt" maxlength="4000" required placeholder="Ex.: fotografia de produto, fundo limpo, luz de estúdio…"></textarea><div class="image-reference-tools"><label class="button" for="image-reference-files">Anexar referência<input id="image-reference-files" class="sr-only" type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple></label><small>Até 4 imagens · 20 MiB</small></div><div id="image-references" class="image-references hint">Nenhuma referência anexada.</div><label class="check"><input name="confirmed" type="checkbox" required>Entendo que esta geração pode consumir minha franquia de imagens.</label><div id="image-result"></div><div class="button-row"><button class="cancel" type="button">Cancelar</button><button class="primary" type="submit">Confirmar e criar</button></div></form>`;
  root.append(dialog);dialog.showModal();dialog.querySelector('#image-prompt').focus();
  dialog.querySelector('.cancel').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>dialog.remove());
  const referenceList=dialog.querySelector('#image-references');
  function renderReferences(){
    referenceList.replaceChildren();if(!references.length){referenceList.className='image-references hint';referenceList.textContent='Nenhuma referência anexada.';return}referenceList.className='image-references';
    for(const asset of references){const item=document.createElement('span');const image=document.createElement('img');image.src=asset.preview_url||`/media/assets/${asset.id}`;image.alt='';const name=document.createElement('b');name.textContent=asset.name;const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.addEventListener('click',()=>{references=references.filter(value=>value.id!==asset.id);renderReferences()});item.append(image,name,remove);referenceList.append(item)}
  }
  dialog.querySelector('#image-reference-files').addEventListener('change',async event=>{const input=event.currentTarget;const chosen=[...input.files];if(references.length+chosen.length>4){toast('Anexe no máximo quatro referências.');input.value='';return}input.disabled=true;try{for(const file of chosen){const staged=await api('/api/stage',{method:'POST',body:{name:file.name,content:await fileBase64(file)}});if(!references.some(value=>value.id===staged.id))references.push({...staged,name:file.name})}renderReferences()}catch(error){toast(error.message)}finally{input.disabled=false;input.value=''}});
  dialog.querySelector('form').addEventListener('submit',async event=>{
    event.preventDefault();const submit=dialog.querySelector('[type=submit]');const note=dialog.querySelector('#image-result');submit.disabled=true;submit.textContent='Criando imagem…';note.className='notice';note.textContent='Aguarde. A imagem será anexada automaticamente.';
    const imagePrompt=event.currentTarget.elements.namedItem('prompt');const confirmation=event.currentTarget.elements.namedItem('confirmed');
    try{const asset=await api('/api/image-generate',{method:'POST',body:{prompt:imagePrompt.value,confirmed:confirmation.checked,reference_asset_ids:references.map(value=>value.id)}});addAsset(asset);renderAttachments(root,form);renderQueue(root,form);persist(form);toast('Imagem criada e anexada.');dialog.close()}
    catch(error){note.className='notice bad';note.textContent=error.message;submit.disabled=false;submit.textContent='Confirmar e criar'}
  });
}

async function libraryDialog(root,form){
  const dialog=document.createElement('dialog');
  dialog.innerHTML=`<div class="dialog-body library-dialog"><div class="panel-head"><div><h2>Biblioteca</h2><p class="lead">Escolha uma imagem salva ou envie novas para usar nos prompts.</p></div><button class="quiet close-library" type="button" aria-label="Fechar">×</button></div><div class="button-row library-toolbar"><label class="button primary" for="library-files">Adicionar arquivos<input id="library-files" class="sr-only" type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple></label><button class="open-library" type="button">Abrir pasta</button></div><div id="library-grid" class="library-grid"><div class="empty">Carregando…</div></div></div>`;
  root.append(dialog);dialog.showModal();
  dialog.querySelector('.close-library').addEventListener('click',()=>dialog.close());dialog.addEventListener('close',()=>dialog.remove());
  const grid=dialog.querySelector('#library-grid');
  async function load(){
    const library=await api('/api/library');grid.replaceChildren();
    if(!library.items.length){grid.innerHTML='<div class="empty">A biblioteca está vazia.</div>';return}
    for(const item of library.items){
      const card=document.createElement('article');card.className='library-card';
      const image=document.createElement('img');image.src=item.preview_url;image.alt=item.name;
      const name=document.createElement('strong');name.textContent=item.name;
      const actions=document.createElement('div');actions.className='library-actions';
      const attach=document.createElement('button');attach.type='button';attach.className='primary';attach.textContent='Anexar';attach.addEventListener('click',()=>{addAsset(item);renderAttachments(root,form);renderQueue(root,form);persist(form);toast('Imagem anexada.');dialog.close()});
      actions.append(attach);card.append(image,name,actions);grid.append(card);
    }
  }
  dialog.querySelector('.open-library').addEventListener('click',()=>api('/api/library/open',{method:'POST',body:{}}).catch(error=>toast(error.message)));
  dialog.querySelector('#library-files').addEventListener('change',async event=>{const input=event.currentTarget;input.disabled=true;try{for(const file of input.files)await api('/api/library/import',{method:'POST',body:{name:file.name,content:await fileBase64(file)}});await load()}catch(error){toast(error.message)}finally{input.disabled=false;input.value=''}});
  try{await load()}catch(error){grid.innerHTML='<div class="notice bad"></div>';grid.firstElementChild.textContent=error.message}
}

function openJsonAndGptDialog(root,form){
  const dlg=document.createElement('dialog');
  dlg.className='sp-dialog';
  dlg.innerHTML=`
    <div class="panel-head" style="margin-bottom:10px">
      <div>
        <h3>Enviar para ChatGPT ou Subir/Colar JSON</h3>
        <small class="hint">Peça o JSON pronto ao ChatGPT, cole qualquer JSON (Fluxo HQ com <code>nodes</code> ou Lote com <code>items</code>) ou suba um arquivo <code>.json</code>.</small>
      </div>
      <button type="button" class="quiet" id="jg-close">×</button>
    </div>
    <div class="cal-add-form">
      <div class="sp-search-shell">
        <input id="jg-gpt-brief" placeholder="Descreva a campanha para o ChatGPT montar os prompts + JSON…">
        <button type="button" id="jg-gpt-send" class="primary">Pedir ao ChatGPT →</button>
      </div>
      <label>Colar JSON aqui
        <textarea id="jg-json-paste" rows="7" class="mono" placeholder='Cole JSON de Fluxo ("nodes") ou JSON de Lote ("items")…'></textarea>
      </label>
      <div class="button-row">
        <label class="button" for="jg-file">Subir arquivo .json<input id="jg-file" class="sr-only" type="file" accept="application/json,.json" multiple></label>
        <button type="button" id="jg-native">Abrir pasta JSON</button>
        <a class="button" href="/modelo-lote.json" download>Baixar modelo JSON</a>
        <button type="button" id="jg-apply" class="primary">Carregar JSON colado →</button>
      </div>
      <div id="jg-status"></div>
    </div>`;
  root.append(dlg);
  dlg.showModal();
  dlg.querySelector('#jg-close').addEventListener('click',()=>dlg.close());
  dlg.addEventListener('close',()=>dlg.remove());
  const stBox=dlg.querySelector('#jg-status');
  const pasteEl=dlg.querySelector('#jg-json-paste');

  async function processJsonText(raw){
    const parsed=parseJsonKind(raw);
    if(!parsed){throw new Error('JSON inválido: precisa conter "nodes" (Fluxo HQ) ou "items" (Lote de prompts).')}
    if(parsed.kind==='flow'){
      dlg.close();
      openFlowModal(root,async batchId=>{await storeReload();openVideosModal(root,batchId)},parsed.raw);
      return;
    }
    await applyManifestDocs([{name:'colado.json',document:parsed.doc}],root,form);
    dlg.close();
    toast(`${importPreview?.valid_indexes?.length||0} take(s) carregado(s) na fila!`);
  }

  dlg.querySelector('#jg-apply').addEventListener('click',async()=>{
    try{await processJsonText(pasteEl.value)}
    catch(err){stBox.innerHTML=`<div class="notice bad">${html(err.message)}</div>`}
  });

  dlg.querySelector('#jg-file').addEventListener('change',async ev=>{
    const files=[...ev.target.files];
    if(!files.length)return;
    try{
      if(files.length===1){
        const raw=await files[0].text();
        pasteEl.value=raw;
        const parsed=parseJsonKind(raw);
        if(parsed?.kind==='flow'){
          dlg.close();
          openFlowModal(root,async batchId=>{await storeReload();openVideosModal(root,batchId)},parsed.raw);
          return;
        }
      }
      const docs=[];
      for(const f of files)docs.push({name:f.name,document:JSON.parse(await f.text())});
      await applyManifestDocs(docs,root,form);
      dlg.close();
      toast(`${importPreview?.valid_indexes?.length||0} item(ns) importado(s) do JSON.`);
    }catch(err){stBox.innerHTML=`<div class="notice bad">${html(err.message)}</div>`}
  });

  dlg.querySelector('#jg-native').addEventListener('click',async()=>{
    try{
      importPreview=await api('/api/import-native',{method:'POST',body:{}});
      if(!importPreview.cancelled){
        form.name.value=importPreview.name;
        if(importPreview.defaults)applySettings(form,importPreview.defaults);
        renderQueue(root,form);
        dlg.close();
        toast(`${importPreview.valid_indexes.length} item(ns) importado(s).`);
      }
    }catch(err){stBox.innerHTML=`<div class="notice bad">${html(err.message)}</div>`}
  });

  dlg.querySelector('#jg-gpt-send').addEventListener('click',async ev=>{
    const brief=dlg.querySelector('#jg-gpt-brief').value.trim();
    if(!brief){toast('Descreva o que deseja gerar no ChatGPT.');return}
    const btn=ev.currentTarget;
    btn.disabled=true;btn.textContent='Consultando ChatGPT…';
    stBox.innerHTML='<div class="hint">Montando fluxo de imagens e vídeos no ChatGPT Web…</div>';
    try{
      const res=await api('/api/spy/offer-to-flow',{method:'POST',body:{product:brief,goal:brief}});
      pasteEl.value=JSON.stringify(res.flow_spec,null,2);
      stBox.innerHTML=`<div class="notice good">JSON recebido do ChatGPT! Clique em <strong>Carregar JSON colado →</strong> ou abra o Fluxo HQ.</div>`;
    }catch(err){
      stBox.innerHTML=`<div class="notice bad">${html(err.message)}</div>`;
    }finally{btn.disabled=false;btn.textContent='Pedir ao ChatGPT →'}
  });
}

export async function renderCreate(root){
  root.innerHTML=`
    <section class="page-head">
      <div>
        <h1>Estúdio de Criação.</h1>
        <p class="lead">Monte sua fila em segundos, peça ao ChatGPT, cole JSON ou confira, gere e edite tudo aqui dentro.</p>
      </div>
      <div class="cr-menu-bar">
        <button type="button" id="cr-btn-gpt" class="cr-menu-btn">✦ ChatGPT &amp; JSON</button>
        <button type="button" id="cr-btn-flow" class="cr-menu-btn">⚙ Fluxo HQ (Nodes)</button>
        <button type="button" id="cr-btn-review" class="cr-menu-btn">✓ Conferir Lote</button>
        <button type="button" id="cr-btn-videos" class="cr-menu-btn">▶ Vídeos</button>
        <button type="button" id="cr-btn-editor" class="cr-menu-btn">✂ Editar (CapCut)</button>
      </div>
    </section>

    <form id="create-form">
      <div class="grid">
        <section class="panel">
          <div class="panel-head"><h2>Prompts &amp; Configuração</h2><span id="take-count" class="counter">0 take(s)</span></div>
          <label for="batch-name">Nome do lote</label>
          <input id="batch-name" name="name" maxlength="200" required>
          <div class="settings compact-settings">
            <label>Modelo<select name="model_id"><option value="omni-1.2">Omni 1.2 Flash</option><option value="veo-fast">Veo 3.1 Fast</option><option value="veo-lite">Veo 3.1 Lite</option></select></label>
            <label>Formato<select name="aspect"><option>9:16</option><option>16:9</option><option>1:1</option></select></label>
            <label>Duração<select name="duration_seconds"><option>3</option><option>4</option><option>5</option><option selected>6</option><option>8</option><option>10</option><option>12</option><option>15</option></select></label>
            <label>Resolução<select name="resolution"><option value="">Automática</option><option>360p</option><option>720p</option></select></label>
            <label>Separar prompts<select name="split_mode"><option value="line">1 por linha (rápido)</option><option value="dash">Por bloco (---)</option></select></label>
          </div>
          <label for="prompt-text">Prompts da fila ou JSON</label>
          <textarea id="prompt-text" name="prompts" placeholder="Cole 1 prompt por linha (ex.: 10 vídeos de uma vez)…&#10;Ou cole aqui um JSON de Fluxo / Lote."></textarea>
          <div class="prompt-tools">
            <label id="attach-label" class="button primary" for="prompt-images">Anexar imagem<input id="prompt-images" class="sr-only" type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple></label>
            <button id="create-image" type="button">Criar imagem</button>
            <button id="open-library" type="button">Biblioteca</button>
            <label class="button" for="json-file">Subir JSON<input id="json-file" class="sr-only" type="file" accept="application/json,.json" multiple></label>
          </div>
          <div id="attached-images" class="hint"></div>
          <div id="import-note"></div>
        </section>

        <aside class="panel">
          <div class="panel-head"><h2>Prévia da Fila</h2><span id="draft-state" class="hint">Rascunho local</span></div>
          <div id="take-list" class="takes"></div>
        </aside>
      </div>

      <div class="sticky-actions">
        <div><strong>Enviar fila</strong><br><small>Mande direto para gerar em sequência ou abra no modal Conferir.</small></div>
        <div class="button-row">
          <button id="save-review-btn" type="submit">Salvar e Conferir (Modal)</button>
          <button id="send-direct-btn" class="primary" type="button">Mandar direto para a fila →</button>
        </div>
      </div>
    </form>`;

  const form=root.querySelector('#create-form');
  lastQueueKey=null;
  form.name.value=draft.name||`Lote ${new Date().toLocaleDateString('pt-BR')}`;
  form.prompts.value=draft.text||'';
  if(form.split_mode)form.split_mode.value=draft.split_mode||'line';
  applySettings(form,draft.settings);
  renderAttachments(root,form);renderQueue(root,form);

  root.querySelector('#cr-btn-gpt').addEventListener('click',()=>openJsonAndGptDialog(root,form));
  root.querySelector('#cr-btn-flow').addEventListener('click',()=>openFlowModal(root,async batchId=>{await storeReload();openVideosModal(root,batchId)}));
  root.querySelector('#cr-btn-review').addEventListener('click',()=>openReviewModal(root,store.batches[0]?.id));
  root.querySelector('#cr-btn-videos').addEventListener('click',()=>openVideosModal(root,store.batches[0]?.id));
  root.querySelector('#cr-btn-editor').addEventListener('click',()=>openEditorModal(root));

  root.querySelector('#prompt-images').addEventListener('change',event=>attachFiles([...event.target.files],root,form));
  root.querySelector('#create-image').addEventListener('click',()=>createImageDialog(root,form));
  root.querySelector('#open-library').addEventListener('click',()=>libraryDialog(root,form));
  for(const event of['input','change'])form.addEventListener(event,()=>{persist(form);renderQueue(root,form)});

  root.querySelector('#json-file').addEventListener('change',async event=>{
    const files=[...event.target.files];
    if(!files.length)return;
    try{
      if(files.length===1){
        const raw=await files[0].text();
        const parsed=parseJsonKind(raw);
        if(parsed?.kind==='flow'){
          openFlowModal(root,async batchId=>{await storeReload();openVideosModal(root,batchId)},parsed.raw);
          return;
        }
      }
      const documents=[];
      for(const file of files)documents.push({name:file.name,document:JSON.parse(await file.text())});
      await applyManifestDocs(documents,root,form);
      toast(`${importPreview.valid_indexes.length} take(s) carregado(s) do JSON.`);
    }catch(error){
      const note=root.querySelector('#import-note');
      if(note){note.className='notice bad';note.textContent=error.message}
      toast(error.message);
    }finally{event.target.value=''}
  });

  async function createBatchFromForm(autoOutput=false){
    const manual=manualRows(form);
    if(!manual.length&&!importPreview?.valid_indexes?.length){throw new Error('Adicione pelo menos um prompt ou carregue um JSON.')}
    let batch=await api('/api/batches',{method:'POST',body:{name:form.name.value.trim(),defaults:settingsFrom(form),max_credits:importPreview?.max_credits??null,output_dir:importPreview?.output_dir||'',auto_output:autoOutput}});
    if(importPreview?.valid_indexes?.length){
      const result=await api(`/api/imports/${importPreview.preview_id}/commit`,{method:'POST',body:{preview_hash:importPreview.preview_hash,batch_id:batch.id,expected_revision:batch.revision,accepted_indexes:importPreview.valid_indexes,idempotency_key:uid('import')}});
      batch.revision=result.revision;
    }
    if(manual.length){
      const added=await api(`/api/batches/${batch.id}/items`,{method:'POST',body:{expected_revision:batch.revision,idempotency_key:uid('items'),items:manual.map(({_manual,...item})=>item)}});
      if(added?.revision)batch.revision=added.revision;
    }
    localStorage.removeItem('flowlocal-draft');
    return batch;
  }

  form.addEventListener('submit',async event=>{
    event.preventDefault();
    const submit=root.querySelector('#save-review-btn');
    submit.disabled=true;
    try{
      const batch=await createBatchFromForm();
      await storeReload();
      await openReviewModal(root,batch.id);
    }catch(error){toast(error.message)}
    finally{submit.disabled=false}
  });

  root.querySelector('#send-direct-btn').addEventListener('click',async ev=>{
    const manual=manualRows(form);
    const total=manual.length+(importPreview?.valid_indexes?.length||0);
    if(!total){toast('Adicione pelo menos um prompt.');return}
    if(!confirm(`Enviar ${total} vídeo(s) direto para a fila de geração agora?`))return;
    const btn=ev.currentTarget;
    btn.disabled=true;btn.textContent='Enfileirando…';
    try{
      const batch=await createBatchFromForm(true);
      const plan=await api('/api/plan',{method:'POST',body:{batch_id:batch.id,expected_revision:batch.revision}});
      const itemsRes=await api(`/api/batches/${batch.id}/items?limit=500`);
      const itemIds=(itemsRes.items||[]).map(x=>x.id);
      await api('/api/queue-direct',{method:'POST',body:{
        batch_id:batch.id,
        expected_revision:plan.revision||batch.revision,
        plan_hash:plan.plan_hash,
        idempotency_key:uid('direct'),
        item_ids:itemIds,
        confirmed:true,
        risk:true,
        unknown_cost_accepted:true,
      }});
      await storeReload();
      toast(`${itemIds.length} vídeo(s) enviado(s) direto para a fila!`);
      await openVideosModal(root,batch.id);
    }catch(error){
      toast(error.message);
    }finally{
      btn.disabled=false;
      btn.textContent='Mandar direto para a fila →';
    }
  });
}

async function storeReload(){const page=await api('/api/batches?limit=50');store.batches=page.items}
