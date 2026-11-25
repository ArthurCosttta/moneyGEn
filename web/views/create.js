import{api,fileBase64,go,store,toast,uid}from'../state.js';
import{openFlowModal}from'../components/flow-modal.js';

let draft={};
try{draft=JSON.parse(localStorage.getItem('flowlocal-draft')||'{}')}catch{}
let assets=Array.isArray(draft.assets)?draft.assets:[];
let rows=[];
let importPreview=null;
let saveTimer;
let lastQueueKey=null;

function prompts(value){
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
    localStorage.setItem('flowlocal-draft',JSON.stringify({name:form.name.value,text:form.prompts.value,settings:settingsFrom(form),assets}));
    document.querySelector('#draft-state').textContent='Rascunho salvo.';
  },300);
}

function manualRows(form){
  return prompts(form.prompts.value).map((prompt,index)=>({id:uid('take'),kind:assets.length?'i2v':'t2v',prompt,asset_id:assets.length?assets[index%assets.length].id:null,output_name:null,overrides:{},title:`Take ${index+1}`,source_index:index,_manual:true}));
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

function isFlowJsonText(text){
  const trimmed=(text||'').trim();
  if(!trimmed.startsWith('{'))return false;
  try{const parsed=JSON.parse(trimmed);return Boolean(parsed&&typeof parsed==='object'&&Array.isArray(parsed.nodes))}catch{return false}
}

function renderQueue(root,form){
  const rawPrompt=form.prompts.value.trim();
  const queueKey=JSON.stringify([rawPrompt,assets.map(x=>x.id),importPreview?.preview_hash||null]);
  if(queueKey===lastQueueKey)return;
  lastQueueKey=queueKey;
  const list=root.querySelector('#take-list');
  if(isFlowJsonText(rawPrompt)){
    try{
      const parsed=JSON.parse(rawPrompt);
      const count=parsed.nodes.length;
      root.querySelector('#take-count').textContent=`${count} node(s) no JSON`;
      list.innerHTML=`<div class="notice good"><strong>JSON de fluxo detectado (${parsed.name||'Fluxo'})</strong><br><small>Este JSON define ${count} node(s). Clique abaixo para anexar as fotos nos slots e iniciar a fila visual.</small><div class="button-row"><button type="button" id="launch-pasted-flow" class="primary">Abrir fila de nodes deste JSON →</button></div></div>`;
      list.querySelector('#launch-pasted-flow').addEventListener('click',()=>openFlowModal(root,async batchId=>{await storeReload();go('videos',batchId)},rawPrompt));
      rows=[];
      return;
    }catch{}
  }
  const manual=manualRows(form);
  rows=[...(importPreview?.valid_items||[]),...manual];
  root.querySelector('#take-count').textContent=`${rows.length} take(s)`;
  list.replaceChildren();
  if(!rows.length){list.innerHTML='<div class="empty">Cole um prompt (ou um JSON de fluxo com <span class="mono">nodes</span>). Uma linha contendo apenas <span class="mono">---</span> separa os takes.</div>';return}
  rows.forEach((row,index)=>{const card=document.createElement('article');card.className='take';const asset=assets.find(x=>x.id===row.asset_id);card.innerHTML=`<span class="take-number">${String(index+1).padStart(2,'0')}</span>${asset?`<img src="/media/assets/${asset.id}" alt="">`:'<span class="image-placeholder">T2V</span>'}<div><h3>Take ${index+1}</h3><p></p></div>`;card.querySelector('p').textContent=row.prompt;list.append(card)});
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

function imageFailureGuidance(error){
  const guides={
    GPT_LOGIN_REQUIRED:['A sessão do GPT expirou.','Clique em “Entrar no GPT”, conclua o acesso e tente novamente.'],
    IMAGE_LIMIT_REACHED:['O GPT atingiu o limite de geração de imagens.','Aguarde o prazo mostrado pelo GPT. Repetir agora não resolverá.'],
    IMAGE_PROMPT_REFUSED:['O GPT recusou o conteúdo solicitado.','Simplifique o prompt, remova termos sensíveis ou retire uma imagem de referência.'],
    INVALID_IMAGE:['Uma imagem de referência não pôde ser validada.','Use PNG, JPEG, GIF ou WebP e anexe novamente.'],
    PAYLOAD_LIMIT:['As imagens anexadas ultrapassaram o limite local.','Reduza o número ou o tamanho das referências.'],
    IMAGE_GENERATOR_UNAVAILABLE:['O gerador de imagens do GPT não está disponível.','Entre no GPT e verifique sua conexão antes de tentar novamente.'],
    CHROME_EXTENSION_REQUIRED:['A extensão chrome-use ainda não está conectada ao Chrome.','Clique em “Entrar no GPT”, adicione a extensão na Chrome Web Store e abra chatgpt.com logado.'],
    CHROME_PROFILE_BUSY:['O perfil do navegador do GPT está bloqueado por outra janela do Chrome sem depuração.','Feche outras janelas do Chrome abertas no perfil do GPT (ou reabra pelo INICIAR.bat) e tente novamente.'],
  };
  return guides[error.code]||['O GPT não concluiu a imagem.','Revise o prompt e os detalhes técnicos antes de tentar novamente.'];
}

function renderImageFailure(note,error){
  const[reason,action]=imageFailureGuidance(error);note.className='image-failure';note.replaceChildren();
  const title=document.createElement('h3');title.textContent='Imagem não criada';
  const why=document.createElement('p');why.innerHTML='<strong>O que aconteceu:</strong> ';why.append(document.createTextNode(reason));
  const next=document.createElement('p');next.innerHTML='<strong>Como resolver:</strong> ';next.append(document.createTextNode(action));
  const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='Ver detalhes técnicos';const technical=document.createElement('pre');technical.className='error';technical.textContent=[`Código: ${error.code||'UNKNOWN'}`,`HTTP: ${error.status||'—'}`,`Mensagem: ${error.message}`,error.correlationId?`Identificador: ${error.correlationId}`:'',error.providerMessage?`Resposta do GPT: ${error.providerMessage}`:''].filter(Boolean).join('\n');details.append(summary,technical);note.append(title,why,next,details);
}

function createImageDialog(root,form){
  let references=[];
  const dialog=document.createElement('dialog');
  dialog.innerHTML=`<form class="dialog-body image-create-dialog"><h2>Criar imagem</h2><p class="lead">Descreva a imagem. Se quiser editar ou combinar uma referência, anexe-a abaixo.</p><label for="image-prompt">Prompt da imagem</label><textarea id="image-prompt" name="prompt" maxlength="4000" required placeholder="Ex.: fotografia de produto, fundo branco, luz suave…"></textarea><div class="image-reference-tools"><label class="button" for="image-reference-files">Anexar referência<input id="image-reference-files" class="sr-only" type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple></label><small>Até quatro imagens · 20 MiB no total</small></div><div id="image-references" class="image-references hint">Nenhuma referência anexada.</div><label class="check"><input name="confirmed" type="checkbox" required>Entendo que esta geração pode consumir minha franquia de imagens.</label><div id="image-result"></div><div class="button-row"><button class="cancel" type="button">Cancelar</button><button class="primary" type="submit">Confirmar e criar</button></div></form>`;
  root.append(dialog);dialog.showModal();dialog.querySelector('#image-prompt').focus();
  dialog.querySelector('.cancel').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>dialog.remove());
  const referenceList=dialog.querySelector('#image-references');
  function renderReferences(){
    referenceList.replaceChildren();if(!references.length){referenceList.className='image-references hint';referenceList.textContent='Nenhuma referência anexada.';return}referenceList.className='image-references';
    for(const asset of references){const item=document.createElement('span');const image=document.createElement('img');image.src=asset.preview_url||`/media/assets/${asset.id}`;image.alt='';const name=document.createElement('b');name.textContent=asset.name;const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label',`Remover ${asset.name}`);remove.addEventListener('click',()=>{references=references.filter(value=>value.id!==asset.id);renderReferences()});item.append(image,name,remove);referenceList.append(item)}
  }
  dialog.querySelector('#image-reference-files').addEventListener('change',async event=>{const input=event.currentTarget;const chosen=[...input.files];if(references.length+chosen.length>4){toast('Anexe no máximo quatro referências.');input.value='';return}input.disabled=true;try{for(const file of chosen){const staged=await api('/api/stage',{method:'POST',body:{name:file.name,content:await fileBase64(file)}});if(!references.some(value=>value.id===staged.id))references.push({...staged,name:file.name})}renderReferences();toast(`${chosen.length} referência(s) anexada(s).`)}catch(error){toast(error.message)}finally{input.disabled=false;input.value=''}});
  dialog.querySelector('form').addEventListener('submit',async event=>{
    event.preventDefault();const submit=dialog.querySelector('[type=submit]');const note=dialog.querySelector('#image-result');submit.disabled=true;submit.textContent='Criando imagem…';note.className='notice';note.textContent='Aguarde. A imagem será anexada automaticamente.';
    const imagePrompt=event.currentTarget.elements.namedItem('prompt');const confirmation=event.currentTarget.elements.namedItem('confirmed');
    try{const asset=await api('/api/image-generate',{method:'POST',body:{prompt:imagePrompt.value,confirmed:confirmation.checked,reference_asset_ids:references.map(value=>value.id)}});addAsset(asset);renderAttachments(root,form);renderQueue(root,form);persist(form);toast('Imagem criada e anexada.');dialog.close()}
    catch(error){renderImageFailure(note,error);submit.disabled=false;submit.textContent='Confirmar e criar'}
  });
}

async function libraryDialog(root,form){
  const dialog=document.createElement('dialog');
  dialog.innerHTML=`<div class="dialog-body library-dialog"><div class="panel-head"><div><h2>Biblioteca</h2><p class="lead">Escolha uma imagem ou coloque arquivos diretamente na pasta local.</p></div><button class="quiet close-library" type="button" aria-label="Fechar">×</button></div><div class="button-row library-toolbar"><label class="button primary" for="library-files">Adicionar arquivos<input id="library-files" class="sr-only" type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple></label><button class="open-library" type="button">Abrir pasta</button></div><div id="library-grid" class="library-grid"><div class="empty">Carregando…</div></div></div>`;
  root.append(dialog);dialog.showModal();
  dialog.querySelector('.close-library').addEventListener('click',()=>dialog.close());dialog.addEventListener('close',()=>dialog.remove());
  const grid=dialog.querySelector('#library-grid');
  async function load(){
    const library=await api('/api/library');grid.replaceChildren();
    if(!library.items.length){grid.innerHTML='<div class="empty">A biblioteca está vazia. Adicione uma imagem ou copie arquivos para a pasta.</div>';return}
    for(const item of library.items){
      const card=document.createElement('article');card.className='library-card';
      const image=document.createElement('img');image.src=item.preview_url;image.alt=item.name;
      const name=document.createElement('strong');name.textContent=item.name;
      const meta=document.createElement('small');meta.textContent=`${item.width} × ${item.height}`;
      const actions=document.createElement('div');actions.className='library-actions';
      const attach=document.createElement('button');attach.type='button';attach.className='primary';attach.textContent='Anexar';attach.addEventListener('click',()=>{addAsset(item);renderAttachments(root,form);renderQueue(root,form);persist(form);toast('Imagem anexada ao prompt.');dialog.close()});
      const open=document.createElement('a');open.className='button';open.href=item.preview_url;open.target='_blank';open.rel='noreferrer';open.textContent='Abrir';
      const copy=document.createElement('button');copy.type='button';copy.textContent='Copiar link';copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(new URL(item.preview_url,location.origin).href);toast('Link da imagem copiado.')}catch{toast('Não foi possível copiar o link.')}});
      actions.append(attach,open,copy);card.append(image,name,meta,actions);grid.append(card);
    }
  }
  dialog.querySelector('.open-library').addEventListener('click',()=>api('/api/library/open',{method:'POST',body:{}}).catch(error=>toast(error.message)));
  dialog.querySelector('#library-files').addEventListener('change',async event=>{const input=event.currentTarget;input.disabled=true;try{for(const file of input.files)await api('/api/library/import',{method:'POST',body:{name:file.name,content:await fileBase64(file)}});await load();toast(`${input.files.length} imagem(ns) adicionada(s) à biblioteca.`)}catch(error){toast(error.message)}finally{input.disabled=false;input.value=''}});
  try{await load()}catch(error){grid.innerHTML='<div class="notice bad"></div>';grid.firstElementChild.textContent=error.message}
}

