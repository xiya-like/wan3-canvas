#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
无头浏览器 DOM 断言。注入探针 -> 真实 MouseEvent -> 结果写进 <pre id="probe"> -> --dump-dom 抓回。
不发起任何生成请求：所有断言只跑前端交互，生成链一律 mock。
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

DIR = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(DIR, "index.html")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

# 无头浏览器的 profile 和临时探针页一律建在系统临时目录里。
# 建在项目目录的话，每次跑测试都会在仓库里堆几百个 Edge 缓存文件（实测一次就 280+ 个、11MB）。
WORK = tempfile.mkdtemp(prefix="wan3_ui_")
TMP = os.path.join(WORK, "_probe.html")
PROFILE = os.path.join(WORK, "edge_profile")

# 必须插在主脚本之前，否则抓不到 boot() 抛出的错
ERROR_CATCH = r"""
<script>
window.__early = [];
window.addEventListener('error', function(e){
  window.__early.push((e.message || 'error') + ' @line ' + (e.lineno || '?') + ':' + (e.colno || '?') +
    ' | ' + ((e.error && e.error.stack) ? String(e.error.stack).split('\n').slice(0,2).join(' // ') : ''));
});
</script>
"""

PROBE = r"""
<pre id="probe" style="position:fixed;left:-99999px"></pre>
<script>
(function(){
  var R = [];
  (window.__early || []).forEach(function(m){ R.push('FAIL | 页面加载期错误 | ' + m); });
  function ok(name, cond, extra){
    R.push((cond ? 'PASS' : 'FAIL') + ' | ' + name + (extra ? ' | ' + extra : ''));
  }
  function flush(){ document.getElementById('probe').textContent = '@@' + R.join('\n') + '@@'; }
  function el(sel){ return document.querySelector(sel); }
  function all(sel){ return Array.prototype.slice.call(document.querySelectorAll(sel)); }
  function nodeEl(id){ return el('.node[data-id="'+id+'"]'); }
  function txt(sel){ var e = el(sel); return e ? e.textContent : ''; }
  function mouse(target, type, x, y){
    target.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, clientX:x||0, clientY:y||0, view:window}));
  }
  function click(target){ mouse(target, 'mousedown'); mouse(target, 'mouseup'); mouse(target, 'click'); }
  var W = window.__wf;

  try {
    if (!W){ R.push('FAIL | 探针句柄未挂载 | window.__wf 缺失，主脚本未执行到底'); }
    /* ---- 1. 左栏 + 添加面板 ---- */
    var rail = el('.rail');
    ok('左栏存在且竖排', !!rail && rail.children.length >= 4);
    ok('导入按钮在左栏', !!el('#impBtn') && rail.contains(el('#impBtn')));
    ok('顶栏无发布类按钮', all('.top button').map(function(b){return b.textContent;}).join('|').indexOf('发') < 0);
    ok('顶栏有导出按钮', !!el('#expBtn'));
    click(el('#addBtn'));
    var items = all('#addPanel .palItem');
    ok('添加面板列出四类块加本地上传', items.length === 5, 'count=' + items.length);
    var names = items.map(function(b){ return b.querySelector('b').textContent; }).join(',');
    ok('添加面板含预期五种入口', ['文本块','图片块','视频块','模版块','本地上传'].every(function(t){ return names.indexOf(t) >= 0; }), names);

    /* ---- 2. 加块 ---- */
    var before = W.S.nodes.length;
    click(items[1]);                       // 图片块
    click(items[2]);                       // 视频块
    ok('点击面板能新建块', W.S.nodes.length === before + 2, 'nodes=' + W.S.nodes.length);
    ok('四类块都能建出来', (function(){
      var t = ['text','image','video','template'].map(function(k){
        return W.addNode(k, 60, 60).type;
      });
      return t.indexOf('text') >= 0 && t.indexOf('image') >= 0 && t.indexOf('video') >= 0 && t.indexOf('template') >= 0;
    })());

    /* ---- 3. 角色推导 ---- */
    var img = W.S.nodes.filter(function(n){ return n.type==='image'; })[0];
    var vid = W.S.nodes.filter(function(n){ return n.type==='video' && !n.vid; })[0];
    var txtn = W.S.nodes.filter(function(n){ return n.type==='text'; })[0];
    ok('空图片块判为图像生成器', W.role(img) === 'imageGen');
    ok('空视频块判为视频生成器', W.role(vid) === 'videoGen');
    img.img = { name:'a.png', url:'/uploads/a.png', box:'/uploads/a.png' };
    W.render();
    ok('有图的图片块判为参考图源', W.role(img) === 'imageSrc');
    vid.vid = { name:'v.mp4', url:'/uploads/v.mp4', box:'/uploads/v.mp4' };
    W.render();
    ok('上传视频的视频块判为参考视频源', W.role(vid) === 'videoSrc');
    vid.vid = null; img.img = null; W.render();

    /* ---- 4. 文本实时镜像（用全新的一对块，避开启动时的演示连线） ---- */
    var mt2 = W.addNode('text', 40, 700);
    var mv2 = W.addNode('video', 400, 700);
    W.render();
    ok('重复连线会被拒绝', W.addEdge(mt2.id, mv2.id, 'in') !== null && W.addEdge(mt2.id, mv2.id, 'in') === null);
    var ed = W.S.edges.filter(function(e){ return e.from === mt2.id && e.to === mv2.id; })[0];
    mt2.text = '镜像是实时更新的';
    W.render();
    var vta = nodeEl(mv2.id).querySelector('textarea');
    ok('连上文本块后下游输入框进入镜像态', vta.readOnly === true && vta.classList.contains('mirror'));
    ok('镜像内容与上游一致', vta.value === '镜像是实时更新的', JSON.stringify(vta.value));
    // 直接改镜像框应被拒
    var setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(vta, '偷偷改的');
    vta.dispatchEvent(new Event('input', {bubbles:true}));
    ok('镜像态下输入框改不动', W.S.nodes.filter(function(n){return n.id===mv2.id;})[0].prompt === '');
    ok('镜像时下游不再显示可编辑态', nodeEl(mv2.id).querySelectorAll('[data-act="i2v"]').length === 0);
    // 上游改动应实时推到下游
    mt2.text = '改一改';
    W.softSync();
    ok('上游改动实时推到下游', nodeEl(mv2.id).querySelector('textarea').value === '改一改');

    /* ---- 5. 断开连线回退 ---- */
    W.S.edges = W.S.edges.filter(function(e){ return e.id !== ed.id; });
    W.render();
    var vta2 = nodeEl(mv2.id).querySelector('textarea');
    ok('断开后输入框恢复可编辑', vta2.readOnly === false);
    ok('断开后 placeholder 变回描述文案', vta2.placeholder === '描述你想要生成的视频内容', vta2.placeholder);
    ok('断开后重新出现上传与图生视频两行', nodeEl(mv2.id).querySelectorAll('[data-act="upVid"],[data-act="i2v"]').length === 2);

    /* ---- 6. 图生视频联动 ---- */
    // 启动演示块仍连着文本块（镜像态），不渲染选项行，这里改用刚断开连线的 mv2
    vid = mv2;
    var n0 = W.S.nodes.length;
    click(nodeEl(vid.id).querySelector('[data-act="i2v"]'));
    ok('点图生视频会自动弹出一个空图片块', W.S.nodes.length === n0 + 1, 'nodes=' + W.S.nodes.length);
    var slot = nodeEl(vid.id).querySelector('[data-fslot="first"]');
    var slotLast = nodeEl(vid.id).querySelector('[data-fslot="last"]');
    ok('视频块冒出首帧连接点', !!slot && !!slot.querySelector('.fdot[data-port="first"]'));
    ok('视频块冒出尾帧连接点', !!slotLast && !!slotLast.querySelector('.fdot[data-port="last"]'));
    ok('空图片块已自动接到首帧', !!W.edgeOf(vid.id, 'first'));
    var autoImg = W.S.nodes[W.S.nodes.length - 1];
    ok('自动生成的图片块接的是首帧位', W.edgeOf(vid.id, 'first').from === autoImg.id);

    /* ---- 7. 运行时模式自动降级 ---- */
    ok('只连首帧时降级为首帧生视频', W.resolveVideo(vid).mode === 'i2v', W.resolveVideo(vid).mode);
    ok('降级时有文字提示', W.resolveVideo(vid).note.length > 0, W.resolveVideo(vid).note);
    autoImg.img = { name:'f.png', url:'/uploads/f.png', box:'/uploads/f.png' };
    W.render();
    var img2 = W.addNode('image', 20, 20);
    img2.img = { name:'l.png', url:'/uploads/l.png', box:'/uploads/l.png' };
    W.render();
    W.addEdge(img2.id, vid.id, 'last');
    W.render();
    ok('首尾帧都连上后为首尾帧模式', W.resolveVideo(vid).mode === 'fl', W.resolveVideo(vid).mode);
    ok('首尾帧都连上后不再有降级提示', W.resolveVideo(vid).note === '');

    /* ---- 8. 首尾帧数量限制 ---- */
    var img3 = W.addNode('image', 20, 20);
    img3.img = { name:'x.png', url:'/uploads/x.png', box:'/uploads/x.png' };
    W.render();
    var bad = W.addEdge(img3.id, vid.id, 'last');
    ok('首帧位拒绝第二条连线', bad === null);
    var bad2 = W.addEdge(txtn.id, vid.id, 'first');
    ok('文本块不能接首帧位', bad2 === null);
    W.render();

    /* ---- 9. 悬浮工具条 ---- */
    mouse(nodeEl(vid.id).querySelector('.nHead'), 'mousemove', 200, 200);
    ok('悬停视频块浮出工具条', all('#hud .bar .ctl').length === 7, 'ctl=' + all('#hud .bar .ctl').length);
    var ctls = all('#hud .bar .ctl').map(function(b){ return b.dataset.ctl; }).join(',');
    ok('工具条七个控件齐全', ctls === 'model,mode,duration,res,ratio,audio,download', ctls);

    /* ---- 9b. 工具条几何：必须在块上方且左对齐，不许重叠 ---- */
    (function(){
      var gv = W.S.nodes.filter(function(n){ return n.type==='video' && !n.vid && !n.aud; })[0];
      gv.x = 500; gv.y = 400; W.S.sel = null; W.S.hover = null; W.render();
      mouse(nodeEl(gv.id).querySelector('.nHead'), 'mousemove', 10, 10);
      var bar = el('#hud .bar').getBoundingClientRect();
      var nd  = nodeEl(gv.id).getBoundingClientRect();
      var gapY = nd.top - bar.bottom;
      var offX = bar.left - nd.left;
      ok('工具条整体在块上方不重叠', gapY >= 0, 'gap=' + Math.round(gapY));
      ok('工具条与块左对齐', Math.abs(offX) <= 2, 'offsetX=' + Math.round(offX));
      ok('视图偏移已应用到世界层', Math.abs(nd.top - (el('#canvasWrap').getBoundingClientRect().top + gv.y * S.view.k + S.view.y)) < 2,
         'nodeTop=' + Math.round(nd.top));
    })();

    mouse(nodeEl(img.id), 'mousemove', 200, 200);
    ok('悬停图片块浮出工具条', all('#hud .bar .ctl').length === 5, 'ctl=' + all('#hud .bar .ctl').length);

    /* ---- 10. 时长滑杆 ---- */
    mouse(nodeEl(vid.id).querySelector('.nHead'), 'mousemove', 200, 200);
    click(el('#hud [data-ctl="duration"]'));
    var sl = el('#hud .pop input[type=range]');
    ok('时长展开为滑杆', !!sl);
    ok('滑杆范围两到三十秒', !!sl && sl.min === '2' && sl.max === '30', sl ? sl.min + '-' + sl.max : '');
    if (sl){ sl.value = '12'; sl.dispatchEvent(new Event('input', {bubbles:true})); }
    ok('拖滑杆实时改时长', W.S.nodes.filter(function(n){return n.id===vid.id;})[0].params.duration === 12);
    click(document.body);

    /* ---- 10.2 分辨率下拉只显示 P 值 ---- */
    mouse(nodeEl(vid.id).querySelector('.nHead'), 'mousemove', 200, 200);
    click(el('#hud [data-ctl="res"]'));
    var resTxt = all('#hud .pop .popItem').map(function(b){ return b.textContent.replace('✓','').trim(); }).join(',');
    ok('分辨率下拉只列 P 值', resTxt === '1080P,720P,480P', resTxt);
    var resChip = el('#hud [data-ctl="res"]').textContent.replace('▼','').trim();
    ok('工具条分辨率只显示 P 值', /^[0-9]+P$/.test(resChip), resChip);
    click(all('#hud .pop .popItem').filter(function(b){ return b.dataset.pick === '1080P'; })[0]);
    mouse(nodeEl(vid.id).querySelector('.nHead'), 'mousemove', 200, 200);
    ok('选中后工具条显示所选 P 值', el('#hud [data-ctl="res"]').textContent.replace('▼','').trim() === '1080P');
    click(document.body);

    /* ---- 11. 比例下拉为勾选框样式且单选 ---- */
    mouse(nodeEl(vid.id).querySelector('.nHead'), 'mousemove', 200, 200);
    click(el('#hud [data-ctl="ratio"]'));
    var checks = all('#hud .pop .popItem').map(function(b){
      return (b.querySelector('.ck').textContent === '✓' ? '1' : '0');
    }).join('');
    ok('比例下拉为勾选框样式', checks.indexOf('1') >= 0 && checks.length === W.S.cfg.ratios.length, 'flags=' + checks);
    var pick = all('#hud .pop .popItem').filter(function(b){
      return b.querySelector('.ck').textContent !== '✓';
    })[0];
    var want = pick.dataset.pick;
    click(pick);
    ok('比例单选生效', W.S.nodes.filter(function(n){return n.id===vid.id;})[0].params.ratio === want, want);
    // 选中后弹层关闭，重新展开应只有一项打勾
    mouse(nodeEl(vid.id).querySelector('.nHead'), 'mousemove', 200, 200);
    click(el('#hud [data-ctl="ratio"]'));
    var onCount = all('#hud .pop .popItem').filter(function(b){
      return b.querySelector('.ck').textContent === '✓';
    }).length;
    ok('比例单选，重新展开后只有一项打勾', onCount === 1, 'on=' + onCount);
    click(document.body);

    /* ---- 12. 模式切换影响块内 UI ---- */
    mouse(nodeEl(vid.id).querySelector('.nHead'), 'mousemove', 200, 200);
    click(el('#hud [data-ctl="mode"]'));
    click(el('#hud .pop [data-pick="omni"]'));
    ok('切多参考后开放的口袋只剩输入', W.nodePorts(W.S.nodes.filter(function(n){return n.id===vid.id;})[0]).join(',') === 'in');
    mouse(nodeEl(vid.id).querySelector('.nHead'), 'mousemove', 200, 200);
    click(el('#hud [data-ctl="mode"]'));
    click(el('#hud .pop [data-pick="fl"]'));
    ok('切回首尾帧重新冒出两个帧位连接点', W.nodePorts(W.S.nodes.filter(function(n){return n.id===vid.id;})[0]).length === 3);

    /* ---- 13. 多参考 @芯片（用没有帧位连线的新视频块，否则会被首尾帧模式抢先） ---- */
    var vi = W.addNode('video', 900, 300);
    vi.params.mode = 'omni'; vi.showFrames = false;
    var refImg = W.S.nodes.filter(function(n){ return n.type==='image'; })[1];
    refImg.img = { name:'r.png', url:'/uploads/r.png', box:'/uploads/r.png' };
    var refImg2 = W.addNode('image', 700, 300);
    refImg2.img = { name:'r2.png', url:'/uploads/r2.png', box:'/uploads/r2.png' };
    W.addEdge(refImg.id, vi.id, 'in');
    W.addEdge(refImg2.id, vi.id, 'in');
    var vidNode = W.addNode('video', 700, 400);
    vidNode.vid = { name:'rv.mp4', url:'/uploads/rv.mp4', box:'/uploads/rv.mp4' };
    W.addEdge(vidNode.id, vi.id, 'in');
    var audNode = W.addNode('video', 700, 500);
    audNode.aud = { name:'a.wav', url:'/uploads/a.wav', box:'/uploads/a.wav' };
    W.addEdge(audNode.id, vi.id, 'in');
    W.render();
    var chips = Array.prototype.map.call(nodeEl(vi.id).querySelectorAll('.chip'), function(c){ return c.textContent; }).join(',');
    ok('多参考模式显示参考芯片', chips.indexOf('@图片') >= 0 && chips.indexOf('@视频') >= 0 && chips.indexOf('@音频') >= 0, chips);
    ok('多参考模式解析为 omni', W.resolveVideo(vi).mode === 'omni', W.resolveVideo(vi).mode);
    ok('上游素材按类型分流', W.upstreamMedia(vi).map(function(m){return m.kind;}).sort().join(',') === 'audio,image,image,video');

    /* ---- 14. 右键菜单 ---- */
    mouse(nodeEl(refImg.id).querySelector('.nHead'), 'contextmenu', 300, 300);
    var cmds = all('#ctx .ctxItem').map(function(b){ return b.dataset.cmd; }).join(',');
    ok('右键块弹出四项菜单', cmds === 'copy,dup,rename,del', cmds);
    ok('菜单定位在鼠标处', parseInt(el('#ctx').style.left, 10) === 300);
    click(document.body);
    ok('点击外部关闭右键菜单', !el('#ctx').classList.contains('show'));

    /* ---- 15. 菜单四个动作 ---- */
    var nImg = refImg.id, nCount = W.S.nodes.length;
    mouse(nodeEl(nImg).querySelector('.nHead'), 'contextmenu', 300, 300);
    click(el('#ctx [data-cmd="dup"]'));
    ok('创建副本生成独立新块', W.S.nodes.length === nCount + 1);
    ok('副本内容一致但 id 不同', W.S.nodes[W.S.nodes.length-1].img && W.S.nodes[W.S.nodes.length-1].id !== nImg);
    ok('副本不继承连线', W.S.edges.every(function(e){ return e.from !== W.S.nodes[W.S.nodes.length-1].id; }));

    W.S.sel = nImg;
    mouse(nodeEl(nImg).querySelector('.nHead'), 'contextmenu', 300, 300);
    click(el('#ctx [data-cmd="copy"]'));
    ok('复制写入剪贴板', !!W.S.clipboard && W.S.clipboard.id === nImg);

    W.S.sel = nImg;
    mouse(nodeEl(nImg).querySelector('.nHead'), 'contextmenu', 300, 300);
    click(el('#ctx [data-cmd="rename"]'));
    var ri = el('[data-rename]');
    ok('重命名进入原地编辑', !!ri);
    if (ri){
      ri.value = '首帧图';
      ri.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}));
    }
    ok('重命名回车后写入块名', W.S.nodes.filter(function(n){return n.id===nImg;})[0].name === '首帧图');

    var beforeDel = W.S.nodes.length, edgesBefore = W.S.edges.length;
    W.S.sel = nImg;
    mouse(nodeEl(nImg).querySelector('.nHead'), 'contextmenu', 300, 300);
    click(el('#ctx [data-cmd="del"]'));
    ok('删除块生效', W.S.nodes.length === beforeDel - 1);
    ok('删除连带清掉相关连线', W.S.edges.length < edgesBefore && W.S.edges.every(function(e){ return e.from !== nImg && e.to !== nImg; }));

    /* ---- 16. 连线三种删除方式 ---- */
    var a = W.addNode('text', 40, 40), b = W.addNode('video', 400, 40);
    var e1 = W.addEdge(a.id, b.id, 'in');
    ok('连线建立成功', !!e1);
    W.S.selEdge = e1.id; W.render();
    ok('选中连线出现中点删除按钮', !!el('.edgeDel'));
    document.dispatchEvent(new KeyboardEvent('keydown', {key:'Delete', bubbles:true}));
    ok('Delete 键能删连线', W.S.edges.indexOf(e1) < 0);
    var e2 = W.addEdge(a.id, b.id, 'in'); W.render();
    mouse(el('.edgeHit[data-edge="'+e2.id+'"]'), 'mousedown', 500, 300);
    ok('点连线可选中', W.S.selEdge === e2.id);

    /* 中点叉号：必须断言「mousedown 之后元素还在文档里」。
       真实浏览器只在 mousedown 和 mouseup 落在同一个仍被连接的元素上时才派发 click；
       元素一旦脱离文档，click 就不在它身上触发 —— 而 dispatchEvent 对游离元素仍然会触发，
       所以只断言「删掉了」是测不出这个 bug 的，必须额外查 isConnected。 */
    var del = el('.edgeDel');
    ok('中点叉号存在', !!del);
    mouse(del, 'mousedown', 500, 300);
    ok('叉号 mousedown 后仍连接在文档里', !!del && del.isConnected, 'connected=' + (del && del.isConnected));
    ok('mousedown 不会把连线取消选中', W.S.selEdge === e2.id, W.S.selEdge);
    ok('mousedown 不会顺手开起平移拖拽', !W.S.drag, W.S.drag && W.S.drag.type);
    mouse(del, 'mouseup', 500, 300);
    ok('叉号 mouseup 后仍在文档里', !!del && del.isConnected);
    mouse(del, 'click', 500, 300);
    ok('点中点叉能删连线', W.S.edges.indexOf(e2) < 0, 'edges=' + W.S.edges.length);
    ok('删完叉号也消失了', !el('.edgeDel'));

    /* ---- 16b. 块内所有可交互元素：mousedown 绝不能把它重建掉 ----
       只要 mousedown 触发 render()，元素就会脱离文档，后续 click 冒泡不到委托监听器，
       表现就是「点了没反应」。这里逐个点过去，断言元素始终存活。 */
    (function(){
      var ib = W.addNode('image', 700, 700);
      // 视频块一旦有节点连入就是「汇」，上传/图生视频两个按钮会收起来（见第 25 节）。
      // 要测这两个按钮还在不在，得拿一个孤立的视频块。
      var soloV = W.addNode('video', 700, 1000);
      W.render();
      var targets = [
        ['文本块输入框', function(){ return document.querySelector('.node[data-id="'+mt2.id+'"] textarea'); }],
        ['图片块输入框', function(){ return document.querySelector('.node[data-id="'+ib.id+'"] textarea'); }],
        ['图片块方式芯片', function(){ return document.querySelector('.node[data-id="'+ib.id+'"] .way'); }],
        ['图片块生成按钮', function(){ return document.querySelector('.node[data-id="'+ib.id+'"] .genBtn'); }],
        ['视频块图生视频按钮', function(){ return document.querySelector('.node[data-id="'+soloV.id+'"] [data-act="i2v"]'); }],
        ['视频块帧位连接点', function(){ return document.querySelector('.node[data-id="'+mv2.id+'"] .fdot'); }],
        ['帧位槽本体', function(){ return document.querySelector('.node[data-id="'+mv2.id+'"] .fslot'); }],
      ];
      targets.forEach(function(pair){
        var e = pair[1]();
        if (!e){ ok(pair[0] + ' 存在', false); return; }
        e.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,clientX:0,clientY:0}));
        ok(pair[0] + ' mousedown 后未脱离文档', document.contains(e));
      });
    })();

    /* ---- 17. 输入框内 Delete 不误删 ---- */
    var keep = W.S.nodes.length;
    var tnode = W.addNode('text', 40, 300);
    var tta = nodeEl(tnode.id).querySelector('textarea');
    tta.focus();
    tta.dispatchEvent(new KeyboardEvent('keydown', {key:'Delete', bubbles:true}));
    ok('文本框内按 Delete 不会删块', W.S.nodes.length === keep + 1 && !!nodeEl(tnode.id));

    /* ---- 18. 布局不塌陷 ---- */
    click(el('#canvasWrap').querySelector('.world') || el('#canvasWrap'));
    mouse(el('#canvasWrap'), 'mousedown', 700, 500);
    mouse(el('#canvasWrap'), 'mouseup', 700, 500);
    W.S.sel = null; W.render();
    var cw = el('#canvasWrap').getBoundingClientRect();
    // 无头窗口视口高度不等于 --window-size，按视口比例判断：塌陷版只占约 40%
    ok('取消选中后画布仍铺满不塌陷', cw.height > window.innerHeight * 0.65,
       'canvas=' + Math.round(cw.height) + ' viewport=' + window.innerHeight);
    ok('画布底部紧贴日志区无黑洞', Math.abs(cw.bottom - el('#log').getBoundingClientRect().top) < 2);
    ok('页面无横向溢出', document.documentElement.scrollWidth <= window.innerWidth + 2);

    /* ---- 19. 费用随参数变化 ---- */
    var vx = W.S.nodes.filter(function(n){return n.type==='video' && !n.vid && !n.aud;})[0];
    var c0 = W.costText(vx);
    vx.params.resolution = '480P'; vx.params.duration = 2;
    W.render();
    var c1 = W.costText(vx);
    ok('费用随分辨率与时长实时变化', c0 !== c1, c0 + ' -> ' + c1);
    var ix = W.S.nodes.filter(function(n){ return n.type==='image'; }).filter(function(n){ return !n.img; })[0];
    ix.imgParams.size = '1536x1024';
    W.render();
    ok('图片块费用随尺寸变化', W.costText(ix) === '¥0.60', W.costText(ix));

    /* ---- 20. 图片块四种生图方式 ---- */
    var iw = W.S.nodes.filter(function(n){ return n.type==='image' && !n.img; })[0];
    var ways = Array.prototype.map.call(nodeEl(iw.id).querySelectorAll('.way'), function(b){ return b.dataset.way; }).join(',');
    ok('图片块提供四种生图方式加自动', ways === 'auto,t2i,i2i,ref,stylize', ways);
    ok('默认按上游自动推导', W.S.nodes.filter(function(n){return n.id===iw.id;})[0].imgParams.way === 'auto');
    click(nodeEl(iw.id).querySelector('[data-way="stylize"]'));
    ok('风格化转绘可被选中', W.S.nodes.filter(function(n){return n.id===iw.id;})[0].imgParams.way === 'stylize');
    click(nodeEl(iw.id).querySelector('[data-way="auto"]'));
    ok('可退回自动推导', W.S.nodes.filter(function(n){return n.id===iw.id;})[0].imgParams.way === 'auto');
    ok('无上游时判定为文生图', W.imgWay(iw) === 't2i', W.imgWay(iw));
    var up1 = W.addNode('image', 20, 500); up1.img = {name:'p1.png', url:'/uploads/p1.png', box:'/uploads/p1.png'};
    W.render();
    var up2 = W.addNode('image', 20, 600); up2.img = {name:'p2.png', url:'/uploads/p2.png', box:'/uploads/p2.png'};
    W.render();
    W.addEdge(up1.id, iw.id, 'in');
    ok('单张上游判为图生图', W.imgWay(iw) === 'i2i', W.imgWay(iw));
    W.addEdge(up2.id, iw.id, 'in');
    ok('多张上游判为参考图生图', W.imgWay(iw) === 'ref', W.imgWay(iw));

    /* ---- 20b. 素材源块里只剩素材本体（图片块与视频块都适用） ---- */
    (function(){
      var mk = function(type, slot){
        var n = W.addNode(type, 900, 300);
        n[slot] = { name:'素材.mp4', url:'/uploads/demo.mp4', box:'/uploads/demo.mp4' };
        W.render();
        return document.querySelector('.node[data-id="'+n.id+'"]');
      };
      var bi = mk('image','img'), bv = mk('video','vid');
      ok('图片源块无按钮无文字', bi.querySelectorAll('button, .mini, .mediaBar, .mediaName, .hint0').length === 0);
      ok('视频源块无按钮无文字', bv.querySelectorAll('button, .mini, .mediaBar, .mediaName, .hint0').length === 0);
      ok('视频源块块内只有播放器', bv.querySelectorAll('.nBody video').length === 1);
      ok('图片源块块内只有图片', bi.querySelectorAll('.nBody img.mediaFull').length === 1);
      // 悬停仍能拿到替换/下载
      var h = bi.querySelector('.nHead');
      h.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:0,clientY:0}));
      var c1 = Array.prototype.map.call(document.querySelectorAll('#hud .bar .ctl'), function(b){ return b.dataset.ctl; }).join(',');
      ok('图片源块工具条仍是替换下载', c1 === 'swap,download', c1);
      bv.querySelector('.nHead').dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:0,clientY:0}));
      var c2 = Array.prototype.map.call(document.querySelectorAll('#hud .bar .ctl'), function(b){ return b.dataset.ctl; }).join(',');
      ok('视频源块工具条仍是替换下载', c2 === 'swap,download', c2);
    })();

    /* ---- 21. 拓扑顺序 ---- */
    var order = W.topo().map(function(n){ return n.id; });
    var pos = {}; order.forEach(function(id, i){ pos[id] = i; });
    ok('拓扑序里上游一定排在下游前面', W.S.edges.every(function(e){
      return pos[e.from] < pos[e.to];
    }));
    ok('拓扑序挑出的生成块非空', W.topo().filter(W.isGen).length > 0);

    /* ---- 22. 拖动置顶：拖谁谁在最上层 ----
       节点之间没有 z-index，叠放完全由 #world 里的 DOM 先后决定，
       所以「置顶」既要让 DOM 排到末尾，也要把 z 写进状态，否则下一次 render() 会打回原形。 */
    (function(){
      var za = W.addNode('video', 300, 600);
      var zb = W.addNode('video', 300, 600);      // 后建的 z 更大，本来就在上面
      W.render();
      var domOrder = function(){ return all('#world .node').map(function(e){ return e.dataset.id; }); };
      ok('后建的块默认压在先建的上面', domOrder().indexOf(zb.id) > domOrder().indexOf(za.id), domOrder().join(','));

      // 拖 zb：zb 仍应保持在最上层
      mouse(nodeEl(zb.id).querySelector('.nHead'), 'mousedown', 100, 100);
      ok('按下即置顶，拖动的块排到 DOM 末尾', domOrder()[domOrder().length - 1] === zb.id, domOrder().join(','));
      mouse(el('#canvasWrap'), 'mouseup', 120, 120);

      // 拖 za（本来在下面那个）：应该被提到最上层
      mouse(nodeEl(za.id).querySelector('.nHead'), 'mousedown', 100, 100);
      ok('拖下面的块会把它提到最上层', domOrder()[domOrder().length - 1] === za.id, domOrder().join(','));
      ok('置顶写进了状态 z', nodeByIdZ(za.id) > nodeByIdZ(zb.id), za.z + ' vs ' + zb.z);
      mouse(el('#canvasWrap'), 'mouseup', 120, 120);

      // 关键：重绘之后叠放顺序必须还在（否则 render() 一来就打回原形）
      W.render();
      ok('重绘后叠放顺序保持不变', domOrder()[domOrder().length - 1] === za.id, domOrder().join(','));
      function nodeByIdZ(id){ var n = W.S.nodes.filter(function(n){return n.id===id;})[0]; return n ? n.z : -1; }

      // 置顶靠搬同一个元素实例完成，不能把播放中的媒体重建掉
      var zEl = nodeEl(za.id);
      zEl.setAttribute('data-sameinst', '1');
      W.bringToFront(za.id);
      ok('置顶搬的是同一个元素实例', nodeEl(za.id).getAttribute('data-sameinst') === '1');

      // 副本与粘贴也该压在最上层
      var dup = W.duplicateNode(zb.id);
      var newTop = all('#world .node').pop();
      ok('副本创建后压在最上层', newTop && newTop.dataset.id === dup.id && dup.z > za.z, dup.z + ' vs ' + za.z);
      W.delNode(dup.id);
      W.delNode(za.id); W.delNode(zb.id);
    })();

    /* ---- 24. 保存并覆盖当前 JSON ----
       浏览器不会暴露用户选的文件路径，所以「覆盖同一个文件」只能靠
       File System Access API 的 FileSystemFileHandle。这里不发真实写入，
       只验证句柄绑定、按钮状态与空画布保护。 */
    ok('顶栏有保存按钮', !!el('#saveBtn'));
    ok('导入按钮与保存按钮同时存在', !!el('#impBtn') && !!el('#saveBtn') && !!el('#expBtn'));
    (function(){
      // 干净起点：假装从没保存过
      W.S.fileHandle = null; W.S.fileName = ''; W.S.fileNodeCount = 0;
      W.renderTop();
      ok('没绑定文件时保存按钮不点亮', !el('#saveBtn').classList.contains('on'));
      ok('没绑定文件时提示为另存为', el('#saveBtn').title.indexOf('另存为') >= 0, el('#saveBtn').title);

      // 载入一份工作流
      W.loadWfText(JSON.stringify({ name:'保存测试', nodes:[
        { type:'text', x:10, y:10, text:'甲' },
        { type:'video', x:300, y:10, prompt:'乙' }
      ], edges:[] }), null, '');
      ok('载入后有两个块', W.S.nodes.length === 2, 'n=' + W.S.nodes.length);

      // 绑定一个假句柄，等价于「导入时拿到了 handle」
      var fake = { name:'my-wf.json', __fake:true };
      W.setCurrentFile(fake, 'my-wf.json');
      ok('绑定后保存按钮点亮', el('#saveBtn').classList.contains('on'));
      ok('绑定后提示写明要覆盖哪个文件',
        el('#saveBtn').title === '保存并覆盖 my-wf.json', el('#saveBtn').title);
      ok('状态记住了文件名', W.S.fileName === 'my-wf.json', W.S.fileName);

      // 导出内容应当是完整工作流（别把 JSON 原文塞进 extra，会把结果行撑成多行）
      var parsed = JSON.parse(W.wfText());
      ok('序列化内容含全部块', parsed.nodes.length === 2 && parsed.name === '保存测试',
        'nodes=' + parsed.nodes.length + ' name=' + parsed.name);
      ok('序列化内容含边数组与版本号', Array.isArray(parsed.edges) && parsed.version === 2);

      // 空画布保护：把块删光，fileNodeCount 仍是 2，保存必须先确认
      W.S.nodes.length = 0;
      ok('空画布时 fileNodeCount 仍记住原来有内容', W.S.fileNodeCount === 2, 'c=' + W.S.fileNodeCount);
      var asked = false;
      var realConfirm = window.confirm;
      window.confirm = function(){ asked = true; return false; };   // 模拟用户点「取消」
      W.S.sel = null;
      // 走保存的守卫分支：这里只验证 confirm 被调用过（fake 句柄 createWritable 不可用，
      // 所以真正落盘会失败，但守卫必须先拦住）
      var p = W.saveWf();
      ok('空画布覆盖会先弹确认', asked);
      window.confirm = realConfirm;
    })();

    /* ---- 23. 导入工作流：块数不能被模板节点翻倍 ----
       曾经拿 newNode() 当字段模板，它会把模板块本身 push 进 S.nodes，
       而 forEach 只遍历原始长度 —— 导入一次，块数直接翻一倍。 */
    (function(){
      var n0 = W.S.nodes.length;
      W.loadWfText(JSON.stringify({ name:'导入测试', edges:[], nodes:[
        { type:'text',  x:10,  y:10 },
        { type:'video', x:300, y:10 },
        { type:'image', x:600, y:10 }
      ]}), null, '');
      ok('导入 3 个块后节点数就是 3', W.S.nodes.length === 3, 'before=' + n0 + ' after=' + W.S.nodes.length);
      ok('导入后每块都有独立且不重复的 z',
        new Set(W.S.nodes.map(function(x){return x.z;})).size === 3,
        W.S.nodes.map(function(x){return x.z;}).join(','));
      ok('导入后 S.zTop 顶到最高，新块不会盖过导入的块', W.S.zTop === 3, 'zTop=' + W.S.zTop);
      ok('导入的项目名被采用', W.S.proj === '导入测试', W.S.proj);
      // 没有句柄时不应假装绑定了文件
      ok('没走选择器的导入不会点亮保存按钮', !el('#saveBtn').classList.contains('on'));
      // 多个块的参数对象必须是各自独立的，改一个不影响另一个
      W.loadWfText(JSON.stringify({ name:'共享检查', edges:[], nodes:[
        { type:'video', x:0, y:0 }, { type:'video', x:0, y:0 }
      ]}), null, '');
      W.S.nodes[0].params.duration = 99;
      ok('两个视频块的参数对象不共用', W.S.nodes[1].params.duration !== 99, W.S.nodes[1].params.duration);
    })();

    /* ---- 25. 源—汇：视频块一旦被连接，就不再是起点，两个按钮收起 ----
       原站行为：孤立视频块显示「视频上传 / 图生视频」；只要有任何节点连进它，
       它就处在「源 → 汇」的汇这一端，两个入口收起来。 */
    (function(){
      var cnt = function(id){
        var e = nodeEl(id);
        return e ? e.querySelectorAll('[data-act="upVid"],[data-act="i2v"]').length : -1;
      };
      W.S.nodes.length = 0; W.S.edges.length = 0; W.S.sel = null; W.S.selEdge = null;

      // a) 孤立
      var v = W.addNode('video', 0, 0);
      W.render();
      ok('孤立视频块有两个按钮', cnt(v.id) === 2, 'n=' + cnt(v.id));

      // b) 文本连入（镜像态）
      var t = W.addNode('text', -400, 0);
      t.text = '上游文本';
      W.addEdge(t.id, v.id, 'in');
      W.render();
      ok('文本连入后两个按钮消失', cnt(v.id) === 0, 'n=' + cnt(v.id));

      // c) 换成图片连入（非镜像态）—— 这条以前是漏的
      W.S.edges.length = 0;
      var i = W.addNode('image', -400, 300);
      i.img = { name:'x.png', url:'/uploads/x.png', box:'/uploads/x.png' };
      W.addEdge(i.id, v.id, 'in');
      W.render();
      ok('图片连入后两个按钮也消失', cnt(v.id) === 0, 'n=' + cnt(v.id));
      ok('图片连入时输入框仍可编辑', nodeEl(v.id).querySelector('textarea').readOnly === false);

      // d) 连到首帧位同样收起
      W.S.edges.length = 0;
      var i2 = W.addNode('image', -400, 600);
      i2.img = { name:'y.png', url:'/uploads/y.png', box:'/uploads/y.png' };
      W.addEdge(i2.id, v.id, 'first');
      W.render();
      ok('连到首帧位后两个按钮也消失', cnt(v.id) === 0, 'n=' + cnt(v.id));

      // e) 断掉连接，两个按钮回来
      W.S.edges.length = 0;
      W.render();
      ok('断开后两个按钮重新出现', cnt(v.id) === 2, 'n=' + cnt(v.id));

      // f) 只有出边（它是源）不算汇，按钮应当保留
      W.S.edges.length = 0;
      var v2 = W.addNode('video', 0, 300);
      var down = W.addNode('image', 400, 300);
      W.addEdge(v2.id, down.id, 'in');
      W.render();
      ok('只有出边时两个按钮保留（它仍是源）', cnt(v2.id) === 2, 'n=' + cnt(v2.id));

      // g) 素材源块本来就没有这两个按钮
      var vs = W.addNode('video', 0, 600);
      vs.vid = { name:'v.mp4', url:'/uploads/v.mp4', box:'/uploads/v.mp4' };
      W.render();
      ok('视频源块没有这两个按钮', cnt(vs.id) === 0, 'n=' + cnt(vs.id));
    })();

  } catch (err){
    R.push('FAIL | 探针抛异常 | ' + (err && err.message ? err.message : String(err)) + ' @ ' + (err && err.stack ? String(err.stack).split('\n')[1] : ''));
    flush();
  }
  flush();
})();
</script>
"""


