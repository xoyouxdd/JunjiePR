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
'谢谢你成为团队里独一无二的一员。',

// 普通的一天
'要是今天出门早，希望到地方时，刚好能喝上一口热的。',
'刚到一个不熟的地方，旁边有人说一句「我带你去」，心里就踏实多了。',
'有人说「这儿有我呢」，那一刻，手里的事就能放心放一放。',
'希望你今天也能听见一句「你要用的东西，我都给你留好了」。',
'同事多说一句「这个放在右边那格」，接下来就能少找半天。这样的小事也挺暖的。',
'如果今天碰上不熟悉的事，希望旁边刚好有个人肯慢慢跟你讲。',
'谁都有头一回。有人愿意等你多看一眼、多问一句，那就挺好。',
'希望你今天出门一路顺，路上还能碰见点让你高兴的小事。',
'忙起来之前，能有几分钟坐着喝口水，也挺舒服的。',
'希望你今天和同事凑到一块儿，有事好商量，闲下来也有话聊。',
'愿你今天在路上碰见熟悉的人，隔着几步就笑着打了招呼。',
'要是今天的安排临时变了，希望忙完以后，还能留点时间做自己想做的事。',
'有人愿意把自己当初也问过的问题，再耐心讲一遍。刚来的时候，碰上这样的同事真好。',
'希望你忙完手上这一阵，刚好有人走过来说「辛苦了，剩下的我来」。',
'和第一次一起做事的人，也有可能越忙越默契，后来一个眼神就懂了。',
'要是早上没顾上吃东西，希望你过一会儿就能吃上一口热乎的。',
'希望今天走路不赶，鞋穿着舒服，伸手要拿的东西也刚好在手边。',
'好几天没见的同事，一碰面就说「哎，你回来啦」。听见这句，心里会热一下。',
'希望今天手里的事能一件件做完，中间还能有点空喘口气。',
'希望你今天抬头的时候，刚好看见一片好看的天，也刚好有空多看两眼。',

// 忙的时候，有人搭把手
'忙不过来的时候，希望刚好有人走过来问一句「要不要我帮忙？」',
'谢谢你忙着自己的事，还记得回头问一句：「你那边还好吗？」',
'有时还没开口，同事已经把你要的东西递过来了。有人跟你想到一块儿，真好。',
'队伍一下子长了，这时有人说「这边我来，你先忙手上的」，听着就安心。',
'同一件事讲了好多遍，希望你忙完能喝口水，歇歇嗓子。',
'有时候同事只是顺手递了样东西，就帮你解了围。有人留意到你忙不过来，感觉挺好。',
'希望今天有位客人走出两步，又回过头跟你说了声谢谢。',
'下雨的时候，希望刚好有人往里挪一点，给你让个淋不到雨的位置。',
'天热的时候，希望你歇脚的地方有阴凉，水也在手边。',
'忙完一阵再抬头，发现同事还在身边。那一眼就挺踏实的。',
'谢谢你看见旁边的人腾不开手，顺手把那件小事接了过去。',
'好几件事挤到一块儿时，有人说「咱俩一起弄」，一下就没那么慌了。',
'排队的人多起来时，希望你们还能隔着几步喊一句「这边有我」。',
'谢谢你愿意把客人着急问的那件事慢慢讲明白。希望忙完这一阵，你也能歇会儿。',
'希望今天有个小朋友特别认真地跟你说「谢谢」，认真得让你都笑了。',
'一时答不上来时，旁边有人说「等一下，我知道找谁问」。有个搭档真好。',
'大家都在忙的时候，还有人看见你那边需要帮忙。希望你今天也遇到这样的同事。',
'要是今天有人替你挡了阵风，或帮你照看了一会儿，希望你们也能找个空聊两句。',
'希望最忙的那阵过去以后，你和同事还能开两句玩笑。',
'大家把事情交给你很放心。也希望你身边有个人，让你交给他也一样放心。',

