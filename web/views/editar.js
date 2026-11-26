import{api,fileBase64,go,html,store,toast}from'../state.js';

let activeMediaTab='videos';
let currentFolder='';
let currentVideos=[];
let currentAudios=[];
let currentLib=[];
let currentProjects=[];
let playhead=0;
let playing=false;
let rafId=null;
let lastTick=0;
let activeAudioEls=[];
let lastRender=null;
let selected={type:'v1',index:0};

const project={
  id:'',
  name:'Edição CapCut',
  aspect:'9:16',
  clips:[],
  overlays:[],
  audio_tracks:[],
  captions:[],
  caption_style:{font_size:52,position:'Inferior',color:'#ffffff',preset:'clean'},
};

const EFFECTS=[
  {id:'none',label:'Normal',css:''},
  {id:'zoom_in',label:'Zoom Suave',css:'transform:scale(1.06)'},
  {id:'contrast',label:'Alto Contraste',css:'filter:contrast(1.22) saturate(1.18)'},
  {id:'warm',label:'Quente',css:'filter:sepia(.18) saturate(1.15)'},
  {id:'bw',label:'P&B',css:'filter:grayscale(1)'},
  {id:'vignette',label:'Vinheta',css:'box-shadow:inset 0 0 60px #000'},
];

function fmtTime(sec){
  const s=Math.max(0,Number(sec||0));
  const m=Math.floor(s/60);
  const rem=(s%60).toFixed(1).padStart(4,'0');
  return `${String(m).padStart(2,'0')}:${rem}`;
}

function clipOutDur(c){
  const dur=Math.max(0.1,(Number(c.trim_end||c.duration||4)-Number(c.trim_start||0)));
  return dur/Math.max(0.5,Number(c.speed||1));
}

function timelineTotal(){
  const vEnd=project.clips.reduce((sum,c)=>sum+clipOutDur(c),0);
  const oEnd=project.overlays.reduce((mx,o)=>Math.max(mx,Number(o.timeline_start||0)+Number(o.duration||0)),0);
  const aEnd=project.audio_tracks.reduce((mx,a)=>Math.max(mx,Number(a.timeline_start||0)+Number(a.duration||0)),0);
  return Math.max(4,vEnd,oEnd,aEnd);
}

function clipOffsets(){
  let acc=0;
  return project.clips.map((c,index)=>{
    const d=clipOutDur(c);
    const entry={clip:c,index,start:acc,end:acc+d,dur:d};
    acc+=d;
    return entry;
  });
}

function stopAudioEls(){
  for(const el of activeAudioEls){try{el.pause()}catch{}}
  activeAudioEls=[];
}

