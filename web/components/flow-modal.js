import{api,fileBase64,go,toast}from'../state.js';

const TYPE_LABELS={
  gpt_json:'GPT JSON',
  image_gen:'IMAGEM HQ',
  scene_fanout:'CENAS',
  video_batch:'VÍDEOS FLOW',
};

const STATE_LABELS={
  PENDING:'Na fila',
  RUNNING:'Rodando…',
  SUCCEEDED:'✓ Passou',
  FAILED:'✕ Falhou',
  SKIPPED:'Pulado',
};

function describeRef(ref){
  if(typeof ref==='string')return ref;
  if(ref&&typeof ref==='object')return `${ref.ref}${ref.role&&ref.role!=='subject'?` (${ref.role})`:''}`;
  return '';
}

function summarizePrompt(prompt){
  if(!prompt)return '';
  if(typeof prompt==='string')return prompt;
  return prompt.objetivo||prompt.goal||prompt.cena||prompt.scene||JSON.stringify(prompt);
}

export function openFlowModal(root,onVideosDone,initialJsonText=''){
  let preview=null;
  let slotValues={};
  let activeRunId=null;
  let pollTimer=null;
  let previewTimer=null;

  const dialog=document.createElement('dialog');
  dialog.className='flow-dialog';
  dialog.innerHTML=`
    <div class="dialog-body flow-dialog-body">
      <div class="panel-head">
        <div>
          <h2>Fluxo automático por JSON</h2>
          <p class="lead">Cole o JSON estruturado, anexe as fotos nos slots e acompanhe cada node ao vivo.</p>
        </div>
        <button class="quiet close-flow" type="button" aria-label="Fechar">×</button>
      </div>
      <div id="flow-inline-toast" class="flow-inline-toast" hidden></div>

      <section class="flow-section">
        <div class="flow-toolbar">
          <button type="button" id="flow-load-template">Carregar modelo HQ (JSON estruturado)</button>
          <button type="button" id="flow-paste-json">Colar JSON</button>
          <button type="button" id="flow-toggle-gpt">Pedir JSON ao GPT</button>
        </div>
        <div id="flow-gpt-bar" class="flow-gpt-bar" hidden>
          <input id="flow-gpt-brief" type="text" placeholder="Ex.: campanha 3 vídeos de caixa de som portátil com sheet + fundo + close LED">
          <button type="button" id="flow-gpt-send" class="primary">Gerar JSON</button>
        </div>
        <label for="flow-json-input">JSON do fluxo</label>
        <textarea id="flow-json-input" class="flow-json-input mono" spellcheck="false" placeholder='Cole aqui o JSON com "inputs" e "nodes"...'></textarea>
        <div id="flow-json-status" class="hint"></div>
      </section>

      <section class="flow-section">
        <div class="panel-head">
          <h3>Entradas do fluxo (slots)</h3>
          <span id="flow-slots-count" class="hint">0 slot(s)</span>
        </div>
        <div id="flow-slots" class="flow-slots">
          <div class="empty">Cole um JSON válido para exibir os slots necessários.</div>
        </div>
      </section>

      <section class="flow-section">
        <div class="panel-head">
          <h3>Mapa ao vivo dos nodes</h3>
          <span id="flow-run-badge" class="hint">Pronto para montar</span>
        </div>
        <div id="flow-canvas" class="flow-canvas">
          <div class="empty">Os nodes aparecerão aqui em ordem automática.</div>
        </div>
      </section>

      <section class="flow-section">
        <div class="panel-head">
          <h3>Fila detalhada de execução</h3>
          <span id="flow-queue-count" class="hint">0 item(ns)</span>
        </div>
        <div id="flow-queue-list" class="flow-queue-list">
          <div class="empty">Quando o JSON for validado, cada etapa da fila (referências, tamanho, prompts JSON e vídeos) aparece aqui.</div>
        </div>
      </section>

      <div id="flow-error-box"></div>

      <label class="check flow-confirm-check">
        <input id="flow-confirm" type="checkbox">
        Entendo que este fluxo vai gerar imagens em alta qualidade no ChatGPT Web e vídeos pagos no Google Flow automaticamente em sequência.
      </label>

      <div class="button-row flow-footer-actions">
        <button type="button" class="cancel close-flow-btn">Fechar</button>
        <button type="button" id="flow-open-videos" class="button" hidden>Abrir em Vídeos →</button>
        <button type="button" id="flow-start" class="primary" disabled>Iniciar fluxo automático</button>
      </div>
    </div>`;

  root.append(dialog);
  dialog.showModal();

  const jsonInput=dialog.querySelector('#flow-json-input');
  const jsonStatus=dialog.querySelector('#flow-json-status');
  const slotsBox=dialog.querySelector('#flow-slots');
  const slotsCount=dialog.querySelector('#flow-slots-count');
  const canvas=dialog.querySelector('#flow-canvas');
  const queueList=dialog.querySelector('#flow-queue-list');
  const queueCount=dialog.querySelector('#flow-queue-count');
  const runBadge=dialog.querySelector('#flow-run-badge');
  const errorBox=dialog.querySelector('#flow-error-box');
  const confirmBox=dialog.querySelector('#flow-confirm');
  const startBtn=dialog.querySelector('#flow-start');
  const openVideosBtn=dialog.querySelector('#flow-open-videos');
  const inlineToast=dialog.querySelector('#flow-inline-toast');

  function modalNotice(msg,isBad=false){
    inlineToast.textContent=msg;
    inlineToast.className=isBad?'flow-inline-toast bad':'flow-inline-toast';
    inlineToast.hidden=false;
    clearTimeout(modalNotice.timer);
    modalNotice.timer=setTimeout(()=>{inlineToast.hidden=true},5500);
  }

  function formatApiError(err){
    if(err?.status===404){
      return 'O servidor antigo ainda está aberto na porta 8765. Clique em Encerrar no topo e abra INICIAR.bat novamente.';
    }
    return err?.message||'Falha inesperada.';
  }

  function cleanup(){
    clearInterval(pollTimer);
    clearTimeout(previewTimer);
    dialog.remove();
  }
  dialog.querySelector('.close-flow').addEventListener('click',()=>dialog.close());
  dialog.querySelector('.close-flow-btn').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',cleanup);

  function renderSlots(){
    const inputs=preview?.inputs||[];
    slotsCount.textContent=`${inputs.length} slot(s)`;
    slotsBox.replaceChildren();
    if(!inputs.length){
      slotsBox.innerHTML='<div class="empty">Este fluxo não exige entradas manuais.</div>';
      updateStartState();
      return;
    }
    for(const slot of inputs){
      const card=document.createElement('div');
      card.className='flow-slot-card';
      const head=document.createElement('div');
      head.className='flow-slot-head';
      const title=document.createElement('strong');
      title.textContent=slot.label+(slot.required?' *':'');
      const key=document.createElement('span');
      key.className='mono hint';
      key.textContent=`@input.${slot.id}`;
      head.append(title,key);
      card.append(head);

      if(slot.type==='image'){
        const row=document.createElement('div');
        row.className='flow-slot-image-row';
        const current=slotValues[slot.id];
        if(current?.preview_url){
          const img=document.createElement('img');
          img.src=current.preview_url;
          img.alt=current.name||slot.label;
          row.append(img);
        }else{
          const placeholder=document.createElement('span');
          placeholder.className='image-placeholder';
          placeholder.textContent='IMG';
          row.append(placeholder);
        }
        const actions=document.createElement('div');
        actions.className='flow-slot-actions';
        const fileId=`slot-file-${slot.id}`;
        const label=document.createElement('label');
        label.className='button';
        label.setAttribute('for',fileId);
        label.textContent=current?'Trocar foto':'Anexar foto';
        const fileInput=document.createElement('input');
        fileInput.id=fileId;
        fileInput.className='sr-only';
        fileInput.type='file';
        fileInput.accept='image/png,image/jpeg,image/gif,image/webp';
        fileInput.addEventListener('change',async ev=>{
          const file=ev.currentTarget.files[0];
          if(!file)return;
          label.textContent='Enviando…';
          try{
            const staged=await api('/api/stage',{method:'POST',body:{name:file.name,content:await fileBase64(file)}});
            slotValues[slot.id]={...staged,name:file.name};
            modalNotice(`Foto "${file.name}" anexada em ${slot.label}.`);
            renderSlots();
          }catch(err){modalNotice(formatApiError(err),true);renderSlots()}
        });
        label.append(fileInput);

        const libBtn=document.createElement('button');
        libBtn.type='button';
        libBtn.textContent='Da Biblioteca';
        libBtn.addEventListener('click',async()=>{
          try{
            const lib=await api('/api/library');
            if(!lib.items.length){modalNotice('A Biblioteca está vazia. Anexe uma foto do seu computador.',true);return}
            const first=lib.items[0];
            slotValues[slot.id]=first;
            modalNotice(`Usando "${first.name}" em ${slot.label}.`);
            renderSlots();
          }catch(err){modalNotice(formatApiError(err),true)}
        });

        actions.append(label,libBtn);
        row.append(actions);
        card.append(row);
        if(current?.name){
          const fileTag=document.createElement('small');
          fileTag.className='hint flow-slot-filename';
          fileTag.textContent=`Selecionado: ${current.name}`;
          card.append(fileTag);
        }
      }else{
        if(slotValues[slot.id]===undefined&&slot.default)slotValues[slot.id]=slot.default;
        const textInput=document.createElement('input');
        textInput.type='text';
        textInput.value=slotValues[slot.id]??'';
        textInput.placeholder=slot.default||'Digite aqui…';
        textInput.addEventListener('input',ev=>{slotValues[slot.id]=ev.currentTarget.value;updateStartState()});
        card.append(textInput);
      }
      slotsBox.append(card);
    }
    updateStartState();
  }

  function renderQueue(nodes){
    queueList.replaceChildren();
    if(!nodes||!nodes.length){
      queueCount.textContent='0 item(ns)';
      queueList.innerHTML='<div class="empty">Quando o JSON for validado, cada etapa da fila aparece aqui.</div>';
      return;
    }
    queueCount.textContent=`${nodes.length} etapa(s)`;
    nodes.forEach((node,idx)=>{
      const cfg=node.config||node;
      const state=node.state||'PENDING';
      const row=document.createElement('article');
      row.className=`flow-queue-item state-${state.toLowerCase()}`;

      const top=document.createElement('div');
      top.className='flow-queue-top';
      const left=document.createElement('div');
      left.className='flow-queue-title';
      const num=document.createElement('span');
      num.className='take-number';
      num.textContent=String(idx+1).padStart(2,'0');
      const name=document.createElement('strong');
      name.textContent=node.label||node.id;
      const type=document.createElement('span');
      type.className='mono hint';
      type.textContent=`${TYPE_LABELS[node.node_type||node.type]||node.type}${cfg.size?` · ${cfg.size}`:''}`;
      left.append(num,name,type);

      const badge=document.createElement('span');
      badge.className=`flow-state-pill state-${state.toLowerCase()}`;
      badge.textContent=STATE_LABELS[state]||state;
      top.append(left,badge);
      row.append(top);

      const refs=(cfg.refs||[]).map(describeRef).filter(Boolean);
      if(refs.length){
        const refLine=document.createElement('div');
        refLine.className='flow-queue-refs mono';
        refLine.textContent='Refs: '+refs.join(' · ');
        row.append(refLine);
      }

      if(cfg.items&&cfg.items.length){
        const vidLine=document.createElement('div');
        vidLine.className='flow-queue-refs';
        vidLine.textContent=`Vídeos (${cfg.items.length}): `+cfg.items.map(it=>`${it.output_name} [${it.asset||'t2v'}]`).join(' · ');
        row.append(vidLine);
      }

      const summaryText=summarizePrompt(cfg.prompt);
      if(summaryText){
        const summaryP=document.createElement('p');
        summaryP.className='flow-queue-summary';
        summaryP.textContent=summaryText;
        row.append(summaryP);
      }

      if(typeof cfg.prompt==='object'&&cfg.prompt!==null){
        const details=document.createElement('details');
        details.className='flow-queue-json';
        const sum=document.createElement('summary');
        sum.textContent='Ver JSON do prompt';
        const pre=document.createElement('pre');
        pre.className='mono';
        pre.textContent=JSON.stringify(cfg.prompt,null,2);
        details.append(sum,pre);
        row.append(details);
      }

      if(node.error_message){
        const err=document.createElement('div');
        err.className='flow-queue-error';
        err.textContent=`Falhou (${node.error_code||'ERRO'}): ${node.error_message}`;
        row.append(err);
      }

      queueList.append(row);
    });
  }

  function renderNodes(nodes,activeNodeId=null){
    canvas.replaceChildren();
    if(!nodes||!nodes.length){
      canvas.innerHTML='<div class="empty">Os nodes aparecerão aqui em ordem automática.</div>';
      renderQueue([]);
      return;
    }
    const track=document.createElement('div');
    track.className='flow-track';
    nodes.forEach((node,idx)=>{
      if(idx>0){
        const arrow=document.createElement('div');
        arrow.className='flow-arrow';
        arrow.textContent='→';
        track.append(arrow);
      }
      const state=node.state||'PENDING';
      const isActive=activeNodeId?node.node_id===activeNodeId||node.id===activeNodeId:state==='RUNNING';
      const card=document.createElement('article');
      card.className=`flow-node state-${state.toLowerCase()}${isActive?' is-active':''}`;
      const badge=document.createElement('div');
      badge.className='flow-node-top';
      badge.innerHTML=`<span class="flow-node-type">${TYPE_LABELS[node.node_type||node.type]||node.type}</span><span class="flow-state-pill state-${state.toLowerCase()}">${STATE_LABELS[state]||state}</span>`;
      const title=document.createElement('h4');
      title.textContent=node.label||node.id;
      const meta=document.createElement('small');
      meta.className='mono hint';
      meta.textContent=`#${node.node_id||node.id}`;
      card.append(badge,title,meta);

      if(isActive){
        const pulse=document.createElement('div');
        pulse.className='flow-node-running-note';
        pulse.textContent='Processando agora…';
        card.append(pulse);
      }

      const out=node.output;
      if(out?.preview_url){
        const thumb=document.createElement('img');
        thumb.className='flow-node-thumb';
        thumb.src=out.preview_url;
        thumb.alt=node.label||'Saída';
        card.append(thumb);
      }else if(Array.isArray(out?.items)&&out.items.length){
        const strip=document.createElement('div');
        strip.className='flow-node-strip';
        for(const item of out.items){
          if(item.preview_url){
            const img=document.createElement('img');
            img.src=item.preview_url;
            img.alt=`Cena ${item.index||''}`;
            strip.append(img);
          }
        }
        card.append(strip);
      }else if(Array.isArray(out?.videos)&&out.videos.length){
        const vids=document.createElement('div');
        vids.className='flow-node-videos';
        vids.textContent=`✓ ${out.videos.length} vídeo(s) validado(s)`;
        card.append(vids);
      }else if(out?.json){
        const tag=document.createElement('div');
        tag.className='flow-node-videos';
        tag.textContent='✓ JSON recebido';
        card.append(tag);
      }
      if(node.error_message){
        const err=document.createElement('div');
        err.className='flow-node-err';
        err.textContent=`✕ ${node.error_code||'ERRO'}: ${node.error_message}`;
        card.append(err);
      }
      track.append(card);
    });
    canvas.append(track);
    renderQueue(nodes);
    const activeEl=canvas.querySelector('.flow-node.is-active');
    if(activeEl){
      activeEl.scrollIntoView({behavior:'smooth',block:'nearest',inline:'center'});
    }
  }

  function updateStartState(){
    const hasPreview=Boolean(preview&&preview.nodes?.length);
    const inputsReady=(preview?.inputs||[]).every(slot=>{
      if(!slot.required)return true;
      const val=slotValues[slot.id];
      return slot.type==='image'?Boolean(val?.id):Boolean(String(val||'').trim());
    });
    startBtn.disabled=Boolean(activeRunId)||!hasPreview||!inputsReady||!confirmBox.checked;
  }

  async function runPreview(){
    const raw=jsonInput.value.trim();
    if(!raw){
      preview=null;
      jsonStatus.textContent='';
      renderSlots();
      renderNodes([]);
      return;
    }
    try{
      preview=await api('/api/flows/preview',{method:'POST',body:{spec:raw}});
      jsonStatus.className='notice good';
      jsonStatus.textContent=`Fluxo válido: ${preview.summary.total_nodes} node(s) (${preview.summary.image_nodes} imagem, ${preview.summary.video_batches} lote de vídeo).`;
      renderSlots();
      renderNodes(preview.nodes);
    }catch(err){
      preview=null;
      jsonStatus.className='notice bad';
      jsonStatus.textContent=formatApiError(err);
      renderSlots();
      renderNodes([]);
    }
  }

  jsonInput.addEventListener('input',()=>{
    clearTimeout(previewTimer);
    previewTimer=setTimeout(runPreview,280);
  });
  confirmBox.addEventListener('change',updateStartState);

  dialog.querySelector('#flow-load-template').addEventListener('click',async()=>{
    try{
      const tpl=await api('/api/flows/template');
      jsonInput.value=JSON.stringify(tpl,null,2);
      await runPreview();
      modalNotice('Modelo de fluxo HQ carregado.');
    }catch(err){modalNotice(formatApiError(err),true);jsonStatus.className='notice bad';jsonStatus.textContent=formatApiError(err)}
  });

  dialog.querySelector('#flow-paste-json').addEventListener('click',async()=>{
    try{
      const text=await navigator.clipboard.readText();
      jsonInput.value=text;
      await runPreview();
    }catch{modalNotice('Permissão de clipboard bloqueada; cole com Ctrl+V na caixa abaixo.',true)}
  });

  const gptBar=dialog.querySelector('#flow-gpt-bar');
  dialog.querySelector('#flow-toggle-gpt').addEventListener('click',()=>{gptBar.hidden=!gptBar.hidden});
  dialog.querySelector('#flow-gpt-send').addEventListener('click',async ev=>{
    const btn=ev.currentTarget;
    const brief=dialog.querySelector('#flow-gpt-brief').value.trim();
    if(!brief){modalNotice('Descreva o fluxo que você quer que o GPT monte.',true);return}
    btn.disabled=true;
    btn.textContent='Consultando GPT…';
    try{
      const tpl=await api('/api/flows/template');
      const askPrompt=`Gere um JSON de fluxo compatível com exatamente esta estrutura (version: 1, name, inputs, video_defaults, nodes onde cada prompt de image_gen é um objeto JSON detalhado de alta qualidade). Exemplo de referência:\n${JSON.stringify(tpl)}\n\nPedido do usuário: ${brief}`;
      const res=await api('/api/gpt-ask',{method:'POST',body:{prompt:askPrompt,new_chat:true}});
      jsonInput.value=JSON.stringify(res.json,null,2);
      await runPreview();
      modalNotice('JSON gerado pelo GPT.');
    }catch(err){modalNotice(formatApiError(err),true)}
    finally{btn.disabled=false;btn.textContent='Gerar JSON'}
  });

  async function pollRun(){
    if(!activeRunId)return;
    try{
      const run=await api(`/api/flows/${encodeURIComponent(activeRunId)}`);
      renderNodes(run.nodes,run.active_node_id);
      runBadge.className=run.state==='SUCCEEDED'?'flow-state-pill state-succeeded':run.state==='FAILED'?'flow-state-pill state-failed':'flow-state-pill state-running';
      runBadge.textContent=run.state==='RUNNING'?`Rodando: ${run.active_node_id||'...'}`:STATE_LABELS[run.state]||run.state;
      if(run.state==='SUCCEEDED'){
        clearInterval(pollTimer);
        activeRunId=null;
        startBtn.textContent='✓ Fluxo concluído';
        if(run.batch_id){
          openVideosBtn.hidden=false;
          openVideosBtn.onclick=()=>{dialog.close();if(onVideosDone)onVideosDone(run.batch_id);else go('videos',run.batch_id)};
        }
        modalNotice('Fluxo automático concluído com sucesso!');
      }else if(run.state==='FAILED'){
        clearInterval(pollTimer);
        activeRunId=null;
        startBtn.disabled=false;
        startBtn.textContent='Tentar novamente';
        errorBox.replaceChildren();
        const box=document.createElement('div');
        box.className='flow-error-banner';
        box.innerHTML=`<strong>✕ Fluxo interrompido em "${run.nodes.find(n=>n.state==='FAILED')?.label||'etapa'}"</strong><p></p>`;
        box.querySelector('p').textContent=`${run.error_code||'FAILED'}: ${run.error_message||'Falha no fluxo.'}`;
        errorBox.append(box);
      }
    }catch{}
  }

  startBtn.addEventListener('click',async()=>{
    if(!preview)return;
    errorBox.replaceChildren();
    startBtn.disabled=true;
    startBtn.textContent='Executando fluxo…';
    const payloadInputs={};
    for(const slot of preview.inputs){
      payloadInputs[slot.id]=slot.type==='image'?(slotValues[slot.id]?.id||null):(slotValues[slot.id]??'');
    }
    try{
      const started=await api('/api/flows/run',{
        method:'POST',
        body:{
          spec:preview.spec,
          inputs:payloadInputs,
          confirmed:confirmBox.checked,
          risk:confirmBox.checked,
        },
      });
      activeRunId=started.id;
      runBadge.className='flow-state-pill state-running';
      runBadge.textContent='Executando…';
      renderNodes(started.nodes,started.active_node_id);
      pollTimer=setInterval(pollRun,1200);
    }catch(err){
      startBtn.disabled=false;
      startBtn.textContent='Iniciar fluxo automático';
      const box=document.createElement('div');
      box.className='flow-error-banner';
      box.innerHTML='<strong>✕ Não foi possível iniciar o fluxo</strong><p></p>';
      box.querySelector('p').textContent=formatApiError(err);
      errorBox.append(box);
    }
  });

  if(initialJsonText){
    jsonInput.value=initialJsonText;
    runPreview();
  }
}
