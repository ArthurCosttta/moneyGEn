import{api,html,toast}from'../state.js';
import{openPageEditorModal}from'../components/page-editor.js';
import{openFlowModal}from'../components/flow-modal.js';

export async function renderPaginas(root){
  let downloads={pages:[],media:[]};
  try{downloads=await api('/api/spy/downloads')}catch{}
  const pages=downloads.pages||[];

  root.innerHTML=`
    <section class="page-head">
      <div>
        <h1>Páginas Clonadas.</h1>
        <p class="lead">Clone qualquer página de oferta, edite textos, links e imagens visualmente e envie para o ChatGPT montar sua campanha.</p>
      </div>
    </section>

    <form id="pg-clone-form" class="panel" style="padding:14px 16px;margin-bottom:16px">
      <div class="sp-search-shell">
        <input id="pg-url-input" type="url" required placeholder="https://sitedoconcorrente.com.br/oferta">
        <button type="submit" id="pg-clone-btn" class="primary">Clonar e Abrir Editor →</button>
      </div>
    </form>

    <div class="sp-cards-clean" id="pg-grid">
      ${pages.length?pages.map(p=>`
        <article class="sp-card-light">
          <div class="sp-card-body">
            <div class="row" style="justify-content:space-between">
              <span class="badge">${p.name.endsWith('-editada.html')?'Editada':'Original'}</span>
              <small class="hint mono">${Math.max(1,Math.round((p.size_bytes||0)/1024))} KB</small>
            </div>
            <div class="sp-card-title mono" style="font-size:12px">${html(p.name)}</div>
          </div>
          <div class="sp-icon-bar">
            <button type="button" class="sp-icon-btn primary pg-edit" data-fname="${html(p.name)}" data-url="${html(p.download_url)}">✏ Editar Visual</button>
            <button type="button" class="sp-icon-btn pg-gpt" data-fname="${html(p.name)}">✦ Oferta + GPT</button>
            <a class="button sp-icon-btn" href="${html(p.download_url)}" target="_blank" rel="noopener">Abrir ↗</a>
          </div>
        </article>`).join(''):'<div class="panel"><div class="empty">Nenhuma página clonada ainda. Cole uma URL acima ou clique em Clonar em Pesquisar.</div></div>'}
    </div>`;

  root.querySelector('#pg-clone-form').addEventListener('submit',async ev=>{
    ev.preventDefault();
    const btn=root.querySelector('#pg-clone-btn');
    const url=root.querySelector('#pg-url-input').value.trim();
    btn.disabled=true;
    btn.textContent='Clonando…';
    try{
      const cap=await api('/api/spy/capture',{method:'POST',body:{url}});
      openPageEditorModal(root,cap,()=>renderPaginas(root));
    }catch(err){toast(err.message)}
    finally{btn.disabled=false;btn.textContent='Clonar e Abrir Editor →'}
  });

  root.querySelectorAll('.pg-edit').forEach(btn=>btn.addEventListener('click',()=>{
    openPageEditorModal(root,{filename:btn.dataset.fname,download_url:btn.dataset.url},()=>renderPaginas(root));
  }));

  root.querySelectorAll('.pg-gpt').forEach(btn=>btn.addEventListener('click',async()=>{
    btn.disabled=true;
    btn.textContent='Gerando no GPT…';
    try{
      const res=await api('/api/spy/offer-to-flow',{method:'POST',body:{page_filename:btn.dataset.fname}});
      openFlowModal(root,null,JSON.stringify(res.flow_spec,null,2));
    }catch(err){
      toast(err.message);
    }finally{
      btn.disabled=false;
      btn.textContent='✦ Oferta + GPT';
    }
  }));
}
