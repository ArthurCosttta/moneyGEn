import{api,html,toast}from'../state.js';

export async function openPageEditorModal(root,pageInfo,onSaved){
  let libItems=[];
  try{const l=await api('/api/library');libItems=l.items||[]}catch{}

  const dialog=document.createElement('dialog');
  dialog.className='pe-dialog';
  dialog.innerHTML=`
    <div class="pe-shell">
      <header class="pe-head">
        <div>
          <strong>Editor Visual de Página Clonada</strong>
          <small class="hint mono">${html(pageInfo.filename||pageInfo.name||'pagina.html')}</small>
        </div>
        <div class="row">
          <button id="pe-undo" type="button" class="quiet" disabled>↩ Desfazer</button>
          <button id="pe-save" type="button" class="primary">Salvar & Baixar HTML ↓</button>
          <button id="pe-close" type="button" class="quiet">Fechar ×</button>
        </div>
      </header>
      <div class="pe-body">
        <div class="pe-canvas-wrap">
          <iframe id="pe-frame" sandbox="allow-same-origin" src="${html(pageInfo.download_url)}"></iframe>
        </div>
        <aside class="pe-inspector">
          <div class="panel-head">
            <h3>Elemento</h3>
            <span id="pe-tag" class="badge">Clique na página ←</span>
          </div>
          <div id="pe-fields" class="pe-fields">
            <p class="hint">Clique em qualquer texto, botão, preço, link ou imagem dentro da página ao lado para editar.</p>
          </div>
          <hr class="cc-sep">
          <div class="pe-bulk">
            <label>Trocar TODOS os links / checkouts da página
              <input id="pe-bulk-url" type="url" placeholder="https://seucheckout.com/oferta">
            </label>
            <button id="pe-bulk-apply" type="button">Aplicar em todos os botões/links</button>
          </div>
          <div id="pe-saved-box"></div>
        </aside>
      </div>
    </div>`;
  root.append(dialog);
  dialog.showModal();

  const frame=dialog.querySelector('#pe-frame');
  const tagBadge=dialog.querySelector('#pe-tag');
  const fieldsBox=dialog.querySelector('#pe-fields');
  const undoBtn=dialog.querySelector('#pe-undo');
  const savedBox=dialog.querySelector('#pe-saved-box');

  let selectedEl=null;
  const history=[];

  function snapshotUndo(){
    const doc=frame.contentDocument;
    if(!doc)return;
    history.push(doc.body.innerHTML);
    if(history.length>30)history.shift();
    undoBtn.disabled=history.length===0;
  }

  function wireFrame(){
    const doc=frame.contentDocument;
    if(!doc||!doc.body)return;
    const style=doc.createElement('style');
    style.setAttribute('data-flow-editor','1');
    style.textContent=`
      [data-fe-hover]{outline:2px dashed #9ee7ce!important;outline-offset:2px!important;cursor:pointer!important}
      [data-fe-sel]{outline:2px solid #22c55e!important;outline-offset:2px!important}
    `;
    (doc.head||doc.documentElement).append(style);

    let hovered=null;
    doc.addEventListener('mouseover',ev=>{
      const t=ev.target;
      if(!t||t===doc.body||t===doc.documentElement)return;
      if(hovered&&hovered!==t)hovered.removeAttribute('data-fe-hover');
      hovered=t;
      t.setAttribute('data-fe-hover','1');
    },true);

    doc.addEventListener('click',ev=>{
      ev.preventDefault();
      ev.stopPropagation();
      const t=ev.target;
      if(!t||t===doc.body||t===doc.documentElement)return;
      if(selectedEl)selectedEl.removeAttribute('data-fe-sel');
      selectedEl=t;
      selectedEl.setAttribute('data-fe-sel','1');
      renderInspector();
    },true);
  }

  function rgbToHex(rgb){
    const m=String(rgb||'').match(/\d+/g);
    if(!m||m.length<3)return '#ffffff';
    return '#'+m.slice(0,3).map(n=>Number(n).toString(16).padStart(2,'0')).join('');
  }

  function renderInspector(){
    if(!selectedEl){
      tagBadge.textContent='Clique na página ←';
      fieldsBox.innerHTML='<p class="hint">Clique em qualquer elemento na página ao lado para editar texto, link, imagem ou cores.</p>';
      return;
    }
    const tag=selectedEl.tagName.toLowerCase();
    tagBadge.textContent=`<${tag}>`;
    const anchor=selectedEl.closest('a');
    const isImg=tag==='img';
    const cs=frame.contentWindow.getComputedStyle(selectedEl);

    const curImgSrc=isImg?(selectedEl.currentSrc||selectedEl.getAttribute('src')||''):'';
    const imgDim=isImg&&selectedEl.naturalWidth?`${selectedEl.naturalWidth} × ${selectedEl.naturalHeight}px`:'';

    fieldsBox.innerHTML=`
      ${isImg?`
        <div class="pe-img-box">
          <div class="row" style="justify-content:space-between">
            <small class="hint">Imagem atual no site do concorrente</small>
            ${imgDim?`<small class="mono hint">${imgDim}</small>`:''}
          </div>
          <img id="pe-img-preview" class="pe-img-preview" src="${html(curImgSrc)}" alt="Preview da imagem">
        </div>
        <label>URL da nova imagem (src)<input id="pe-src" value="${html(selectedEl.getAttribute('src')||'')}"></label>
        ${libItems.length?`
          <label>Ou trocar pela sua imagem (Biblioteca FlowLocal)
            <select id="pe-lib-img">
              <option value="">Selecionar imagem da biblioteca…</option>
              ${libItems.map(i=>`<option value="${html(i.preview_url)}">${html(i.name)}</option>`).join('')}
            </select>
          </label>`:''}
      `:`
        <label>Texto do elemento<textarea id="pe-text" rows="3">${html(selectedEl.innerText||selectedEl.textContent||'')}</textarea></label>
      `}
      ${anchor?`<label>Link de destino (href)<input id="pe-href" value="${html(anchor.getAttribute('href')||'')}"></label>`:''}
      <div class="settings">
        <label>Cor do texto<input id="pe-col" type="color" value="${rgbToHex(cs.color)}"></label>
        <label>Cor de fundo<input id="pe-bg" type="color" value="${rgbToHex(cs.backgroundColor)}"></label>
      </div>
      <div class="button-row">
        <button id="pe-apply" type="button" class="primary">Aplicar alteração</button>
        <button id="pe-hide" type="button" class="danger quiet">Remover bloco</button>
      </div>`;

    const imgPrev=fieldsBox.querySelector('#pe-img-preview');
    const srcInp=fieldsBox.querySelector('#pe-src');
    srcInp?.addEventListener('input',e=>{if(imgPrev&&e.target.value.trim())imgPrev.src=e.target.value.trim()});
    fieldsBox.querySelector('#pe-lib-img')?.addEventListener('change',e=>{
      if(e.target.value&&srcInp){
        srcInp.value=e.target.value;
        if(imgPrev)imgPrev.src=e.target.value;
      }
    });

    fieldsBox.querySelector('#pe-apply').addEventListener('click',()=>{
      snapshotUndo();
      if(isImg){
        const nextSrc=fieldsBox.querySelector('#pe-src').value.trim();
        if(nextSrc)selectedEl.setAttribute('src',nextSrc);
      }else{
        const nextTxt=fieldsBox.querySelector('#pe-text').value;
        if(selectedEl.children.length===0){
          selectedEl.textContent=nextTxt;
        }else{
          // Replace direct text node(s) cleanly while keeping child icons/spans intact
          const tn=[...selectedEl.childNodes].find(n=>n.nodeType===3&&n.textContent.trim());
          if(tn)tn.textContent=nextTxt;
          else selectedEl.innerText=nextTxt;
        }
      }
      if(anchor){
        const nextHref=fieldsBox.querySelector('#pe-href').value.trim();
        if(nextHref)anchor.setAttribute('href',nextHref);
      }
      selectedEl.style.color=fieldsBox.querySelector('#pe-col').value;
      const bg=fieldsBox.querySelector('#pe-bg').value;
      if(bg&&bg!=='#000000')selectedEl.style.backgroundColor=bg;
      toast('Elemento atualizado.');
    });

    fieldsBox.querySelector('#pe-hide').addEventListener('click',()=>{
      snapshotUndo();
      selectedEl.remove();
      selectedEl=null;
      renderInspector();
      toast('Bloco removido.');
    });
  }

  frame.addEventListener('load',wireFrame);

  undoBtn.addEventListener('click',()=>{
    const prev=history.pop();
    if(prev==null)return;
    frame.contentDocument.body.innerHTML=prev;
    selectedEl=null;
    renderInspector();
    undoBtn.disabled=history.length===0;
  });

  dialog.querySelector('#pe-bulk-apply').addEventListener('click',()=>{
    const newUrl=dialog.querySelector('#pe-bulk-url').value.trim();
    if(!newUrl.startsWith('http')){toast('Informe uma URL válida (https://...).');return}
    const doc=frame.contentDocument;
    if(!doc)return;
    snapshotUndo();
    let count=0;
    doc.querySelectorAll('a[href]').forEach(a=>{a.setAttribute('href',newUrl);count++});
    toast(`${count} link(s) atualizado(s) para o seu destino.`);
  });

  dialog.querySelector('#pe-save').addEventListener('click',async ev=>{
    const doc=frame.contentDocument;
    if(!doc)return;
    const btn=ev.currentTarget;
    btn.disabled=true;
    btn.textContent='Salvando…';
    try{
      const cloneDoc=doc.documentElement.cloneNode(true);
      cloneDoc.querySelectorAll('[data-flow-editor]').forEach(n=>n.remove());
      cloneDoc.querySelectorAll('[data-fe-hover],[data-fe-sel]').forEach(n=>{
        n.removeAttribute('data-fe-hover');
        n.removeAttribute('data-fe-sel');
      });
      const fullHtml='<!doctype html>\n'+cloneDoc.outerHTML;
      const res=await api('/api/spy/save-page',{method:'POST',body:{filename:pageInfo.filename||pageInfo.name,html:fullHtml}});
      savedBox.innerHTML=`<div class="notice good"><strong>Salvo:</strong> ${html(res.filename)}<br><a class="button primary" href="${html(res.download_url)}" download="${html(res.filename)}">Baixar HTML editado ↓</a></div>`;
      toast(`Página salva: ${res.filename}`);
      if(onSaved)await onSaved(res);
    }catch(err){
      toast(err.message);
    }finally{
      btn.disabled=false;
      btn.textContent='Salvar & Baixar HTML ↓';
    }
  });

  dialog.querySelector('#pe-close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>dialog.remove());
}
