(() => {
  const palettes = {teal:['青绿','#2dd4bf','#087f73'],blue:['海蓝','#60a5fa','#1d4ed8'],violet:['紫罗兰','#c4b5fd','#6d28d9'],rose:['玫瑰','#fda4af','#be123c'],amber:['琥珀','#fcd34d','#92400e'],slate:['岩灰','#cbd5e1','#475569']};
  let current={mode:'dark',palette:'teal',background:'',surface:''},kind='user',owner='visitor';
  const validColor=x=>/^#[\da-f]{6}$/i.test(x||'');
  function luminance(hex){const c=hex.slice(1).match(/../g).map(x=>parseInt(x,16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);return .2126*c[0]+.7152*c[1]+.0722*c[2];}
  function contrast(a,b){const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}
  function blend(color,target,weight){const a=color.slice(1).match(/../g).map(x=>parseInt(x,16)),b=target.slice(1).match(/../g).map(x=>parseInt(x,16));return '#'+a.map((x,i)=>Math.round(x*(1-weight)+b[i]*weight).toString(16).padStart(2,'0')).join('');}
  function apply(value){
    current={...current,...value};const root=document.documentElement;root.dataset.theme=current.mode;
    ['--nf-bg','--nf-bg-elevated','--nf-surface','--nf-surface-strong','--nf-text','--nf-muted'].forEach(x=>root.style.removeProperty(x));
    const palette=palettes[current.palette]||palettes.teal,primary=palette[current.mode==='light'?2:1];
    root.style.setProperty('--nf-primary',primary);root.style.setProperty('--nf-primary-strong',primary);root.style.setProperty('--nf-on-primary',current.mode==='light'?'#ffffff':'#101010');
    const bg=validColor(current.background)?current.background:null,surface=validColor(current.surface)?current.surface:null;
    const light=current.mode==='light';
    root.style.setProperty('--nf-bg',blend(primary,light?'#ffffff':'#000000',light?.97:.94));
    root.style.setProperty('--nf-bg-elevated',blend(primary,light?'#ffffff':'#000000',light?.94:.90));
    root.style.setProperty('--nf-surface',light?'#ffffff':blend(primary,'#000000',.86));
    root.style.setProperty('--nf-surface-strong',blend(primary,light?'#ffffff':'#000000',light?.90:.82));
    if(bg)root.style.setProperty('--nf-bg',bg);
    if(surface){root.style.setProperty('--nf-surface',surface);root.style.setProperty('--nf-bg-elevated',surface);root.style.setProperty('--nf-surface-strong',surface);}
    if(bg||surface){const b=bg||(current.mode==='light'?'#f3faf8':'#061512'),s=surface||(current.mode==='light'?'#ffffff':'#0d2420');const text=Math.min(contrast('#ffffff',b),contrast('#ffffff',s))>=4.5?'#ffffff':'#101010';root.style.setProperty('--nf-text',text);root.style.setProperty('--nf-muted',text);}
    document.querySelectorAll('[data-theme-toggle],#themeBtn').forEach(button=>button.setAttribute('aria-label',current.mode==='dark'?'切换至浅色主题':'切换至深色主题'));
    document.querySelectorAll('[data-appearance]').forEach(form=>{form.elements.mode.value=current.mode;form.elements.palette.value=current.palette;form.elements.background.value=current.background||'#061512';form.elements.surface.value=current.surface||'#0d2420';});
  }
  function mount(){document.querySelectorAll('[data-appearance]').forEach(form=>{
    form.innerHTML=`<h2>外观偏好</h2><p class="muted">当前账号独立保存。选择六套配色，或分别设置背景与卡片颜色。</p><div class="form-grid"><label>显示模式<select name="mode"><option value="dark">黑夜版</option><option value="light">白天版</option></select></label><label>配色方案<select name="palette">${Object.entries(palettes).map(([key,p])=>`<option value="${key}">${p[0]}</option>`).join('')}</select></label><label>背景颜色<input name="background" type="color"></label><label>方框 / 卡片颜色<input name="surface" type="color"></label></div><p class="form-error" role="status"></p><div class="actions"><button class="primary" type="submit">保存外观</button><button class="secondary" type="button" data-reset-colors>恢复方案颜色</button></div>`;
    form.addEventListener('change',event=>{const name=event.target.name;apply({[name]:event.target.value,...(['mode','palette'].includes(name)?{background:'',surface:''}:{})});});
    form.querySelector('[data-reset-colors]').addEventListener('click',()=>apply({background:'',surface:''}));
    form.addEventListener('submit',async event=>{event.preventDefault();const status=form.querySelector('[role=status]'),button=form.querySelector('[type=submit]');button.disabled=true;try{
      const b=current.background||(current.mode==='light'?'#f3faf8':'#061512'),s=current.surface||(current.mode==='light'?'#ffffff':'#0d2420');
      if(Math.max(Math.min(contrast('#ffffff',b),contrast('#ffffff',s)),Math.min(contrast('#101010',b),contrast('#101010',s)))<4.5)throw Error('背景与卡片明暗差异过大，请选择能使用相同文字颜色的搭配。');
      await save();status.textContent='外观已保存';
    }catch(error){status.textContent=error.message;}finally{button.disabled=false;}});
  });}
  async function save(){const response=await fetch(kind==='admin'?'/api/v2/admin/me/appearance':'/api/v2/me/appearance',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(current)});if(!response.ok)throw Error('外观保存失败，请重试');localStorage.setItem(`nf-appearance-${kind}-${owner}`,JSON.stringify(current));}
  async function load(type,id){kind=type;owner=String(id);current={mode:'dark',palette:'teal',background:'',surface:''};const cached=localStorage.getItem(`nf-appearance-${kind}-${owner}`);if(cached){try{apply(JSON.parse(cached));}catch{}}
    try{const response=await fetch(kind==='admin'?'/api/v2/admin/me/appearance':'/api/v2/me/appearance');if(!response.ok)throw Error();apply((await response.json()).data);}catch{apply(current);}
  }
  window.NFAppearance={load,apply,mount,setMode(mode){apply({mode,background:'',surface:''});if(owner!=='visitor')save().catch(()=>{});},reset(){owner='visitor';apply({mode:'dark',palette:'teal',background:'',surface:''});}};
  mount();apply(current);
})();
