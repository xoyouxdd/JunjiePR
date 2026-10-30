// Keep the original combinations stable for existing device-local history.
const openings=[
'今天辛苦了。','谢谢你认真走过今天。','忙碌的时候，也别忘了照顾自己。','每一次耐心，都值得被看见。','你不必事事完美。','慢一点，也是一种前进。','平凡的一天，也有值得珍惜的瞬间。','认真做好眼前的小事，已经很了不起。','感谢你带来的善意。','你付出的努力，不必急着得到答案。',
'生活不必时时充满干劲。','希望你记得，休息也是重要的事。','今天的你已经尽力了。','愿你在忙碌中找到自己的节奏。','一声问候，也能让一天变得柔软。','感谢你愿意为身边的人多想一点。','那些不被注意的小事，也有它们的意义。','有些事情慢慢来就好。','不是每一天都需要闪闪发光。','愿你在平常的日子里发现小小的快乐。',
'工作之外，你也有自己的生活和喜欢的事。','今天遇到难题也没关系。','每个人都有需要喘口气的时候。','一份真诚的微笑，是很珍贵的礼物。','谢谢你给团队带来的温暖。','你不需要独自扛下所有事情。','有时停下来看看周围，也很美好。','今天的心情怎样，都可以被接纳。','细心和耐心都需要能量。','愿你对自己也多一点温柔。',
'你认真倾听的样子很珍贵。','小小的进步也值得开心。','愿你今天遇到理解你的人。','一次友好的回应，也能照亮别人的一天。','谢谢你一直以来的用心。','不顺利的时候，允许自己重新整理。','每个人都有属于自己的步调。','愿今天的你少一点匆忙。','把事情一件件做好，就已经足够。','生活里总有一些小小的惊喜。'
];
const endings=[
'愿你今天能喝上一杯喜欢的饮料。','愿你忙完后有片刻真正的放松。','愿你遇到困难时有人愿意搭把手。','愿你今天听到一句真诚的感谢。','愿你下班的路上心情轻快。','愿你在小小的间隙里深呼吸。','愿你的付出得到温暖的回应。','愿你今晚睡个安稳的好觉。','愿你今天遇到一件让你微笑的小事。','愿你拥有不被催促的一刻。',
'愿你能按自己的节奏完成眼前的事。','愿你今天的每一步都踏实。','愿你遇到的人都待你友善。','愿你留一点时间给喜欢的事情。','愿你在疲惫时得到及时的休息。','愿你今天的努力都不被辜负。','愿你有勇气说出自己的需要。','愿你心里留有一小块轻松的地方。','愿今天的好天气也照进你的心里。','愿你有机会吃到喜欢的食物。',
'愿你发现身边不经意的善意。','愿你和伙伴们的配合顺顺利利。','愿你在不确定中也能安心。','愿你今天有足够的耐心，也有被耐心对待的幸运。','愿你把自己放在需要被关心的位置。','愿你在繁忙中也能舒展一下肩膀。','愿你今晚能放下白天的烦恼。','愿每个小小的期待都有回应。','愿你听到一首让心情变好的歌。','愿你有机会和喜欢的人聊聊天。',
'愿你今天多遇到一些好消息。','愿你遇到麻烦时不必着急责怪自己。','愿你在需要帮助时放心开口。','愿你为自己已经完成的事感到欣慰。','愿你今天有一个自在的笑容。','愿你的努力被温柔地记住。','愿接下来的时间对你宽容一些。','愿你偶尔抬头，看看身边的风景。','愿你今天有一份小小的惊喜。','愿你每次出发都有平安的归途。',
'愿你今天能找到适合自己的节奏。','愿你在平淡的日子里也感到充实。','愿你在忙碌结束后安心地休息。','愿你有时间好好享受一顿饭。','愿你被理解，也被尊重。','愿你记得肯定自己走过的路。','愿你和身边的人彼此照应。','愿你今天少一点烦心，多一点从容。','愿你把不愉快留在过去，让心情慢慢变好。','愿你明天醒来时，心里有一点新的期待。'
];
const originalWishes=[
'愿你今天遇到的人，都能给彼此多一点耐心。',
'如果今天过得有点累，回家后请安心地歇一歇。',
'不管今天收获多少，都别忘了给自己一个肯定。',
'愿你走过的每段忙碌，最后都能换来片刻安宁。',
'今天做得好的地方，值得你悄悄为自己骄傲。',
'愿你在一声普通的问候里，感受到被惦记。',
'事情一时没有答案也没关系，先让自己轻松一点。',
'愿你能遇见愿意认真听你说话的人。',
'愿窗外的风、路边的树，给你带来一点好心情。',
'今天也许并不完美，但你依然值得被善待。',
'感谢你在忙碌时，仍愿意把事情交代清楚。',
'愿你身边总有人愿意分享，也愿意倾听。',
'如果有件事让你开心，就请把这份开心多留一会儿。',
'愿你今天的晚餐合口味，回家的路也顺利。',
'你可以先照顾好自己，再慢慢照顾其他事情。',
'遇到不顺的时刻，允许自己稍作停顿。',
'愿你今天有时间喝完那杯已经准备好的水。',
'谢谢你对细节的留心，让许多事情变得更顺畅。',
'愿你在平常的一天里，遇见一个意想不到的好瞬间。',
'做完一件事，就给自己一点放松的空间吧。',
'愿你在需要支持的时候，身边刚好有可靠的伙伴。',
'今天没有完成的事，可以明天再继续。',
'希望你能听到一句发自内心的“谢谢”。',
'愿你面对变化时，仍能找到自己的步调。',
'谢谢你愿意多走一步，帮助需要帮助的人。',
'愿今天的你被好好理解，也能自在地表达。',
'不必和别人比较，你有属于自己的闪光之处。',
'愿今天的小困扰，都能找到合适的解决办法。',
'哪怕只是短短几分钟，也愿你能好好放松。',
'希望你在忙碌之后，能见到让自己开心的人。',
'愿你今天收到的善意，也成为心里的一点暖意。',
'有时候先休息一下，事情会更容易理清。',
'愿你今天不必着急赶路，平安抵达每个目的地。',
'谢谢你在合作中愿意沟通，也愿意互相体谅。',
'愿你今天的好心情，有一个温柔的开始。',
'遇到新问题时，不会马上解决也没有关系。',
'愿你的辛苦有人看见，你的休息也不被打扰。',
'希望你今天能做一件自己真心喜欢的小事。',
'愿你在喧闹中找到片刻安静，让心情慢下来。',
'你的感受值得被认真对待，包括你自己的感受。',
'愿你和伙伴之间的每次交流，都多一点理解。',
'希望你今天出门顺利，回家也平安。',
'愿你今天少一些自我责备，多一些耐心。',
'如果今天有点灰暗，愿明天能亮一点点。',
'感谢你把该做的事放在心上，也请把自己放在心上。',
'愿你今天的笑容，是因为真的遇到了开心的事。',
'不需要一直坚强，想休息时就给自己一点时间。',
'愿那些认真准备的事情，都有一个顺利的开始。',
'希望你在熟悉的日常里，也能发现新的乐趣。',
'愿你在疲倦的时候，有人轻声说一句“辛苦了”。',
'愿你有一顿不必匆忙的饭，和一段安静的时光。',
'今天如果迈出了一小步，也请记得为自己高兴。',
'愿你身边的每份善意，都能被好好珍惜。',
'你的节奏不必和别人一样，走得踏实就很好。',
'希望今天的你能遇到一件简单又开心的事。',
'愿你拥有拒绝不合理要求的勇气和底气。',
'和伙伴一起解决问题的时刻，值得被记住。',
'愿你今天既能照顾他人，也有人照顾你的心情。',
'每个认真生活的人，都值得一份温柔的祝愿。',
'希望你下班后，能把时间留给自己喜欢的事。',
'愿你今天说出的每句真诚，都得到善意回应。',
'如果眼前的事有点多，就先从最容易的一件开始。',
'愿你的付出无需声张，也能被身边的人珍惜。',
'希望你今天能找到一个让肩膀放松的瞬间。',
'愿忙完之后的你，有一段安静自在的时间。',
'谢谢你愿意认真对待每一次小小的托付。',
'今天的你可以有好心情，也可以有不那么好的心情。',
'愿你遇到不同意见时，依然能被尊重地倾听。',
'愿你今天能把重要的事做好，也把自己照顾好。',
'有些温暖不需要很盛大，一句问候就足够。',
'愿你和信任的人相处时，能够放下疲惫。',
'如果今天走得有些慢，也请相信自己的脚步。',
'愿你在偶然抬头时，看到喜欢的天空。',
'希望你今天能收到一个让你安心的消息。',
'愿你在困难面前，有商量的人，也有喘息的时间。',
'谢谢你那些没有被写进清单的细心。',
'愿你有一段不用看时间、只管放松的时光。',
'每一次重新开始，都可以从一个小动作做起。',
'愿今天的忙碌结束后，你能踏踏实实地休息。',
'希望你对别人的善意，也能回到自己身边。',
'愿你在团队里既有归属感，也保有自己的空间。',
'今天的事情不必全部解决，先完成眼前的一步。',
'愿你今天遇到的问题，比想象中更容易处理。',
'希望你在日复一日的工作中，仍能感受到自己的价值。',
'愿你拥有一个不被打断的休息片刻。',
'如果心里装了太多事，试着先把呼吸放慢。',
'愿一句简单的“我来帮你”，让你今天轻松一些。',
'感谢你在自己的位置上，认真照顾每一个细节。',
'愿你今天的付出之外，也有属于自己的快乐。',
'不必把每个小失误都放大，你也有做得很好的地方。',
'愿你今天被一首歌、一阵风或一段对话温柔安慰。',
'希望你回想今天时，能记得至少一件暖心的小事。',
'愿你在合作中遇到默契，在独处时感到自在。',
'你可以慢慢调整，生活不要求每一步都走得一样快。',
'愿今天的你有力气忙碌，也有机会好好休息。',
'希望你的努力被理解，而不是只被当成理所当然。',
'愿你对未来有期待，也能安心过好眼前的一天。',
'遇到值得开心的事，不妨认真感受那一刻。',
'愿你的每一份真诚，都有温暖的落点。',
'谢谢你成为团队里独一无二的一员。'
];
const combinedTotal=openings.length*endings.length,total=combinedTotal+originalWishes.length;
function dayKey(){const d=new Date();return `${d.getFullYear()}-${d.getMonth()+1}-${d.getDate()}`;}
function read(key){try{return JSON.parse(localStorage.getItem(key))||{};}catch(_){return {};}}
function save(key,value){try{localStorage.setItem(key,JSON.stringify(value));}catch(_){}}
function choose(history){const recent=new Set(history.slice(-90)),preferOriginal=Math.random()<0.5;let candidates=[];for(let i=preferOriginal?combinedTotal:0;i<(preferOriginal?total:combinedTotal);i++)if(!recent.has(i))candidates.push(i);if(!candidates.length)for(let i=0;i<total;i++)if(!recent.has(i))candidates.push(i);return candidates[Math.floor(Math.random()*candidates.length)];}
function mountDailyWish(host,userId){
  const key=`fz-daily-wish-v1-${userId}`,stored=read(key);let history=Array.isArray(stored.history)?stored.history.filter(n=>Number.isInteger(n)&&n>=0&&n<total).slice(-90):[];
  let id=stored.day===dayKey()&&Number.isInteger(stored.id)&&stored.id>=0&&stored.id<total?stored.id:choose(history);
  const remember=()=>{history=history.filter(n=>n!==id);history.push(id);history=history.slice(-90);save(key,{day:dayKey(),id,history});};remember();
  host.innerHTML='<details class="daily-wish"><summary><span aria-hidden="true">✦</span><strong>今日小祝福</strong><small>给自己一点温柔</small></summary><div class="daily-wish-body"><p class="daily-wish-hint">轻轻刮开，看看今天的祝福</p><div class="daily-wish-card"><p data-wish-text></p><canvas data-wish-scratch aria-hidden="true"></canvas></div><div class="daily-wish-actions"><button type="button" class="secondary" data-wish-reveal>直接查看</button><button type="button" class="secondary" data-wish-next>换一句</button></div><p class="daily-wish-note">祝福仅供放松，与积分和考核无关。</p></div></details>';
  const text=host.querySelector('[data-wish-text]'),canvas=host.querySelector('canvas'),ctx=canvas.getContext('2d');let revealed=false,drawing=false,last=null,initialized=false;
  function draw(){initialized=true;text.textContent=id<combinedTotal?openings[Math.floor(id/endings.length)]+endings[id%endings.length]:originalWishes[id-combinedTotal];revealed=false;const rect=canvas.getBoundingClientRect(),ratio=window.devicePixelRatio||1;canvas.width=Math.round(rect.width*ratio);canvas.height=Math.round(rect.height*ratio);ctx.setTransform(ratio,0,0,ratio,0,0);ctx.globalCompositeOperation='source-over';ctx.fillStyle='#dcece2';ctx.fillRect(0,0,rect.width,rect.height);ctx.fillStyle='#527861';ctx.font='14px sans-serif';ctx.textAlign='center';ctx.fillText('✦ 轻轻刮开 ✦',rect.width/2,rect.height/2+5);}
  function reveal(){revealed=true;ctx.clearRect(0,0,canvas.width,canvas.height);}
  function scratch(e){if(!drawing||revealed)return;const r=canvas.getBoundingClientRect(),point={x:e.clientX-r.left,y:e.clientY-r.top};ctx.globalCompositeOperation='destination-out';ctx.lineWidth=38;ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath();ctx.moveTo((last||point).x,(last||point).y);ctx.lineTo(point.x,point.y);ctx.stroke();last=point;}
  canvas.addEventListener('pointerdown',e=>{drawing=true;last=null;canvas.setPointerCapture(e.pointerId);scratch(e);});canvas.addEventListener('pointermove',scratch);canvas.addEventListener('pointerup',()=>{drawing=false;last=null;});canvas.addEventListener('pointercancel',()=>{drawing=false;last=null;});
  host.querySelector('[data-wish-reveal]').onclick=reveal;host.querySelector('[data-wish-next]').onclick=()=>{id=choose(history);remember();draw();};
  host.querySelector('details').addEventListener('toggle',e=>{if(e.currentTarget.open&&!initialized)draw();});
}
export { mountDailyWish };
