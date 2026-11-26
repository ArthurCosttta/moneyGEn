import{api,go,refreshBase,route,setNav,store,toast}from'./state.js';
import{renderCreate}from'./views/create.js';
import{renderReview}from'./views/review.js';
import{renderVideos}from'./views/videos.js';
import{renderEditar}from'./views/editar.js';
import{renderMetaAds,openMetaLoginModal}from'./views/meta_ads.js';
import{renderPesquisar}from'./views/pesquisar.js';
import{renderPaginas}from'./views/paginas.js';
import{renderCalendario}from'./views/calendario.js';

let renderSeq=0;
let pollTimer;

async function pollChanges(){
  const current=route();
  if(current.name!=='videos'||!store.current?.id)return;
  try{
    const result=await api(`/api/events?batch_id=${encodeURIComponent(store.current.id)}&after_event_id=${store.lastEventId||0}&limit=20`);
    if(result.events.length)await render();
  }catch{}
}

async function render(){
  const seq=++renderSeq;
  const root=document.querySelector('#app');
  try{
    await refreshBase();
    if(seq!==renderSeq)return;
    const current=route();setNav(current.name);root.replaceChildren();
    if(current.name==='pesquisar')await renderPesquisar(root);
    else if(current.name==='paginas')await renderPaginas(root);
    else if(current.name==='criar')await renderCreate(root);
    else if(current.name==='conferir')await renderReview(root,current.id);
    else if(current.name==='editar')await renderEditar(root);
    else if(current.name==='meta-ads')await renderMetaAds(root);
    else if(current.name==='calendario')await renderCalendario(root);
    else await renderVideos(root,current.id);
    if(seq!==renderSeq)return;
    root.classList.remove('page-transition');void root.offsetWidth;root.classList.add('page-transition');
    root.focus({preventScroll:true});
    clearInterval(pollTimer);pollTimer=current.name==='videos'?setInterval(pollChanges,1800):null;
  }catch(error){
    if(seq!==renderSeq)return;
    root.innerHTML='<section class="panel centered-panel"><h1>Não foi possível abrir.</h1><pre class="error"></pre><button id="retry">Tentar novamente</button></section>';root.querySelector('pre').textContent=error.message;root.querySelector('#retry').addEventListener('click',render);toast(error.message);
  }
}

addEventListener('hashchange',render);
addEventListener('flowlocal-refresh',render);
render();

document.querySelector('#shutdown').addEventListener('click',async()=>{if(!confirm('Encerrar o Flow Local?'))return;try{await api('/api/shutdown',{method:'POST',body:{}});document.querySelector('#app').innerHTML='<section class="panel centered-panel"><h1>Flow Local encerrado.</h1></section>';clearInterval(pollTimer)}catch(error){toast(error.message)}});
document.querySelector('#credits').addEventListener('click',async event=>{event.currentTarget.disabled=true;try{await api('/api/credits',{method:'POST',body:{}});await refreshBase();toast('Créditos atualizados.')}catch(error){toast(error.message)}finally{event.currentTarget.disabled=false}});
document.querySelector('#flow-account').addEventListener('click',async event=>{const leaving=store.state?.session.connected;if(leaving&&!confirm('Sair da conta Google usada pelo Flow?'))return;event.currentTarget.disabled=true;try{await api(leaving?'/api/logout':'/api/login',{method:'POST',body:{}});await refreshBase();toast(leaving?'Flow desconectado.':'Flow conectado.')}catch(error){toast(error.message)}finally{event.currentTarget.disabled=false}});
document.querySelector('#gpt-account').addEventListener('click',async event=>{const leaving=store.state?.gpt_session?.connected;if(leaving&&!confirm('Sair do ChatGPT Web e apagar apenas o perfil de login dedicado?'))return;event.currentTarget.disabled=true;try{if(leaving){await api('/api/gpt-logout',{method:'POST',body:{}});await refreshBase();toast('GPT desconectado.');return}const result=await api('/api/gpt-login',{method:'POST',body:{}});await refreshBase();if(!result.started){toast(result.message);return}toast('Entre no ChatGPT. A conexão será detectada automaticamente.');for(let attempt=0;attempt<450;attempt++){await new Promise(resolve=>setTimeout(resolve,2000));const check=await api('/api/gpt-verify',{method:'POST',body:{}});await refreshBase();if(check.session?.connected){toast('ChatGPT Web conectado e verificado.');break}}}catch(error){toast(error.message)}finally{event.currentTarget.disabled=false}});
document.querySelector('#meta-account')?.addEventListener('click',async()=>{if(store.state?.meta_ads?.connected){go('meta-ads');return}openMetaLoginModal(document.querySelector('#app'),async()=>{await refreshBase();if(route().name==='meta-ads')await render();else go('meta-ads')})});