def main():
    try:
        return run()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)   # 临时目录用完即弃，项目目录保持干净


def run():
    html = io.open(SRC, encoding="utf-8").read()
    html = html.replace("<body>", "<body>\n" + ERROR_CATCH, 1)
    html = html.replace("</body>", PROBE + "\n</body>")
    io.open(TMP, "w", encoding="utf-8").write(html)

    url = "file:///" + urllib.parse.quote(TMP.replace("\\", "/").lstrip("/"))
    cmd = [
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--window-size=1600,1000", "--virtual-time-budget=8000",
        "--user-data-dir=" + PROFILE,
        "--dump-dom", url,
    ]
    p = subprocess.run(cmd, capture_output=True, timeout=180)
    raw = p.stdout or b""
    try:
        out = raw.decode("utf-8")
    except UnicodeDecodeError:
        out = raw.decode("gbk", "replace")
    m = re.search(r"@@(.*?)@@", out, re.S)
    if not m:
        msg = "!! 没抓到探针输出。stderr 前 800 字：\n" + (p.stderr or b"")[:800].decode("utf-8", "replace")
        io.open(os.path.join(DIR, "test_ui_out.txt"), "w", encoding="utf-8").write(msg)
        print("no-probe-output (see test_ui_out.txt)")
        sys.exit(2)
    lines = [l for l in m.group(1).split("\n") if l.strip()]
    npass = sum(1 for l in lines if l.startswith("PASS"))
    body = "\n".join(lines) + "\n\n===== %d/%d passed =====\n" % (npass, len(lines))
    io.open(os.path.join(DIR, "test_ui_out.txt"), "w", encoding="utf-8").write(body)
    print("wrote test_ui_out.txt : %d/%d passed" % (npass, len(lines)))
    sys.exit(0 if npass == len(lines) else 1)


if __name__ == "__main__":
    main()
