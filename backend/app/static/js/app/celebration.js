// Four short local effects; no polling, external libraries, audio or score writes.
function bindCelebration(host){
  const button=host.querySelector('[data-celebrate]');if(!button)return ()=>{};
  let bag=[],last=-1,timer=null;
  const motion=window.matchMedia('(prefers-reduced-motion: reduce)');
  const clear=()=>{clearTimeout(timer);host.querySelector('.honor-celebration')?.remove();};
  button.onclick=()=>{
    clear();
    if(motion.matches){button.textContent='每一份认真，都值得庆祝';return;}
    if(!bag.length){bag=[0,1,2,3].sort(()=>Math.random()-.5);if(bag[0]===last)[bag[0],bag[1]]=[bag[1],bag[0]];}
    last=bag.shift();
    const layer=document.createElement('div');layer.className='honor-celebration';layer.dataset.effect=String(last);layer.setAttribute('aria-hidden','true');
    for(let i=0;i<24;i++){const dot=document.createElement('i');dot.style.setProperty('--x',`${Math.round(Math.random()*96)}%`);dot.style.setProperty('--delay',`${Math.random()*.3}s`);dot.style.setProperty('--drift',`${Math.round(Math.random()*100-50)}px`);layer.append(dot);}
    host.append(layer);timer=setTimeout(clear,2200);
  };
  const change=()=>{if(motion.matches)clear();};motion.addEventListener('change',change);
  return ()=>{clear();button.onclick=null;motion.removeEventListener('change',change);};
}
export { bindCelebration };