export async function renderCreate(root){
  root.innerHTML=`<section class="page-head"><div><p class="kicker">01 · preparar</p><h1>Crie a fila.</h1><p class="lead">Configure o modelo, escreva o prompt e anexe a imagem. Nada é enviado ao Google nesta tela.</p></div><a class="button" href="/modelo-lote.json" download>Modelo JSON</a></section>
  <form id="create-form"><div class="grid"><section class="panel"><div class="panel-head"><h2>Prompt e modelo</h2><span id="take-count" class="counter">0 take(s)</span></div>
  <label for="batch-name">Nome do lote</label><input id="batch-name" name="name" maxlength="200" required>
  <div class="settings compact-settings"><label>Modelo<select name="model_id"><option value="omni-1.2">Omni 1.2 Flash</option><option value="veo-fast">Veo 3.1 Fast</option><option value="veo-lite">Veo 3.1 Lite</option></select></label><label>Formato<select name="aspect"><option>9:16</option><option>16:9</option><option>1:1</option></select></label><label>Duração<select name="duration_seconds"><option>3</option><option>4</option><option>5</option><option selected>6</option><option>8</option><option>10</option><option>12</option><option>15</option></select></label><label>Resolução<select name="resolution"><option value="">Automática</option><option>360p</option><option>720p</option></select></label></div>
  <label for="prompt-text">Prompt</label><textarea id="prompt-text" name="prompts" placeholder="Descreva o vídeo…\n---\nPróximo vídeo…"></textarea>
  <div class="prompt-tools"><label id="attach-label" class="button primary" for="prompt-images">Anexar imagem<input id="prompt-images" class="sr-only" type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple></label><button id="create-image" type="button">Criar imagem</button><button id="open-library" type="button">Biblioteca</button><button id="open-flow-json" type="button">Fluxo por JSON</button></div><div id="attached-images" class="hint"></div>
  <div class="button-row"><label class="button" for="json-file">Importar JSON<input id="json-file" class="sr-only" type="file" accept="application/json,.json" multiple></label><button id="native-json" type="button">Abrir JSON local</button></div><div id="import-note"></div></section>
  <aside class="panel"><div class="panel-head"><h2>Fila</h2><span id="draft-state" class="hint">Rascunho local</span></div><div id="take-list" class="takes"></div></aside></div>
  <div class="sticky-actions"><div><strong>Nenhum gasto nesta etapa</strong><br><small>A confirmação paga acontece somente em Conferir.</small></div><button class="primary" type="submit">Salvar fila e conferir →</button></div></form>`;
  const form=root.querySelector('#create-form');
  lastQueueKey=null;
  form.name.value=draft.name||`Lote ${new Date().toLocaleDateString('pt-BR')}`;
  form.prompts.value=draft.text||'';
  applySettings(form,draft.settings);
  renderAttachments(root,form);renderQueue(root,form);
  root.querySelector('#prompt-images').addEventListener('change',event=>attachFiles([...event.target.files],root,form));
  root.querySelector('#create-image').addEventListener('click',()=>createImageDialog(root,form));
  root.querySelector('#open-library').addEventListener('click',()=>libraryDialog(root,form));
  root.querySelector('#open-flow-json').addEventListener('click',()=>openFlowModal(root,async batchId=>{await storeReload();go('videos',batchId)}));
  for(const event of['input','change'])form.addEventListener(event,()=>{persist(form);renderQueue(root,form)});
  root.querySelector('#json-file').addEventListener('change',async event=>{try{const documents=[];for(const file of event.target.files)documents.push({name:file.name,document:JSON.parse(await file.text())});importPreview=await api('/api/import-manifest',{method:'POST',body:{documents,assets:assets.map(x=>({reference:x.name,asset_id:x.id}))}});if(importPreview.defaults)applySettings(form,importPreview.defaults);const note=root.querySelector('#import-note');note.className=importPreview.errors.length||importPreview.unresolved_images.length?'notice bad':'notice good';note.textContent=`Prévia: ${importPreview.valid_indexes.length} válido(s), ${importPreview.errors.length} erro(s).`;renderQueue(root,form)}catch(error){toast(error.message)}});
  root.querySelector('#native-json').addEventListener('click',async()=>{try{importPreview=await api('/api/import-native',{method:'POST',body:{}});if(!importPreview.cancelled){form.name.value=importPreview.name;applySettings(form,importPreview.defaults);renderQueue(root,form);toast(`${importPreview.valid_indexes.length} item(ns) importado(s).`)}}catch(error){toast(error.message)}});
  form.addEventListener('submit',async event=>{event.preventDefault();const manual=manualRows(form);if(!manual.length&&!importPreview?.valid_indexes?.length){toast('Adicione pelo menos um prompt.');return}const submit=form.querySelector('[type=submit]');submit.disabled=true;try{let batch=await api('/api/batches',{method:'POST',body:{name:form.name.value.trim(),defaults:settingsFrom(form),max_credits:importPreview?.max_credits??null,output_dir:importPreview?.output_dir||''}});if(importPreview?.valid_indexes?.length){const result=await api(`/api/imports/${importPreview.preview_id}/commit`,{method:'POST',body:{preview_hash:importPreview.preview_hash,batch_id:batch.id,expected_revision:batch.revision,accepted_indexes:importPreview.valid_indexes,idempotency_key:uid('import')}});batch.revision=result.revision}if(manual.length)await api(`/api/batches/${batch.id}/items`,{method:'POST',body:{expected_revision:batch.revision,idempotency_key:uid('items'),items:manual.map(({_manual,...item})=>item)}});localStorage.removeItem('flowlocal-draft');await storeReload();go('conferir',batch.id)}catch(error){toast(error.message);submit.disabled=false}});
}

async function storeReload(){const page=await api('/api/batches?limit=50');store.batches=page.items}