export async function renderEditar(root){
  stopAudioEls();
  playing=false;
  cancelAnimationFrame(rafId);

  const caps=store.state?.editor||await api('/api/editor/capabilities');
  const pendingTarget=store.editorTarget||(store.editorTargetPath?{path:store.editorTargetPath}:null);
  store.editorTarget=null;
  store.editorTargetPath=null;
  if(pendingTarget){
    if(pendingTarget.folder)currentFolder=pendingTarget.folder;
    if(pendingTarget.path){
      try{
        const a=await api('/api/editor/analyze',{method:'POST',body:{path:pendingTarget.path,run_whisper:false,run_scenes:true,extract_frames:true}});
        addVideoToTimeline(a);
      }catch(err){toast(err.message)}
    }
  }

  root.innerHTML=`
    <section class="page-head">
      <div>
        <p class="kicker">04 · estúdio capcut</p>
        <h1>Editor Multi-Faixas.</h1>
        <p class="lead">Monte camadas de vídeo (V1/V2), trilhas de áudio, efeitos e legendas automáticas Whisper com exportação real em FFmpeg.</p>
      </div>
      <div class="row">
        <span class="badge ${caps.ffmpeg?'good':'bad'}">FFmpeg ${caps.ffmpeg?'OK':'Falta'}</span>
        <span class="badge ${caps.faster_whisper?'good':'warn'}">Whisper ${caps.faster_whisper?'OK':'Falta'}</span>
        <span class="badge ${caps.scenedetect?'good':'warn'}">Cenas ${caps.scenedetect?'OK':'Falta'}</span>
        <span class="badge ${caps.auto_editor?'good':'warn'}">Auto-Editor ${caps.auto_editor?'OK':'Falta'}</span>
      </div>
    </section>

    <div class="panel cc-topbar">
      <input id="cc-proj-name" value="${html(project.name)}" placeholder="Nome do projeto" aria-label="Nome do projeto">
      <select id="cc-aspect" aria-label="Proporção">
        <option value="9:16" ${project.aspect==='9:16'?'selected':''}>9:16 Reels / TikTok</option>
        <option value="16:9" ${project.aspect==='16:9'?'selected':''}>16:9 YouTube</option>
        <option value="1:1" ${project.aspect==='1:1'?'selected':''}>1:1 Feed</option>
      </select>
      <button id="cc-auto-caption" type="button">Auto-Legenda (Whisper)</button>
      <button id="cc-save-proj" type="button" class="quiet">Salvar projeto</button>
      <button id="cc-export-btn" type="button" class="primary">Exportar MP4 →</button>
    </div>

    <div class="cc-studio">
      <aside class="panel cc-library">
        <div class="cc-tabs">
          <button type="button" data-tab="videos" class="${activeMediaTab==='videos'?'primary':''}">Vídeos</button>
          <button type="button" data-tab="audios" class="${activeMediaTab==='audios'?'primary':''}">Áudios</button>
          <button type="button" data-tab="overlays" class="${activeMediaTab==='overlays'?'primary':''}">Camada V2</button>
          <button type="button" data-tab="effects" class="${activeMediaTab==='effects'?'primary':''}">Efeitos</button>
        </div>
        <div id="cc-lib-body" class="cc-lib-body"></div>
      </aside>

      <section class="panel cc-monitor-panel">
        <div id="cc-stage" class="cc-stage aspect-${project.aspect.replace(':','x')}">
          <video id="ed-player" class="cc-video" playsinline></video>
          <div id="cc-overlay-preview" class="cc-overlay-preview" hidden></div>
          <div id="cc-caption-preview" class="cc-caption-preview"></div>
        </div>
        <div class="cc-transport">
          <button id="cc-play-btn" type="button" class="primary">▶ Play</button>
          <span id="cc-timecode" class="mono">00:00.0 / 00:04.0</span>
          <button id="cc-split-btn" type="button" title="Dividir clipe no playhead">✂️ Dividir aqui</button>
          <button id="cc-open-out" type="button" class="quiet">Abrir pasta</button>
        </div>
        <div id="cc-frame-strip" class="cc-frame-strip"></div>
        <div id="cc-render-box"></div>
      </section>

      <aside class="panel cc-inspector">
        <div class="panel-head"><h2>Inspetor</h2><span id="cc-sel-badge" class="badge">V1</span></div>
        <div id="cc-inspector-body"></div>
      </aside>
    </div>

    <section class="panel cc-timeline-panel">
      <div class="panel-head">
        <h2>Timeline</h2>
        <small class="hint">Clique na régua para posicionar a agulha ou clique em qualquer bloco para editar corte, volume e efeitos.</small>
      </div>
      <div id="cc-ruler" class="cc-ruler"></div>
      <div id="cc-tracks" class="cc-tracks"></div>
    </section>`;

  const libBody=root.querySelector('#cc-lib-body');
  const stage=root.querySelector('#cc-stage');
  const videoEl=root.querySelector('#ed-player');
  const ovPreview=root.querySelector('#cc-overlay-preview');
  const capPreview=root.querySelector('#cc-caption-preview');
  const timecodeEl=root.querySelector('#cc-timecode');
  const inspectorBody=root.querySelector('#cc-inspector-body');
  const selBadge=root.querySelector('#cc-sel-badge');
  const rulerEl=root.querySelector('#cc-ruler');
  const tracksEl=root.querySelector('#cc-tracks');
  const frameStripEl=root.querySelector('#cc-frame-strip');
  const playBtn=root.querySelector('#cc-play-btn');

  function addVideoToTimeline(a){
    const dur=Number(a.duration||4);
    project.clips.push({
      id:`v1_${Date.now()}_${Math.random().toString(36).slice(2,6)}`,
      name:a.name,
      path:a.path,
      source_url:a.source_url,
      duration:dur,
      has_audio:Boolean(a.has_audio),
      trim_start:0,
      trim_end:dur,
      speed:1,
      volume:1,
      mute:false,
      effect:'none',
      transition:'none',
      frames:a.frames||[],
      scenes:a.scenes||[],
    });
    if(Array.isArray(a.segments)&&a.segments.length&&!project.captions.length){
      project.captions=a.segments.map(s=>({start:Number(s.start||0),end:Number(s.end||0),text:s.text||''}));
    }
    selected={type:'v1',index:project.clips.length-1};
  }

  async function loadAllMedia(){
    const q=currentFolder?`?folder=${encodeURIComponent(currentFolder)}`:'';
    const [vRes,aRes,lRes,pRes]=await Promise.all([
      api(`/api/editor/videos${q}`).catch(()=>({folder:'',items:[]})),
      api('/api/editor/audios').catch(()=>({items:[]})),
      api('/api/library').catch(()=>({items:[]})),
      api('/api/editor/projects').catch(()=>({items:[]})),
    ]);
    currentFolder=vRes.folder||currentFolder;
    currentVideos=vRes.items||[];
    currentAudios=aRes.items||[];
    currentLib=lRes.items||[];
    currentProjects=pRes.items||[];
    if(!project.clips.length&&currentVideos.length){
      try{
        const first=currentVideos[0];
        const a=await api('/api/editor/analyze',{method:'POST',body:{path:first.path,run_whisper:false,run_scenes:true,extract_frames:true}});
        addVideoToTimeline(a);
      }catch{}
    }
    renderLib();
    renderTimeline();
    renderInspector();
    syncMonitorAt(playhead,true);
  }

  function renderLib(){
    libBody.replaceChildren();
    if(activeMediaTab==='videos'){
      libBody.innerHTML=`
        <div class="row">
          <input id="cc-folder-in" value="${html(currentFolder)}" placeholder="Pasta de vídeos">
          <button id="cc-scan" type="button" class="quiet">↻</button>
          <button id="cc-pick" type="button" class="quiet">Pasta…</button>
        </div>
        <div id="cc-v-list" class="cc-media-list"></div>`;
      const list=libBody.querySelector('#cc-v-list');
      for(const v of currentVideos){
        const row=document.createElement('div');
        row.className='cc-media-item';
        row.innerHTML=`
          <div class="cc-media-meta">
            <strong>${html(v.relative_label||v.name)}</strong>
            <small class="hint mono">${(v.duration||0).toFixed(1)}s · ${v.has_audio?'Áudio':'Mudo'}</small>
          </div>
          <div class="row">
            <button type="button" class="cc-add-v1 primary">+ V1</button>
            <button type="button" class="cc-add-v2 quiet">+ V2</button>
          </div>`;
        row.querySelector('.cc-add-v1').addEventListener('click',async()=>{
          try{
            const a=await api('/api/editor/analyze',{method:'POST',body:{path:v.path,run_whisper:false,run_scenes:true,extract_frames:true}});
            addVideoToTimeline(a);
            renderTimeline();renderInspector();syncMonitorAt(playhead,true);
            toast(`${v.name} adicionado na faixa V1.`);
          }catch(err){toast(err.message)}
        });
        row.querySelector('.cc-add-v2').addEventListener('click',()=>{
          project.overlays.push({id:`ov_${Date.now()}`,name:v.name,path:v.path,preview_url:'',timeline_start:Number(playhead.toFixed(1)),duration:Math.min(4,Number(v.duration||3)),scale:0.35,pos:'top_right',kind:'video'});
          selected={type:'v2',index:project.overlays.length-1};
          renderTimeline();renderInspector();syncMonitorAt(playhead,true);
        });
        list.append(row);
      }
      libBody.querySelector('#cc-scan').addEventListener('click',()=>{currentFolder=libBody.querySelector('#cc-folder-in').value.trim();loadAllMedia()});
      libBody.querySelector('#cc-pick').addEventListener('click',async()=>{
        const r=await api('/api/choose-folder',{method:'POST',body:{initial:currentFolder}}).catch(()=>({}));
        if(r?.path){currentFolder=r.path;loadAllMedia()}
      });
    }else if(activeMediaTab==='audios'){
      libBody.innerHTML=`
        <label class="button primary" for="cc-aud-up">+ Importar MP3 / WAV / M4A<input id="cc-aud-up" class="sr-only" type="file" accept=".mp3,.wav,.m4a,.aac,.ogg,audio/*"></label>
        <div id="cc-a-list" class="cc-media-list"></div>`;
      libBody.querySelector('#cc-aud-up').addEventListener('change',async ev=>{
        const f=ev.currentTarget.files?.[0];
        if(!f)return;
        try{
          const up=await api('/api/editor/upload-audio',{method:'POST',body:{name:f.name,content:await fileBase64(f)}});
          currentAudios.unshift(up);
          project.audio_tracks.push({id:`a1_${Date.now()}`,name:up.name,path:up.path,url:up.url,timeline_start:Number(playhead.toFixed(1)),trim_start:0,duration:up.duration||10,volume:0.6});
          selected={type:'a1',index:project.audio_tracks.length-1};
          renderLib();renderTimeline();renderInspector();
          toast(`Áudio ${up.name} adicionado na Trilha A1.`);
        }catch(err){toast(err.message)}
      });
      const list=libBody.querySelector('#cc-a-list');
      if(!currentAudios.length){
        list.innerHTML='<div class="empty">Envie música, batida ou locução acima para usar na trilha A1.</div>';
      }else{
        for(const a of currentAudios){
          const row=document.createElement('div');
          row.className='cc-media-item';
          row.innerHTML=`<div class="cc-media-meta"><strong>${html(a.name)}</strong><small class="hint mono">${(a.duration||0).toFixed(1)}s</small></div><button type="button" class="primary">+ A1</button>`;
          row.querySelector('button').addEventListener('click',()=>{
            project.audio_tracks.push({id:`a1_${Date.now()}`,name:a.name,path:a.path,url:a.url,timeline_start:Number(playhead.toFixed(1)),trim_start:0,duration:a.duration||10,volume:0.6});
            selected={type:'a1',index:project.audio_tracks.length-1};
            renderTimeline();renderInspector();
          });
          list.append(row);
        }
      }
    }else if(activeMediaTab==='overlays'){
      libBody.innerHTML='<p class="hint">Imagens da Biblioteca para usar como camada PiP / logo / B-roll na faixa V2:</p><div id="cc-ov-list" class="cc-media-list"></div>';
      const list=libBody.querySelector('#cc-ov-list');
      if(!currentLib.length){list.innerHTML='<div class="empty">Biblioteca vazia. Use Vídeos → + V2 ou crie imagens em Criar.</div>';return}
      for(const img of currentLib){
        const row=document.createElement('div');
        row.className='cc-media-item';
        row.innerHTML=`<img class="cc-ov-thumb" src="${html(img.preview_url)}" alt=""><div class="cc-media-meta"><strong>${html(img.name)}</strong></div><button type="button" class="primary">+ V2</button>`;
        row.querySelector('button').addEventListener('click',()=>{
          project.overlays.push({id:`ov_${Date.now()}`,name:img.name,path:img.path,preview_url:img.preview_url,timeline_start:Number(playhead.toFixed(1)),duration:3.5,scale:0.35,pos:'top_right',kind:'image'});
          selected={type:'v2',index:project.overlays.length-1};
          renderTimeline();renderInspector();syncMonitorAt(playhead,true);
        });
        list.append(row);
      }
    }else{
      const activeClip=selected.type==='v1'?project.clips[selected.index]:project.clips[0];
      libBody.innerHTML=`
        <label>Efeito visual no clipe V1 selecionado</label>
        <div class="cc-fx-grid">${EFFECTS.map(fx=>`<button type="button" data-fx="${fx.id}" class="${activeClip?.effect===fx.id?'primary':''}">${fx.label}</button>`).join('')}</div>
        <label>Preset da legenda automática</label>
        <div class="cc-fx-grid">
          <button type="button" data-cap="clean" class="${project.caption_style.preset==='clean'?'primary':''}">Limpa Branca</button>
          <button type="button" data-cap="tiktok_bold" class="${project.caption_style.preset==='tiktok_bold'?'primary':''}">TikTok Amarela</button>
        </div>`;
      libBody.querySelectorAll('[data-fx]').forEach(b=>b.addEventListener('click',()=>{
        if(!activeClip){toast('Adicione um vídeo em V1 primeiro.');return}
        activeClip.effect=b.dataset.fx;
        renderLib();renderTimeline();renderInspector();syncMonitorAt(playhead,true);
      }));
      libBody.querySelectorAll('[data-cap]').forEach(b=>b.addEventListener('click',()=>{
        project.caption_style.preset=b.dataset.cap;
        project.caption_style.color=b.dataset.cap==='tiktok_bold'?'#ffdf81':'#ffffff';
        renderLib();renderInspector();syncMonitorAt(playhead,true);
      }));
    }
  }

  function syncMonitorAt(t,forceSrc=false){
    const total=timelineTotal();
    playhead=Math.max(0,Math.min(total,t));
    timecodeEl.textContent=`${fmtTime(playhead)} / ${fmtTime(total)}`;
    const needle=rulerEl.querySelector('.cc-needle');
    if(needle)needle.style.left=`${(playhead/total)*100}%`;

    const offsets=clipOffsets();
    const hit=offsets.find(o=>playhead>=o.start&&playhead<o.end)||offsets.at(-1);
    if(hit){
      const targetSrc=hit.clip.source_url||'';
      const switched=videoEl.getAttribute('src')!==targetSrc;
      if(forceSrc||switched){
        if(targetSrc)videoEl.setAttribute('src',targetSrc);
      }
      const localTime=Number(hit.clip.trim_start||0)+(playhead-hit.start)*Number(hit.clip.speed||1);
      if(!playing||switched||Math.abs(videoEl.currentTime-localTime)>0.35){
        try{videoEl.currentTime=localTime}catch{}
      }
      if(playing&&switched)videoEl.play().catch(()=>{});
      videoEl.playbackRate=Number(hit.clip.speed||1);
      videoEl.muted=Boolean(hit.clip.mute);
      videoEl.volume=Math.min(1,Number(hit.clip.volume??1));
      const fx=EFFECTS.find(e=>e.id===hit.clip.effect);
      videoEl.style.cssText=fx?fx.css:'';
    }

    const activeCap=project.captions.find(c=>playhead>=Number(c.start)&&playhead<=Number(c.end));
    capPreview.textContent=activeCap?activeCap.text:'';
    capPreview.style.color=project.caption_style.color||'#ffffff';

    const activeOv=project.overlays.find(o=>playhead>=Number(o.timeline_start)&&playhead<=Number(o.timeline_start)+Number(o.duration));
    if(activeOv&&activeOv.preview_url){
      ovPreview.hidden=false;
      ovPreview.innerHTML=`<img src="${html(activeOv.preview_url)}" alt="">`;
    }else if(activeOv){
      ovPreview.hidden=false;
      ovPreview.innerHTML=`<span class="badge good">${html(activeOv.name)}</span>`;
    }else{
      ovPreview.hidden=true;
    }
  }

  function togglePlay(){
    if(playing){
      playing=false;
      playBtn.textContent='▶ Play';
      videoEl.pause();
      stopAudioEls();
      cancelAnimationFrame(rafId);
      return;
    }
    if(!project.clips.length){toast('Adicione um clipe em V1 primeiro.');return}
    playing=true;
    playBtn.textContent='⏸ Pause';
    lastTick=performance.now();
    videoEl.play().catch(()=>{});
    stopAudioEls();
    for(const trk of project.audio_tracks){
      if(!trk.url)continue;
      const au=new Audio(trk.url);
      au.volume=Math.min(1,Number(trk.volume??0.6));
      if(playhead>=trk.timeline_start&&playhead<trk.timeline_start+trk.duration){
        au.currentTime=Math.max(0,(playhead-trk.timeline_start)+(trk.trim_start||0));
        au.play().catch(()=>{});
      }
      activeAudioEls.push(au);
    }
    function step(now){
      if(!playing)return;
      const dt=(now-lastTick)/1000;
      lastTick=now;
      const total=timelineTotal();
      playhead+=dt;
      if(playhead>=total){
        playhead=0;
        togglePlay();
        syncMonitorAt(0,true);
        return;
      }
      syncMonitorAt(playhead,false);
      rafId=requestAnimationFrame(step);
    }
    rafId=requestAnimationFrame(step);
  }

  function renderTimeline(){
    const total=timelineTotal();
    const ticks=Math.max(4,Math.min(16,Math.ceil(total)));
    rulerEl.innerHTML=`<div class="cc-needle" style="left:${(playhead/total)*100}%"></div>`+
      Array.from({length:ticks+1},(_,i)=>{
        const sec=(i*total)/ticks;
        return `<span style="left:${(sec/total)*100}%">${sec.toFixed(1)}s</span>`;
      }).join('');

    const offsets=clipOffsets();
    const v1Html=offsets.map(o=>{
      const left=(o.start/total)*100;
      const width=Math.max(4,(o.dur/total)*100);
      const isSel=selected.type==='v1'&&selected.index===o.index;
      return `<button type="button" class="cc-block v1 ${isSel?'is-sel':''}" data-sel="v1:${o.index}" style="left:${left}%;width:${width}%"><span>${html(o.clip.name)}</span><small>${o.dur.toFixed(1)}s ${o.clip.effect!=='none'?`· ${o.clip.effect}`:''}</small></button>`;
    }).join('');

    const v2Html=project.overlays.map((o,i)=>{
      const left=(Number(o.timeline_start||0)/total)*100;
      const width=Math.max(4,(Number(o.duration||2)/total)*100);
      const isSel=selected.type==='v2'&&selected.index===i;
      return `<button type="button" class="cc-block v2 ${isSel?'is-sel':''}" data-sel="v2:${i}" style="left:${left}%;width:${width}%"><span>${html(o.name)}</span><small>${o.pos}</small></button>`;
    }).join('');

    const capHtml=project.captions.map((c,i)=>{
      const left=(Number(c.start||0)/total)*100;
      const width=Math.max(3,((Number(c.end||1)-Number(c.start||0))/total)*100);
      const isSel=selected.type==='t1'&&selected.index===i;
      return `<button type="button" class="cc-block t1 ${isSel?'is-sel':''}" data-sel="t1:${i}" style="left:${left}%;width:${width}%"><span>${html(c.text)}</span></button>`;
    }).join('');

    const a1Html=project.audio_tracks.map((a,i)=>{
      const left=(Number(a.timeline_start||0)/total)*100;
      const width=Math.max(5,(Math.min(total-Number(a.timeline_start||0),Number(a.duration||5))/total)*100);
      const isSel=selected.type==='a1'&&selected.index===i;
      return `<button type="button" class="cc-block a1 ${isSel?'is-sel':''}" data-sel="a1:${i}" style="left:${left}%;width:${width}%"><span>🎵 ${html(a.name)}</span><small>${Math.round((a.volume??0.6)*100)}%</small></button>`;
    }).join('');

    tracksEl.innerHTML=`
      <div class="cc-track-row"><span class="cc-track-lbl">V1 Vídeo</span><div class="cc-track-lane">${v1Html||'<small class="hint">Adicione vídeos na coluna esquerda (+ V1)</small>'}</div></div>
      <div class="cc-track-row"><span class="cc-track-lbl">V2 Camada</span><div class="cc-track-lane">${v2Html||'<small class="hint">Opcional: B-roll / Logo (+ V2)</small>'}</div></div>
      <div class="cc-track-row"><span class="cc-track-lbl">T1 Legendas</span><div class="cc-track-lane">${capHtml||'<small class="hint">Clique em Auto-Legenda (Whisper) ou adicione no Inspetor</small>'}</div></div>
      <div class="cc-track-row"><span class="cc-track-lbl">A1 Áudio</span><div class="cc-track-lane">${a1Html||'<small class="hint">Opcional: música / locução (aba Áudios → + A1)</small>'}</div></div>`;

    tracksEl.querySelectorAll('[data-sel]').forEach(b=>b.addEventListener('click',()=>{
      const [type,idx]=b.dataset.sel.split(':');
      selected={type,index:Number(idx)};
      renderTimeline();renderInspector();
    }));

    const activeClip=selected.type==='v1'?project.clips[selected.index]:project.clips[0];
    const activeOff=offsets.find(o=>o.clip===activeClip)||offsets[0];
    if(frameStripEl&&activeClip?.frames?.length){
      frameStripEl.innerHTML=activeClip.frames.map(f=>`<button type="button" class="cc-frame-thumb" data-t="${activeOff.start+Math.max(0,(f.time-activeClip.trim_start)/activeClip.speed)}" title="Pular para ${f.time}s"><img src="${html(f.url)}" alt=""></button>`).join('');
      frameStripEl.querySelectorAll('[data-t]').forEach(b=>b.addEventListener('click',()=>syncMonitorAt(Number(b.dataset.t||0),false)));
    }else if(frameStripEl){
      frameStripEl.replaceChildren();
    }
  }

  function renderInspector(){
    selBadge.textContent=selected.type.toUpperCase();
    inspectorBody.replaceChildren();
    if(selected.type==='v1'&&project.clips[selected.index]){
      const c=project.clips[selected.index];
      inspectorBody.innerHTML=`
        <strong class="mono">${html(c.name)}</strong>
        <div class="settings">
          <label>Início corte (s)<input id="in-s" type="number" step="0.1" min="0" value="${c.trim_start}"></label>
          <label>Fim corte (s)<input id="in-e" type="number" step="0.1" min="0.1" max="${c.duration}" value="${c.trim_end}"></label>
          <label>Velocidade<input id="in-spd" type="number" step="0.1" min="0.5" max="2" value="${c.speed}"></label>
          <label>Volume<input id="in-vol" type="number" step="0.1" min="0" max="2" value="${c.volume}"></label>
          <label>Efeito<select id="in-fx">${EFFECTS.map(f=>`<option value="${f.id}" ${c.effect===f.id?'selected':''}>${f.label}</option>`).join('')}</select></label>
          <label>Transição<select id="in-tr"><option value="none" ${c.transition==='none'?'selected':''}>Corte seco</option><option value="fade" ${c.transition==='fade'?'selected':''}>Fade</option></select></label>
        </div>
        <label class="check"><input id="in-mute" type="checkbox" ${c.mute?'checked':''}>Mutar áudio original deste clipe</label>
        <div class="button-row">
          <button id="in-left" type="button" class="quiet">◀</button>
          <button id="in-right" type="button" class="quiet">▶</button>
          <button id="in-dup" type="button" class="quiet">⧉ Duplicar</button>
          <button id="in-del" type="button" class="danger quiet">Remover</button>
        </div>`;
      inspectorBody.querySelector('#in-s').addEventListener('change',e=>{c.trim_start=Math.max(0,Number(e.target.value||0));renderTimeline();syncMonitorAt(playhead)});
      inspectorBody.querySelector('#in-e').addEventListener('change',e=>{c.trim_end=Math.min(c.duration,Math.max(c.trim_start+0.2,Number(e.target.value||c.duration)));renderTimeline()});
      inspectorBody.querySelector('#in-spd').addEventListener('change',e=>{c.speed=Math.max(0.5,Math.min(2,Number(e.target.value||1)));renderTimeline();syncMonitorAt(playhead)});
      inspectorBody.querySelector('#in-vol').addEventListener('change',e=>{c.volume=Number(e.target.value??1);syncMonitorAt(playhead)});
      inspectorBody.querySelector('#in-fx').addEventListener('change',e=>{c.effect=e.target.value;renderTimeline();syncMonitorAt(playhead)});
      inspectorBody.querySelector('#in-tr').addEventListener('change',e=>{c.transition=e.target.value});
      inspectorBody.querySelector('#in-mute').addEventListener('change',e=>{c.mute=e.target.checked;syncMonitorAt(playhead)});
      inspectorBody.querySelector('#in-left').addEventListener('click',()=>{if(selected.index>0){[project.clips[selected.index-1],project.clips[selected.index]]=[project.clips[selected.index],project.clips[selected.index-1]];selected.index--;renderTimeline();renderInspector()}});
      inspectorBody.querySelector('#in-right').addEventListener('click',()=>{if(selected.index<project.clips.length-1){[project.clips[selected.index+1],project.clips[selected.index]]=[project.clips[selected.index],project.clips[selected.index+1]];selected.index++;renderTimeline();renderInspector()}});
      inspectorBody.querySelector('#in-dup').addEventListener('click',()=>{const copy={...c,id:`v1_${Date.now()}`};project.clips.splice(selected.index+1,0,copy);selected.index++;renderTimeline();renderInspector();toast('Clipe duplicado na timeline.')});
      inspectorBody.querySelector('#in-del').addEventListener('click',()=>{project.clips.splice(selected.index,1);selected={type:'v1',index:0};renderTimeline();renderInspector();syncMonitorAt(0,true)});
    }else if(selected.type==='v2'&&project.overlays[selected.index]){
      const o=project.overlays[selected.index];
      inspectorBody.innerHTML=`
        <strong>Camada V2: ${html(o.name)}</strong>
        <div class="settings">
          <label>Entra em (s)<input id="ov-s" type="number" step="0.1" min="0" value="${o.timeline_start}"></label>
          <label>Duração (s)<input id="ov-d" type="number" step="0.2" min="0.3" value="${o.duration}"></label>
          <label>Tamanho (%)<input id="ov-sc" type="number" step="5" min="15" max="90" value="${Math.round((o.scale||0.35)*100)}"></label>
          <label>Posição<select id="ov-pos"><option value="top_right" ${o.pos==='top_right'?'selected':''}>Sup. Direito</option><option value="top_left" ${o.pos==='top_left'?'selected':''}>Sup. Esquerdo</option><option value="bottom_right" ${o.pos==='bottom_right'?'selected':''}>Inf. Direito</option><option value="center" ${o.pos==='center'?'selected':''}>Centro</option></select></label>
        </div>
        <button id="ov-del" type="button" class="danger quiet">Remover camada</button>`;
      inspectorBody.querySelector('#ov-s').addEventListener('change',e=>{o.timeline_start=Math.max(0,Number(e.target.value||0));renderTimeline()});
      inspectorBody.querySelector('#ov-d').addEventListener('change',e=>{o.duration=Math.max(0.3,Number(e.target.value||1));renderTimeline()});
      inspectorBody.querySelector('#ov-sc').addEventListener('change',e=>{o.scale=Number(e.target.value||35)/100});
      inspectorBody.querySelector('#ov-pos').addEventListener('change',e=>{o.pos=e.target.value;renderTimeline()});
      inspectorBody.querySelector('#ov-del').addEventListener('click',()=>{project.overlays.splice(selected.index,1);selected={type:'v1',index:0};renderTimeline();renderInspector()});
    }else if(selected.type==='a1'&&project.audio_tracks[selected.index]){
      const a=project.audio_tracks[selected.index];
      inspectorBody.innerHTML=`
        <strong>Áudio A1: ${html(a.name)}</strong>
        <div class="settings">
          <label>Entra em (s)<input id="a-s" type="number" step="0.1" min="0" value="${a.timeline_start}"></label>
          <label>Duração (s)<input id="a-d" type="number" step="0.5" min="0.5" value="${a.duration}"></label>
          <label>Volume (0–2)<input id="a-v" type="number" step="0.1" min="0" max="2" value="${a.volume}"></label>
        </div>
        <button id="a-del" type="button" class="danger quiet">Remover áudio</button>`;
      inspectorBody.querySelector('#a-s').addEventListener('change',e=>{a.timeline_start=Math.max(0,Number(e.target.value||0));renderTimeline()});
      inspectorBody.querySelector('#a-d').addEventListener('change',e=>{a.duration=Math.max(0.5,Number(e.target.value||1));renderTimeline()});
      inspectorBody.querySelector('#a-v').addEventListener('change',e=>{a.volume=Number(e.target.value??0.6);renderTimeline()});
      inspectorBody.querySelector('#a-del').addEventListener('click',()=>{project.audio_tracks.splice(selected.index,1);selected={type:'v1',index:0};renderTimeline();renderInspector()});
    }else if(selected.type==='t1'&&project.captions[selected.index]){
      const c=project.captions[selected.index];
      inspectorBody.innerHTML=`
        <strong>Legenda #${selected.index+1}</strong>
        <div class="settings">
          <label>Início (s)<input id="c-s" type="number" step="0.1" value="${c.start}"></label>
          <label>Fim (s)<input id="c-e" type="number" step="0.1" value="${c.end}"></label>
        </div>
        <label>Texto<input id="c-t" value="${html(c.text)}"></label>
        <button id="c-del" type="button" class="danger quiet">Remover legenda</button>`;
      inspectorBody.querySelector('#c-s').addEventListener('change',e=>{c.start=Number(e.target.value||0);renderTimeline()});
      inspectorBody.querySelector('#c-e').addEventListener('change',e=>{c.end=Number(e.target.value||0);renderTimeline()});
      inspectorBody.querySelector('#c-t').addEventListener('input',e=>{c.text=e.target.value;renderTimeline();syncMonitorAt(playhead)});
      inspectorBody.querySelector('#c-del').addEventListener('click',()=>{project.captions.splice(selected.index,1);selected={type:'v1',index:0};renderTimeline();renderInspector()});
    }else{
      inspectorBody.innerHTML='<div class="empty">Adicione um vídeo em V1 ou clique em qualquer bloco na timeline abaixo.</div>';
    }

    const capFoot=document.createElement('div');
    capFoot.className='cc-cap-foot';
    capFoot.innerHTML=`
      <hr class="cc-sep">
      <div class="row"><button id="cc-add-cap" type="button" class="quiet">+ Legenda manual</button><span class="counter">${project.captions.length} legenda(s)</span></div>`;
    capFoot.querySelector('#cc-add-cap').addEventListener('click',()=>{
      const st=Number(playhead.toFixed(1));
      project.captions.push({start:st,end:Number((st+2).toFixed(1)),text:'Legenda no vídeo'});
      selected={type:'t1',index:project.captions.length-1};
      renderTimeline();renderInspector();syncMonitorAt(playhead);
    });
    inspectorBody.append(capFoot);
  }

  root.querySelectorAll('.cc-tabs button').forEach(btn=>btn.addEventListener('click',()=>{
    activeMediaTab=btn.dataset.tab;
    root.querySelectorAll('.cc-tabs button').forEach(b=>b.className=b.dataset.tab===activeMediaTab?'primary':'');
    renderLib();
  }));

  root.querySelector('#cc-aspect').addEventListener('change',e=>{
    project.aspect=e.target.value;
    stage.className=`cc-stage aspect-${project.aspect.replace(':','x')}`;
  });
  root.querySelector('#cc-proj-name').addEventListener('input',e=>{project.name=e.target.value});

  rulerEl.addEventListener('click',ev=>{
    const rect=rulerEl.getBoundingClientRect();
    const pct=Math.max(0,Math.min(1,(ev.clientX-rect.left)/Math.max(1,rect.width)));
    syncMonitorAt(pct*timelineTotal(),false);
  });

  playBtn.addEventListener('click',togglePlay);

  root.querySelector('#cc-split-btn').addEventListener('click',()=>{
    const offsets=clipOffsets();
    const hit=offsets.find(o=>playhead>o.start+0.2&&playhead<o.end-0.2);
    if(!hit){toast('Posicione a agulha no meio de um clipe V1 para dividir.');return}
    const splitSourceTime=Number(hit.clip.trim_start)+(playhead-hit.start)*Number(hit.clip.speed||1);
    const clone={...hit.clip,id:`v1_${Date.now()}`,trim_start:Number(splitSourceTime.toFixed(2))};
    hit.clip.trim_end=Number(splitSourceTime.toFixed(2));
    project.clips.splice(hit.index+1,0,clone);
    selected={type:'v1',index:hit.index+1};
    renderTimeline();renderInspector();
    toast('Clipe dividido no playhead.');
  });

  root.querySelector('#cc-auto-caption').addEventListener('click',async ev=>{
    if(!project.clips.length){toast('Adicione um clipe em V1 primeiro.');return}
    const btn=ev.currentTarget;
    btn.disabled=true;
    btn.textContent='Transcrevendo…';
    try{
      const offsets=clipOffsets();
      const merged=[];
      for(const o of offsets){
        if(!o.clip.has_audio)continue;
        const res=await api('/api/editor/analyze',{method:'POST',body:{path:o.clip.path,run_whisper:true,run_scenes:false,extract_frames:false,force:true}});
        for(const s of (res.segments||[])){
          const sStart=Number(s.start||0),sEnd=Number(s.end||0);
          if(sEnd<=o.clip.trim_start||sStart>=o.clip.trim_end)continue;
          merged.push({
            start:Number((o.start+Math.max(0,sStart-o.clip.trim_start)/o.clip.speed).toFixed(2)),
            end:Number((o.start+Math.min(o.dur,(sEnd-o.clip.trim_start)/o.clip.speed)).toFixed(2)),
            text:s.text||'',
          });
        }
      }
      project.captions=merged;
      renderTimeline();renderInspector();syncMonitorAt(playhead);
      toast(merged.length?`${merged.length} legenda(s) gerada(s) pelo Whisper!`:'Nenhuma fala detectada nos clipes.');
    }catch(err){toast(err.message)}finally{
      btn.disabled=false;btn.textContent='Auto-Legenda (Whisper)';
    }
  });

  root.querySelector('#cc-save-proj').addEventListener('click',async()=>{
    try{
      const saved=await api('/api/editor/projects',{method:'POST',body:{...project,last_render:lastRender}});
      project.id=saved.id;
      toast('Projeto salvo localmente.');
    }catch(err){toast(err.message)}
  });

  root.querySelector('#cc-open-out').addEventListener('click',()=>api('/api/editor/open-folder',{method:'POST',body:{folder:currentFolder}}).catch(err=>toast(err.message)));

  root.querySelector('#cc-export-btn').addEventListener('click',async ev=>{
    if(!project.clips.length){toast('Adicione pelo menos 1 clipe na Timeline V1.');return}
    const btn=ev.currentTarget;
    btn.disabled=true;
    btn.textContent='Renderizando MP4…';
    try{
      lastRender=await api('/api/editor/render-timeline',{method:'POST',body:project});
      project.last_render=lastRender;
      await api('/api/editor/projects',{method:'POST',body:project}).catch(()=>{});
      const rBox=root.querySelector('#cc-render-box');
      rBox.innerHTML=`
        <div class="notice good">
          <strong>MP4 Exportado:</strong> ${html(lastRender.output_name)} (${fmtTime(lastRender.duration)})<br>
          <small class="mono">${html(lastRender.output_path)}</small>
          <div class="button-row">
            <a class="button" href="${html(lastRender.preview_url)}" download="${html(lastRender.output_name)}">Baixar MP4</a>
            <button id="cc-send-meta" type="button" class="primary">Subir no Meta Ads →</button>
          </div>
        </div>`;
      rBox.querySelector('#cc-send-meta').addEventListener('click',()=>{
        store.metaDraft={media_path:lastRender.output_path,ad_name:lastRender.output_name.replace(/\.mp4$/i,'')};
        const dlg=root.closest('dialog');
        if(dlg){dlg.close();dlg.remove()}
        go('meta-ads');
      });
      toast(`Exportado com sucesso: ${lastRender.output_name}`);
    }catch(err){toast(err.message)}finally{
      btn.disabled=false;btn.textContent='Exportar MP4 →';
    }
  });

  await loadAllMedia();
}

export async function openEditorModal(host){
  const dlg=document.createElement('dialog');
  dlg.className='hub-dialog';
  dlg.innerHTML=`
    <div class="hub-dialog-head">
      <strong>Editor CapCut Local</strong>
      <button type="button" class="quiet" id="hub-ed-close">Fechar ×</button>
    </div>
    <div class="hub-dialog-body"></div>`;
  host.append(dlg);
  dlg.showModal();
  const stopPlayback=()=>{
    playing=false;
    cancelAnimationFrame(rafId);
    stopAudioEls();
    dlg.querySelector('#ed-player')?.pause();
  };
  dlg.querySelector('#hub-ed-close').addEventListener('click',()=>{stopPlayback();dlg.close();dlg.remove()});
  dlg.addEventListener('close',()=>{stopPlayback();dlg.remove()});
  await renderEditar(dlg.querySelector('.hub-dialog-body'));
}