// 吃口饭，歇一会儿
'希望你今天吃饭的时候，饭还是热的，也有时间慢慢吃。',
'希望今天有几分钟，想看手机就看一会儿，想安静坐着也行。',
'吃完饭一看，居然还有几分钟能靠一会儿。希望你今天也有这样的空当。',
'那杯水要是放了半天，希望这会儿刚好能想起来，喝上几口。',
'站久了能坐下来的那一刻，腿一下就松了。希望你今天有这么一会儿。',
'走了一天，回家换上拖鞋的时候，整个人都能松下来一点。',
'晚班辛苦了。希望走出去的那一刻，你就能慢下来，想想回家做点什么。',
'希望你下班刚好赶上那趟车，上车一看，还有个空位。',
'要是今天回得晚，希望进门的时候屋里暖暖的，还能吃上口热的。',
'希望你忙完往外走的时候，心里惦记的是晚饭想吃什么。',
'希望你下班路上买到喜欢吃的那样东西，回家打开还是热的。',
'今天已经赶了好几趟。希望最后这一段路，可以慢慢走。',
'要是鞋袜被雨打湿了，回家换上干袜子的那一下，应该特别舒服。',
'忙完想跟同事聊两句就聊两句，想戴上耳机一个人走也挺好。',
'希望你回到家，换上舒服的衣服，还有点时间做自己喜欢的事。',
'希望你今晚洗个舒服的热水澡，躺下没多久就睡着了。',
'明天要是休息，希望今晚想到不用赶早，心里就已经松快一点。',
'希望你忙完往外走时，天还没黑，刚好赶上一点好看的晚霞。',
'今天在外面说了好多话的话，希望回去有空放首喜欢的歌，安安静静地听完。',
'希望你回家路上收到一句「到了说一声」。有人惦记你平安到家，挺暖的。',

// 遇到不顺的时候
'有句话说完才觉得没说好，谁都有这种时候。希望它今天就慢慢过去了。',
'有件事一时想不明白也正常。说不定哪天跟熟悉的人聊两句，就想通了。',
'要是今天受了点委屈，希望有人能听你说完，还问一句「后来呢？」',
'谁都有手忙脚乱的时候。这时有人说一句「没事，我陪你理一下」，心里就能松一点。',
'累得不想说话的时候，希望你能跟一个坐在旁边也不用找话说的人待一会儿。',
'做得不顺的那一小段，跟那些做得挺好的时候一样，都只是今天的一部分。',
'碰到心里没底的事，希望刚好有个熟悉的人愿意陪你过一遍。',
'有句话在心里憋了一天的话，希望今天能碰见一个听了也懂你的人。',
'有人看见你已经很认真了，还特意跟你说一声。希望今天也有人这样跟你说。',
'忙到一转头就忘了要拿什么，太正常了。希望你还有空停一下，喝两口水。',
'安排临时变了，前面花的心思也算数。希望后面的事能比预想中顺一点。',
'要是今天有点憋气，希望走出去吹吹风，心里能松一点。',
'那阵特别难熬的时候，希望有人愿意在旁边陪一会儿。安静坐着也好。',
'今天要是还有件事没做完，希望它先留在那儿，你能踏实吃顿饭。',
'希望你说「我想歇一下」的时候，有人回你一句「行，我在这儿」。',
'有时两个人都忙，说的话没对上。过一会儿，能找到个空当好好聊两句就好。',
'希望今天有人看到你忙完了，顺口问一句「刚才累坏了吧？」',
'如果今天过得有点乱，希望回头想想，还能记起中间有一小段其实挺好。',
'有件事一直惦记着，希望你能把它写在纸上，或者跟谁说两句，心里先放下一点。',
'要是这几天没睡好，希望今晚能早一点睡着，醒来的时候人轻松些。',

// 日常里的小开心
'今天有人随手帮了你一下的话，希望那一点好心情能陪你久一点。',
'希望你路过同事身边时，刚好听到一句好笑的，也跟着乐了一会儿。',
'同事递来一杯饮料，说「我记得你不喝冰的」。有人记得这点小事，真的会开心。',
'希望你哪天翻到一条旧消息，还会为了当初那件事再笑一遍。',
'好久没碰面的同事，见了面还能很自然地问一句「最近怎么样」。这就挺好。',
'希望今天走到树下时，刚好有风吹过来，能凉快一会儿。',
'希望哪天有人随口跟你说「跟你一起忙，我挺放心的」。这句话听着真舒服。',
'同事分来的小零食，忙完才想起来还有。拆开一看，刚好是喜欢的口味。',
'有人忙完自己的事，又多问了一句「你这边还要帮忙吗？」这一句有时真能解围。',
'希望今天有个人叫住你，只是为了告诉你一件好笑的事。',
'希望你忙完还有心情看一集喜欢的剧，看到好笑的地方就笑出声。',
'忙里顺手拍到的那片天空，哪天再翻出来看，也许还会想起当时吹过来的风。',
'希望你打开手机，刚好收到一条跟工作无关、又让你高兴的消息。',
'刚来的时候，有人带着走了一趟，连东西放在哪儿也说了。过了很久，可能还记得那个人。',
'希望这周有一天醒来，你看了眼时间，发现还能翻个身再睡半小时。',
'和同事一起忙完一件费劲的事，两个人互相看一眼，说「总算弄好了」。这一刻值得高兴一下。',
'听见「给你也留了一份」，还没看见是什么，心里就先高兴了一点。',
'希望你今天跟别人说了几次「辛苦啦」，也有人回头跟你说一声「你也辛苦了」。',
'你顺手帮过的那个忙，对你可能就一会儿。对方后来想起来，也许还记得那时候有人帮了他。',
'希望回头想起今天，你还能记得一件跟工作无关、光是想起来就有点高兴的小事。'
];
const combinedTotal=openings.length*endings.length,total=combinedTotal+originalWishes.length;
function dayKey(){const d=new Date();return `${d.getFullYear()}-${d.getMonth()+1}-${d.getDate()}`;}
function read(key){try{return JSON.parse(localStorage.getItem(key))||{};}catch(_){return {};}}
function save(key,value){try{localStorage.setItem(key,JSON.stringify(value));return true;}catch(_){return false;}}
function validId(value){return Number.isInteger(value)&&value>=0&&value<total;}
function wishText(id){return id<combinedTotal?openings[Math.floor(id/endings.length)]+endings[id%endings.length]:originalWishes[id-combinedTotal];}
function choose(history){const recent=new Set(history.slice(-90)),preferOriginal=Math.random()<0.5;let candidates=[];for(let i=preferOriginal?combinedTotal:0;i<(preferOriginal?total:combinedTotal);i++)if(!recent.has(i))candidates.push(i);if(!candidates.length)for(let i=0;i<total;i++)if(!recent.has(i))candidates.push(i);return candidates[Math.floor(Math.random()*candidates.length)];}
function bindScratchCard(canvas,caption,reveal,beforeScratch){
  const ctx=canvas.getContext('2d');
  let drawing=false,last=null,width=0,height=0,disposed=false,hasScratches=false;
  function draw(force=false){
    if(disposed||canvas.hidden||!ctx)return;
    const rect=canvas.getBoundingClientRect();if(!rect.width||!rect.height)return;
    if(!force&&rect.width===width&&rect.height===height)return;
    let mask=null;
    if(!force&&hasScratches&&canvas.width&&canvas.height){mask=document.createElement('canvas');mask.width=canvas.width;mask.height=canvas.height;const maskContext=mask.getContext('2d');if(maskContext)maskContext.drawImage(canvas,0,0);else mask=null;}
    width=rect.width;height=rect.height;last=null;
    const ratio=Math.min(window.devicePixelRatio||1,2);canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);ctx.setTransform(ratio,0,0,ratio,0,0);ctx.globalCompositeOperation='source-over';
    if(mask){ctx.drawImage(mask,0,0,width,height);return;}
    const gradient=ctx.createLinearGradient(0,0,width,height);gradient.addColorStop(0,'#eee6cd');gradient.addColorStop(.5,'#ddd5ba');gradient.addColorStop(1,'#f2ead3');ctx.fillStyle=gradient;ctx.fillRect(0,0,width,height);
    ctx.strokeStyle='#ae9b6a55';ctx.lineWidth=1;for(let x=-height;x<width;x+=14){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x+height,height);ctx.stroke();}
    ctx.fillStyle='#5c6049';ctx.font='500 13px system-ui';ctx.textAlign='center';ctx.fillText('轻轻刮开',width/2,height/2-2);ctx.font='11px system-ui';ctx.fillText(caption,width/2,height/2+19);
  }
  function reset(){
    canvas.hidden=false;
    hasScratches=false;
    drawing=false;
    width=height=0;
    draw(true);
  }
  function scratch(event){if(!drawing||canvas.hidden||!ctx)return;hasScratches=true;const rect=canvas.getBoundingClientRect(),point={x:event.clientX-rect.left,y:event.clientY-rect.top};ctx.globalCompositeOperation='destination-out';ctx.strokeStyle='#000000';ctx.lineWidth=32;ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath();ctx.moveTo((last||point).x,(last||point).y);ctx.lineTo(point.x,point.y);ctx.stroke();last=point;}
  function start(event){beforeScratch?.();if(canvas.hidden||!ctx)return;drawing=true;last=null;canvas.setPointerCapture(event.pointerId);scratch(event);}
  function end(){if(!drawing)return;drawing=false;last=null;if(!ctx||!canvas.width||!canvas.height)return;const pixels=ctx.getImageData(0,0,canvas.width,canvas.height).data;let blank=0,count=0;for(let i=3;i<pixels.length;i+=64){count++;if(pixels[i]<64)blank++;}if(count&&blank/count>.32)reveal();}
  canvas.addEventListener('pointerdown',start);canvas.addEventListener('pointermove',scratch);canvas.addEventListener('pointerup',end);canvas.addEventListener('pointercancel',end);
  const observer=new ResizeObserver(()=>draw());observer.observe(canvas);
  return {draw,reset,dispose(){disposed=true;observer.disconnect();canvas.removeEventListener('pointerdown',start);canvas.removeEventListener('pointermove',scratch);canvas.removeEventListener('pointerup',end);canvas.removeEventListener('pointercancel',end);}};
}
function mountDailyWish(host,userId,{integrated=false}={}){
  if(!host)return ()=>{};
  const target=integrated?host.querySelector('.skin-story-wish-host'):host;
  if(!target)return ()=>{};
  const key=`fz-daily-wish-v1-${userId}`,stored=read(key),today=dayKey();
  const favoritesKey=`fz-daily-wish-favorites-v1-${userId}`;
  const previous=host._dailyWishContext,storedId=stored.day===today&&validId(stored.id)?stored.id:null;
  if(previous&&previous.userId===String(userId)&&previous.integrated===integrated&&previous.target===target&&previous.canvas.isConnected&&previous.day===today&&(storedId===null||storedId===previous.id))return host._disposeDailyWish;
  host._disposeDailyWish?.();
  let currentDay=today;
  let history=Array.isArray(stored.history)?stored.history.filter(validId).slice(-90):[];
  let id=storedId===null?choose(history):storedId;
  const remember=()=>{history=history.filter(n=>n!==id);history.push(id);history=history.slice(-90);save(key,{day:currentDay,id,history});};remember();
  const content=`<div class="daily-wish-body">
    <span class="daily-wish-kicker">FZ · 今日小祝福</span>
    <div class="daily-wish-card"><p data-wish-text aria-hidden="true"></p><canvas data-wish-scratch aria-hidden="true"></canvas></div>
    <div class="daily-wish-actions">
      <button type="button" class="secondary" data-wish-reveal>直接查看</button>
      <button type="button" class="secondary" data-wish-next>换一句</button>
      <button type="button" class="secondary" data-wish-favorite aria-label="收藏这句祝福" aria-pressed="false" hidden>收藏</button>
      <button type="button" class="secondary" data-wish-favorites-toggle aria-expanded="false">我的收藏</button>
    </div>
    <p class="daily-wish-favorite-error" data-wish-favorite-error role="alert" hidden></p>
    <section class="daily-wish-favorites" aria-label="我的收藏" data-wish-favorites hidden>
      <div class="daily-wish-favorites-heading">
        <p class="daily-wish-favorites-note">收藏保存在当前浏览器。</p>
        <button type="button" class="secondary" data-wish-favorites-draw hidden>翻一张</button>
      </div>
      <p class="daily-wish-favorites-empty" data-wish-favorites-empty>刮开后，喜欢的话可以留在这里。</p>
      <div data-wish-favorites-card hidden>
        <div class="daily-wish-card"><p data-wish-replay-text aria-hidden="true"></p><canvas data-wish-replay-scratch aria-hidden="true"></canvas></div>
        <div class="daily-wish-actions">
          <button type="button" class="secondary" data-wish-replay-reveal>直接查看收藏</button>
          <button type="button" class="secondary" data-wish-replay-back>返回收藏</button>
        </div>
      </div>
      <ul data-wish-favorites-list></ul>
    </section>
    <p class="daily-wish-note">祝福仅供放松，与积分和考核无关。</p>
    <span class="wish-status" data-wish-status role="status" aria-live="polite"></span>
  </div>`;
  target.innerHTML=integrated?content:'<details class="daily-wish"><summary><span aria-hidden="true">✦</span><strong>今日小祝福</strong><small>给自己一点温柔</small></summary>'+content+'</details>';
  host.hidden=false;host.dataset.dailyWish='';
  const text=target.querySelector('[data-wish-text]'),canvas=target.querySelector('[data-wish-scratch]'),revealButton=target.querySelector('[data-wish-reveal]'),nextButton=target.querySelector('[data-wish-next]');
  const favoriteButton=target.querySelector('[data-wish-favorite]'),favoritesToggle=target.querySelector('[data-wish-favorites-toggle]');
  const favoritesPanel=target.querySelector('[data-wish-favorites]'),favoritesList=target.querySelector('[data-wish-favorites-list]');
  const favoritesEmpty=target.querySelector('[data-wish-favorites-empty]'),favoriteError=target.querySelector('[data-wish-favorite-error]');
  const replayButton=target.querySelector('[data-wish-favorites-draw]'),replayCard=target.querySelector('[data-wish-favorites-card]');
  const replayText=target.querySelector('[data-wish-replay-text]'),replayCanvas=target.querySelector('[data-wish-replay-scratch]');
  const replayReveal=target.querySelector('[data-wish-replay-reveal]'),replayBack=target.querySelector('[data-wish-replay-back]');
  const status=target.querySelector('[data-wish-status]');
  let revealed=false,disposed=false,replayId=null;
  const dailyScratch=bindScratchCard(canvas,'收下今天的小祝福',reveal,synchronizeDay);
  const favoriteScratch=bindScratchCard(replayCanvas,'再读一句收藏的祝福',revealReplay);
  const mountContext={userId:String(userId),integrated,target,canvas,get day(){return currentDay;},get id(){return id;}};
  host._dailyWishContext=mountContext;
  const writeText=()=>{text.textContent=wishText(id);};writeText();
  function readFavorites(){
    const storedFavorites=read(favoritesKey);
    return Array.isArray(storedFavorites)?[...new Set(storedFavorites.filter(validId))]:[];
  }
  function renderFavorites(ids){
    const collected=ids.includes(id);
    favoriteButton.hidden=!revealed;
    favoriteButton.setAttribute('aria-pressed',String(collected));
    favoriteButton.textContent=collected?'已收藏':'收藏';
    replayButton.hidden=ids.length===0;
    if(replayId!==null&&!ids.includes(replayId))replayId=null;
    replayCard.hidden=replayId===null;
    favoritesList.hidden=replayId!==null;
    if(favoritesPanel.hidden)return;
    favoritesEmpty.hidden=ids.length>0;
    favoritesList.replaceChildren(...ids.map((favoriteId,index)=>{
      const item=document.createElement('li');
      const quote=document.createElement('p');
      quote.textContent=wishText(favoriteId);
      const remove=document.createElement('button');
      remove.type='button';
      remove.className='secondary';
      remove.textContent='移除';
      remove.setAttribute('aria-label',`移除第 ${index+1} 条收藏`);
      remove.onclick=()=>{
        if(changeFavorite(favoriteId,false)){
          const next=favoritesList.querySelectorAll('button')[index];
          (next||favoritesList.querySelector('li:last-child button')||favoritesToggle).focus();
        }
      };
      item.append(quote,remove);
      return item;
    }));
  }
  function changeFavorite(favoriteId,add){
    const otherIds=readFavorites().filter(savedId=>savedId!==favoriteId);
    const next=add?[favoriteId,...otherIds]:otherIds;
    if(!save(favoritesKey,next)){
      favoriteError.textContent='浏览器暂时存不了收藏，请稍后再试。';
      favoriteError.hidden=false;
      return false;
    }
    favoriteError.hidden=true;
    renderFavorites(next);
    status.textContent=add?'已收藏这句祝福':'已移除收藏';
    return true;
  }
  function revealReplay(){
    if(disposed||replayId===null||replayCanvas.hidden)return;
    replayCanvas.hidden=true;
    replayText.removeAttribute('aria-hidden');
    replayReveal.hidden=true;
    status.textContent=replayText.textContent;
  }
  function drawFavorite(){
    const ids=readFavorites();
    if(!ids.length){renderFavorites(ids);return;}
    replayId=ids[Math.floor(Math.random()*ids.length)];
    replayText.textContent=wishText(replayId);
    replayText.setAttribute('aria-hidden','true');
    replayReveal.hidden=false;
    renderFavorites(ids);
    favoriteScratch.reset();
    status.textContent='翻出了一张收藏的祝福，轻轻刮开看看。';
  }
  function reset(){writeText();revealed=false;text.setAttribute('aria-hidden','true');revealButton.disabled=false;revealButton.textContent='直接查看';favoriteButton.hidden=true;favoriteError.hidden=true;status.textContent='新的祝福已准备好';dailyScratch.reset();}
  function synchronizeDay(){
    const nextDay=dayKey();if(disposed||nextDay===currentDay)return false;
    const latest=read(key);if(Array.isArray(latest.history))history=latest.history.filter(validId).slice(-90);
    currentDay=nextDay;id=latest.day===nextDay&&validId(latest.id)?latest.id:choose(history);remember();reset();return true;
  }
  function reveal(){if(disposed)return;synchronizeDay();if(revealed)return;revealed=true;canvas.hidden=true;text.removeAttribute('aria-hidden');revealButton.disabled=true;revealButton.textContent='已收下祝福';renderFavorites(readFavorites());status.textContent=text.textContent;}
  revealButton.onclick=reveal;
  nextButton.onclick=()=>{if(!synchronizeDay()){id=choose(history);remember();reset();}};
  favoriteButton.onclick=()=>{
    if(!synchronizeDay()&&revealed)changeFavorite(id,!readFavorites().includes(id));
  };
  favoritesToggle.onclick=()=>{
    favoritesPanel.hidden=!favoritesPanel.hidden;
    favoritesToggle.setAttribute('aria-expanded',String(!favoritesPanel.hidden));
    favoriteError.hidden=true;
    renderFavorites(readFavorites());
  };
  replayButton.onclick=drawFavorite;
  replayReveal.onclick=()=>{revealReplay();replayBack.focus();};
  replayBack.onclick=()=>{
    replayId=null;
    renderFavorites(readFavorites());
    (replayButton.hidden?favoritesToggle:replayButton).focus();
  };
  const onStorage=event=>{if(event.key===favoritesKey||event.key===null)renderFavorites(readFavorites());};
  window.addEventListener('storage',onStorage);
  const details=target.querySelector('details'),onToggle=()=>{if(details?.open)dailyScratch.draw();};details?.addEventListener('toggle',onToggle);if(integrated)dailyScratch.draw();
  const onVisible=()=>{if(!document.hidden)synchronizeDay();};
  // The next button owns its day rollover; capturing it here would choose twice.
  const onActivity=event=>{if(event.target?.closest?.('[data-wish-next]'))return;synchronizeDay();};
  host.addEventListener('pointerdown',onActivity,true);host.addEventListener('keydown',onActivity,true);document.addEventListener('visibilitychange',onVisible);window.addEventListener('focus',onVisible);
  const dispose=()=>{disposed=true;dailyScratch.dispose();favoriteScratch.dispose();details?.removeEventListener('toggle',onToggle);host.removeEventListener('pointerdown',onActivity,true);host.removeEventListener('keydown',onActivity,true);document.removeEventListener('visibilitychange',onVisible);window.removeEventListener('focus',onVisible);window.removeEventListener('storage',onStorage);revealButton.onclick=null;nextButton.onclick=null;favoriteButton.onclick=null;favoritesToggle.onclick=null;replayButton.onclick=null;replayReveal.onclick=null;replayBack.onclick=null;favoritesList.replaceChildren();if(host._dailyWishContext===mountContext){delete host._dailyWishContext;delete host._disposeDailyWish;}};
  host._disposeDailyWish=dispose;return dispose;
}
export { mountDailyWish };
