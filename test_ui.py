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
    // 先取消选中：选中块的胶囊是钉住的，会与悬停这条并存（见第 28 节）
    W.S.sel = null;
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
        ['图片块上传按钮', function(){ return document.querySelector('.node[data-id="'+ib.id+'"] [data-act="upImg"]'); }],
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
    // 3.0-pro 的 2K 是 ¥0.50/张（不是旧表里的 ¥0.60）
    ok('图片块费用随尺寸变化', W.costText(ix) === '¥0.50', W.costText(ix));

    /* ---- 20. 图片块只保留「上传 / 参考图生图 / 风格化转绘」三个入口 ----
       自动（反映不出实际状态）、文生图（直接打字就行）、图生图（与参考图生图同义）都已删。 */
    var iw = W.S.nodes.filter(function(n){ return n.type==='image' && !n.img; })[0];
    // 这批断言要用孤立图片块；有连线时按钮按原站规则全部收起（见第 26 节）
    W.S.edges = W.S.edges.filter(function(e){ return e.from !== iw.id && e.to !== iw.id; });
    W.render();
    var ways = Array.prototype.map.call(nodeEl(iw.id).querySelectorAll('[data-act="upImg"],[data-way]'), function(b){
      return b.dataset.act || b.dataset.way;
    }).join(',');
    ok('图片块只留上传、参考图生图、风格化转绘三个入口', ways === 'upImg,ref,stylize', ways);
    ok('没有自动/文生图/图生图按钮',
      !nodeEl(iw.id).querySelector('[data-way="auto"]') &&
      !nodeEl(iw.id).querySelector('[data-way="t2i"]') &&
      !nodeEl(iw.id).querySelector('[data-way="i2i"]'));
    ok('默认仍按上游自动推导（默认值没变，只是不再显示该按钮）',
      W.S.nodes.filter(function(n){return n.id===iw.id;})[0].imgParams.way === 'auto');
    click(nodeEl(iw.id).querySelector('[data-way="stylize"]'));
    ok('风格化转绘可被选中', W.S.nodes.filter(function(n){return n.id===iw.id;})[0].imgParams.way === 'stylize');
    ok('选中后按钮点亮', nodeEl(iw.id).querySelector('[data-way="stylize"]').classList.contains('on'));
    click(nodeEl(iw.id).querySelector('[data-way="ref"]'));
    ok('可切到参考图生图', W.S.nodes.filter(function(n){return n.id===iw.id;})[0].imgParams.way === 'ref');
    ok('切到参考图生图后按钮点亮', nodeEl(iw.id).querySelector('[data-way="ref"]').classList.contains('on'));
    // 下面几条验的是「默认按上游自动推导」，先把 way 拨回 auto
    W.S.nodes.filter(function(n){return n.id===iw.id;})[0].imgParams.way = 'auto';
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

      // f) 只有出边也算连上了 —— 原站实测：视频块往下游接出去时按钮照样收起
      W.S.edges.length = 0;
      var v2 = W.addNode('video', 0, 300);
      var down = W.addNode('image', 400, 300);
      W.addEdge(v2.id, down.id, 'in');
      W.render();
      ok('只有出边时两个按钮也消失', cnt(v2.id) === 0, 'n=' + cnt(v2.id));
      ok('此时它仍被判为生成器（只是不再显示入口）', W.role(v2) === 'videoGen', W.role(v2));

      // g) 素材源块本来就没有这两个按钮
      var vs = W.addNode('video', 0, 600);
      vs.vid = { name:'v.mp4', url:'/uploads/v.mp4', box:'/uploads/v.mp4' };
      W.render();
      ok('视频源块没有这两个按钮', cnt(vs.id) === 0, 'n=' + cnt(vs.id));
    })();

    /* ---- 26. 图片块连上线后入口全收起；文本块不挂工具条 ---- */
    (function(){
      var btns = function(id){
        var e = nodeEl(id);
        return e ? e.querySelectorAll('[data-act="upImg"],[data-way]').length : -1;
      };
      W.S.nodes.length = 0; W.S.edges.length = 0; W.S.sel = null; W.S.hover = null; W.selEdge = null;

      // a) 孤立图片块：三个入口都在
      var i = W.addNode('image', 0, 0);
      W.render();
      ok('孤立图片块有三个入口', btns(i.id) === 3, 'n=' + btns(i.id));

      // b) 图片 -> 视频（它是源，原站照样收起）
      var v = W.addNode('video', 500, 0);
      W.addEdge(i.id, v.id, 'in');
      W.render();
      ok('图片连到视频后入口全部消失', btns(i.id) === 0, 'n=' + btns(i.id));
      ok('此时图片块仍是生成器', W.role(i) === 'imageGen', W.role(i));

      // c) 文本 -> 图片
      W.S.edges.length = 0;
      var t = W.addNode('text', -500, 300);
      t.text = '上游文本';
      W.addEdge(t.id, i.id, 'in');
      W.render();
      ok('文本连到图片后入口全部消失', btns(i.id) === 0, 'n=' + btns(i.id));

      // d) 图片 -> 图片
      W.S.edges.length = 0;
      var j = W.addNode('image', 500, 600);
      W.addEdge(i.id, j.id, 'in');
      W.render();
      ok('图片连到图片后两端入口都消失', btns(i.id) === 0 && btns(j.id) === 0,
         'src=' + btns(i.id) + ' dst=' + btns(j.id));

      // e) 断线即恢复
      W.S.edges.length = 0;
      W.render();
      ok('断开后入口重新出现', btns(i.id) === 3, 'n=' + btns(i.id));

      // f) 素材源块本来就没有这些入口
      var isrc = W.addNode('image', 0, 900);
      isrc.img = { name:'a.png', url:'/uploads/a.png', box:'/uploads/a.png' };
      W.render();
      ok('图片源块没有这三个入口', btns(isrc.id) === 0, 'n=' + btns(isrc.id));
    })();

    /* ---- 27. 文本块与模版块不挂悬浮工具条 ---- */
    (function(){
      var bare = function(id){
        W.S.hover = id; W.S.sel = null;
        W.renderHud();
        return document.querySelectorAll('#hud .bar .ctl').length;
      };
      W.S.nodes.length = 0; W.S.edges.length = 0; W.S.sel = null; W.S.hover = null;
      var t = W.addNode('text', 0, 0);
      var img = W.addNode('image', 400, 0);
      var v = W.addNode('video', 800, 0);
      var tp = W.addNode('template', 0, 400);
      W.render();
      ok('文本块悬浮时不出现工具条', bare(t.id) === 0, 'ctl=' + bare(t.id));
      ok('模版块悬浮时不出现工具条', bare(tp.id) === 0, 'ctl=' + bare(tp.id));
      ok('图片块悬浮时仍有两个工具条控件', bare(img.id) === 5, 'ctl=' + bare(img.id));
      ok('视频块悬浮时仍有七个工具条控件', bare(v.id) === 7, 'ctl=' + bare(v.id));
      W.S.hover = null; W.renderHud();
    })();

    /* ---- 28. 胶囊钉住：选中后不随指针移开而消失，悬停别的块可叠加出现 ---- */
    (function(){
      var bars = function(){ return all('#hud .bar'); };
      // 工具条锚在所属块的正上方，按横坐标反查它属于哪个块
      var screenX = function(n){ return W.S.view.x + n.x * W.S.view.k; };
      var ownerOf = function(bar){
        var bx = parseFloat(bar.style.left), best = null, bd = 1e9;
        W.S.nodes.forEach(function(n){
          var d = Math.abs(screenX(n) - bx);
          if (d < bd){ bd = d; best = n; }
        });
        return best;
      };

      W.S.nodes.length = 0; W.S.edges.length = 0; W.S.sel = null; W.S.hover = null; W.S.pop = null;
      var v = W.addNode('video', 60, 300);
      var i = W.addNode('image', 700, 60);
      W.render();

      // a) 只选中：一条胶囊
      W.S.sel = v.id; W.S.hover = null; W.renderHud();
      ok('选中后出现一条胶囊', bars().length === 1, 'bars=' + bars().length);
      ok('这条胶囊属于选中的视频块', ownerOf(bars()[0]) === v, (ownerOf(bars()[0]) || {}).id + ' vs ' + v.id);

      // b) 指针移开（悬停清空）：钉住的那条还在
      W.S.hover = null; W.renderHud();
      ok('指针移开后选中块的胶囊仍在', bars().length === 1 && ownerOf(bars()[0]) === v, 'bars=' + bars().length);

      // c) 悬停另一个块：两条同时存在
      W.S.hover = i.id; W.renderHud();
      ok('悬停别的块时两条胶囊同时出现', bars().length === 2, 'bars=' + bars().length);
      var owners = bars().map(ownerOf).filter(Boolean).map(function(n){ return n.id; }).sort().join(',');
      ok('两条分别属于选中的视频块与悬停的图片块',
         owners === [i.id, v.id].sort().join(','), owners);

      // d) 指针移到画布空白：只剩钉住的那条
      W.S.hover = null; W.renderHud();
      ok('指针移回画布后只剩选中那条', bars().length === 1 && ownerOf(bars()[0]) === v, 'bars=' + bars().length);

      // e) 点空白取消选中：胶囊才消失
      W.S.sel = null; W.S.hover = null; W.renderHud();
      ok('取消选中后胶囊才消失', bars().length === 0, 'bars=' + bars().length);

      // f) 悬停同一个块不会重复出两条
      W.S.sel = v.id; W.S.hover = v.id; W.renderHud();
      ok('悬停的就是选中的块时不重复渲染', bars().length === 1, 'bars=' + bars().length);

      // g) 悬停文本块不会凭空多一条（文本块本来就没有工具条）
      var t = W.addNode('text', 700, 500);
      W.render();
      W.S.sel = v.id; W.S.hover = t.id; W.renderHud();
      ok('悬停文本块不会多出胶囊', bars().length === 1, 'bars=' + bars().length);
      W.S.sel = null; W.S.hover = null; W.renderHud();
    })();

    /* ---- 29. 首尾帧模式：进出端口要避开首帧/尾帧两个连接点 ----
       .port 默认 top:50%，而帧位点也画在块左侧，两者实测只差 4.4px，糊成一团。
       placePorts() 在帧位行可见时把端口抬到提示框的垂直中心。 */
    (function(){
      W.S.nodes.length = 0; W.S.edges.length = 0; W.S.sel = null;
      var v = W.addNode('video', 300, 120);
      var img = W.addNode('image', 40, 420);
      v.params.mode = 'fl'; v.showFrames = true;
      W.render();

      var vEl = nodeEl(v.id);
      var midY = function(d){ var r = d.getBoundingClientRect(); return r.top + r.height/2; };
      var pin = vEl.querySelector('.port.in'), pout = vEl.querySelector('.port.out');
      var f1 = vEl.querySelector('.fdot[data-port="first"]');
      var f2 = vEl.querySelector('.fdot[data-port="last"]');

      ok('首尾帧模式下两个帧位连接点都在', !!f1 && !!f2);

      // a) 纵向必须真的拉开（圆点半径 6px，20px 才有肉眼可见的间隔）
      var g1 = Math.abs(midY(f1) - midY(pin)), g2 = Math.abs(midY(f2) - midY(pin));
      ok('入端口与首帧连接点拉开 20px 以上', g1 >= 20, 'gap=' + g1.toFixed(1));
      ok('入端口与尾帧连接点拉开 20px 以上', g2 >= 20, 'gap=' + g2.toFixed(1));

      // b) 出入口必须同高，否则进线出线一高一低很难看
      ok('出入端口左右对齐在同一水平线', Math.abs(midY(pin) - midY(pout)) < 0.6,
         'd=' + Math.abs(midY(pin) - midY(pout)).toFixed(2));

      // c) 端口不能被抬出块外面，也不能钻进标题栏
      var vr = vEl.getBoundingClientRect(), pr = pin.getBoundingClientRect();
      ok('端口仍在块的高度范围内', pr.top >= vr.top - 1 && pr.bottom <= vr.bottom + 1);

      // d) 端口上移后，帧位连线仍走自己的锚点，不能被端口带偏
      var e = W.addEdge(img.id, v.id, 'first');
      W.render();
      vEl = nodeEl(v.id);
      var hit = el('#edges .edgeHit[data-edge="' + e.id + '"]');
      ok('帧位连线仍然存在', !!hit);
      if (hit){
        var d = hit.getAttribute('d');
        var endY = parseFloat(d.slice(d.lastIndexOf(' ') + 1).split(',')[1]);
        var portWorldY = v.y + (parseFloat(vEl.querySelector('.port.in').style.top));
        ok('帧位连线的终点不跟着端口走', Math.abs(endY - portWorldY) > 20,
           'edgeEndY=' + endY.toFixed(1) + ' portY=' + portWorldY.toFixed(1));
      }

      // e) 不显示帧位行时，端口回到垂直中线（不能被永久顶上去）
      v.params.mode = 'omni'; v.showFrames = false;
      W.render();
      vEl = nodeEl(v.id);
      ok('多参考模式下没有帧位连接点', vEl.querySelectorAll('.fdot').length === 0);
      var h = vEl.offsetHeight;
      ok('多参考模式端口回到垂直中线',
         Math.abs(parseFloat(vEl.querySelector('.port.in').style.top) - h/2) < 0.6,
         'top=' + vEl.querySelector('.port.in').style.top + ' h/2=' + (h/2));

      // f) 切回首尾帧，端口要重新避让（不能只算一次）
      v.params.mode = 'fl'; v.showFrames = true;
      W.render();
      vEl = nodeEl(v.id);
      ok('切回首尾帧后端口重新上移',
         parseFloat(vEl.querySelector('.port.in').style.top) < h/2 - 10,
         'top=' + vEl.querySelector('.port.in').style.top);
    })();

    /* ---- 30. 帧位连接点要落在块的边框上，与进出端口同一条竖轴 ----
       原来 .fdot 是 .fslot 里的流内元素，靠 margin-left:-15px 顶出去，
       实测落在块内侧 11px（portIn.x=0.0 而 fdot.x=11.0），和端口错开一截。 */
    (function(){
      W.S.nodes.length = 0; W.S.edges.length = 0; W.S.sel = null;
      var v = W.addNode('video', 300, 120);
      var img = W.addNode('image', 40, 460);
      v.params.mode = 'fl'; v.showFrames = true;
      W.render();

      var vEl = nodeEl(v.id);
      var vr = vEl.getBoundingClientRect();
      var cxOf = function(d){ var r = d.getBoundingClientRect(); return r.left + r.width/2 - vr.left; };
      var pin = vEl.querySelector('.port.in');
      var f1 = vEl.querySelector('.fdot[data-port="first"]');
      var f2 = vEl.querySelector('.fdot[data-port="last"]');

      // a) 与进出端口同一条竖轴（用户明确要求）
      ok('首帧连接点与入端口同轴', Math.abs(cxOf(f1) - cxOf(pin)) < 0.6,
         'fdot=' + cxOf(f1).toFixed(1) + ' port=' + cxOf(pin).toFixed(1));
      ok('尾帧连接点与入端口同轴', Math.abs(cxOf(f2) - cxOf(pin)) < 0.6,
         'fdot=' + cxOf(f2).toFixed(1) + ' port=' + cxOf(pin).toFixed(1));

      // b) 落在块的边框上，不是块内侧
      ok('首帧连接点压在块边框上', Math.abs(cxOf(f1)) < 1.5, 'x=' + cxOf(f1).toFixed(1));
      ok('尾帧连接点压在块边框上', Math.abs(cxOf(f2)) < 1.5, 'x=' + cxOf(f2).toFixed(1));

      // c) 三个点在竖直方向仍然拉得开（别为了对齐 x 又叠到一起）
      var cyOf = function(d){ var r = d.getBoundingClientRect(); return r.top + r.height/2; };
      ok('首帧与尾帧连接点仍然错开', Math.abs(cyOf(f2) - cyOf(f1)) >= 20,
         'd=' + Math.abs(cyOf(f2) - cyOf(f1)).toFixed(1));

      // d) 连线终点要跟着圆点走 —— 锚点改成读真实坐标后不再有经验偏移
      var e = W.addEdge(img.id, v.id, 'first');
      W.render();
      vEl = nodeEl(v.id);
      var hit = el('#edges .edgeHit[data-edge="' + e.id + '"]');
      ok('帧位连线仍然存在', !!hit);
      if (hit){
        var dTxt = hit.getAttribute('d');
        var tail = dTxt.slice(dTxt.lastIndexOf(' ') + 1).split(',');
        var ex = parseFloat(tail[0]), ey = parseFloat(tail[1]);
        var vr2 = vEl.getBoundingClientRect();
        var fd = vEl.querySelector('.fdot[data-port="first"]').getBoundingClientRect();
        var wantX = fd.left + fd.width/2 - vr2.left + v.x;
        var wantY = fd.top + fd.height/2 - vr2.top + v.y;
        ok('帧位连线终点跟着圆点走', Math.abs(ex - wantX) < 2 && Math.abs(ey - wantY) < 2,
           'edge=(' + ex.toFixed(1) + ',' + ey.toFixed(1) + ') dot=(' + wantX.toFixed(1) + ',' + wantY.toFixed(1) + ')');
      }

      // e) 帧位行里的文字没被圆点的绝对定位挤走
      ok('帧位文字仍在行内', vEl.querySelectorAll('.fslot .ftxt').length === 2);
    })();

    /* ---- 31. 多参考模式：原站在输入框左上角显示「输入」标签和 @ 徽章 ----
       依据原站截图 2026-10-16 220957：图片块连入后，框内是
       「输入 [@] 使用参考内容，例如：@图片1 @视频1 @音频1」。 */
    (function(){
      W.S.nodes.length = 0; W.S.edges.length = 0; W.S.sel = null;
      var v = W.addNode('video', 300, 120);
      var img = W.addNode('image', 40, 420);
      v.params.mode = 'omni'; v.showFrames = false;
      W.addEdge(img.id, v.id, 'in');
      W.render();

      var phOf = function(id){
        // omni 用 .pEdit（placeholder 落在 data-ph），其余还是 textarea.prompt
        var vEl = nodeEl(id);
        var ta = vEl.querySelector('textarea.prompt');
        if (ta) return ta.placeholder;
        var p = vEl.querySelector('.pEdit');
        return p ? (p.getAttribute('data-ph') || '') : '';
      };

      var vEl = nodeEl(v.id);
      ok('多参考模式渲染出输入前缀', !!vEl.querySelector('.promptPre'),
         'pre=' + !!vEl.querySelector('.promptPre'));
      ok('输入前缀带 @ 徽章', !!vEl.querySelector('.promptPre .at'));
      ok('输入框让出前缀的位置', /hasPre/.test(vEl.querySelector('.promptBox').className));
      ok('多参考模式 placeholder 与原站一致',
         phOf(v.id) === '使用参考内容，例如：@图片1 @视频1 @音频1', phOf(v.id));

      // 非多参考模式不该有这个前缀（用户说的触发条件是「为多参考生成」）
      W.S.edges.length = 0;
      v.params.mode = 't2v'; v.showFrames = false;
      W.render();
      ok('非多参考模式没有输入前缀', !nodeEl(v.id).querySelector('.promptPre'));
      ok('非多参考模式 placeholder 变回描述文案',
         phOf(v.id) === '描述你想要生成的视频内容', phOf(v.id));

      // 文本镜像态是只读的，也不该显示前缀
      W.S.nodes.length = 0; W.S.edges.length = 0;
      var t = W.addNode('text', 60, 120); t.text = '写点什么';
      var v2 = W.addNode('video', 460, 120);
      v2.params.mode = 'omni';
      W.addEdge(t.id, v2.id, 'in');
      W.render();
      ok('文本镜像态没有输入前缀', !nodeEl(v2.id).querySelector('.promptPre'));
    })();

    /* ---- 32. 双击块名进入重命名（原站行为，见截图 2026-10-16 221625 / 221653） ----
       原来只有 F2 和右键菜单能改名，双击名称没反应。
       双击是 mousedown 里手工判的，不依赖原生 dblclick 事件。 */
    (function(){
      function twoClicks(target, x, y){
        // 完整走两轮 mousedown → mouseup → click，和真实双击一致
        mouse(target, 'mousedown', x, y); mouse(target, 'mouseup', x, y); mouse(target, 'click', x, y);
        mouse(target, 'mousedown', x, y); mouse(target, 'mouseup', x, y); mouse(target, 'click', x, y);
      }
      function nameOf(id){ return nodeEl(id).querySelector('.nName'); }
      function inputOf(id){ return nodeEl(id).querySelector('.nRename'); }
      W.S.nodes.length = 0; W.S.edges.length = 0; W.S.sel = null; W.S.renaming = null; W.S.dbl = null;
      var v = W.addNode('video', 120, 120);
      W.render();

      ok('块名渲染成可识别的 .nName', !!nameOf(v.id));

      // 命中区要比文字本身大：小字也好点（用 padding 撑的）
      var nEl = nameOf(v.id), nb = nEl.getBoundingClientRect();
      ok('块名命中区大于文字本身', nb.width > 40 && nb.height > 20,
         Math.round(nb.width) + 'x' + Math.round(nb.height));

      // 光标按下那一瞬间 <b> 必须还在文档里
      mouse(nEl, 'mousedown');
      ok('按下时块名没有被重建掉', nEl.isConnected);
      mouse(nEl, 'mouseup');
      ok('单击一次不进入重命名', !inputOf(v.id));

      twoClicks(nameOf(v.id), nb.left + nb.width/2, nb.top + nb.height/2);
      var inp = inputOf(v.id);
      ok('双击块名进入重命名态', !!inp);
      ok('重命名输入框自动获得焦点', document.activeElement === inp,
         'active=' + (document.activeElement ? document.activeElement.className : 'null'));
      ok('重命名态下原名不再是 <b>', !nameOf(v.id));

      // 输入并回车
      inp.value = '我的短片';
      inp.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}));
      ok('回车后名称写进状态', W.S.nodes.filter(function(n){ return n.id === v.id; })[0].name === '我的短片',
         JSON.stringify(W.S.nodes.filter(function(n){ return n.id === v.id; })[0].name));
      ok('回车后退出重命名态', !inputOf(v.id));
      ok('回车后显示新名称', nameOf(v.id).textContent === '我的短片', nameOf(v.id).textContent);

      // Esc 取消：名字不该被改
      twoClicks(nameOf(v.id), 0, 0);
      var inp2 = inputOf(v.id);
      ok('再次双击可重命名', !!inp2);
      inp2.value = '不该保存';
      inp2.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
      ok('Esc 放弃修改', W.S.nodes.filter(function(n){ return n.id === v.id; })[0].name === '我的短片',
         JSON.stringify(W.S.nodes.filter(function(n){ return n.id === v.id; })[0].name));

      // 两次单击间隔太久不算双击（这里时间戳必然远超阈值，只验证缓冲会被清掉）
      W.S.dbl = { id:v.id, t:performance.now() - 5000, x:0, y:0 };
      mouse(nameOf(v.id), 'mousedown'); mouse(nameOf(v.id), 'mouseup');
      ok('间隔过久不算双击', !inputOf(v.id));
      // 点画布空白要作废上一次的缓冲
      W.S.dbl = { id:v.id, t:performance.now(), x:0, y:0 };
      mouse(el('#canvasWrap'), 'mousedown'); mouse(el('#canvasWrap'), 'mouseup');
      ok('点空白后双击缓冲被清掉', W.S.dbl === null, 'dbl=' + JSON.stringify(W.S.dbl));

      // 「任何一个块」都要能改名
      ['text','image','video'].forEach(function(tp){
        var n = W.addNode(tp, 400, 300);
        W.render();
        var nb2 = nameOf(n.id).getBoundingClientRect();
        twoClicks(nameOf(n.id), nb2.left + nb2.width/2, nb2.top + nb2.height/2);
        ok(tp + '块双击名称也能进入重命名', !!inputOf(n.id));
        W.S.renaming = null; W.render();
      });

      // 标题栏别处双击仍然是拖动块，不该误触发重命名
      W.S.renaming = null; W.S.dbl = null;
      var img2 = W.addNode('image', 700, 300);
      W.render();
      var head = nodeEl(img2.id).querySelector('.nHead');
      var hb = head.getBoundingClientRect();
      twoClicks(head, hb.right - 6, hb.top + hb.height/2);   // 标题栏右侧空白（角色标签那边）
      ok('双击标题栏空白处不触发重命名', !inputOf(img2.id));

      // 从名称上按下不该启动拖动
      W.S.dbl = null;
      mouse(nameOf(img2.id), 'mousedown');
      ok('在块名上按下不会启动拖动', W.S.drag === null, 'drag=' + JSON.stringify(W.S.drag));
      mouse(nameOf(img2.id), 'mouseup');

      // R 键改名；F2 不再响应（功能键被系统/软件占用，按不到）
      W.S.sel = img2.id;
      document.dispatchEvent(new KeyboardEvent('keydown', {key:'r', bubbles:true}));
      ok('按 R 进入重命名', !!inputOf(img2.id));
      W.S.renaming = null; W.render();
      W.S.sel = img2.id;
      document.dispatchEvent(new KeyboardEvent('keydown', {key:'F2', bubbles:true}));
      ok('按 F2 不再有反应', !inputOf(img2.id));

      // Ctrl+R 是浏览器刷新，不能被我们截胡
      W.S.sel = img2.id;
      document.dispatchEvent(new KeyboardEvent('keydown', {key:'r', ctrlKey:true, bubbles:true}));
      ok('Ctrl+R 不触发重命名', !inputOf(img2.id));
      W.S.sel = null;
    })();

    /* ---- 33. 多参考模式输入 @ 选素材（原站截图 2026-10-16 223709 / 224059 / 224342） ----
       只有「有真实内容」的上游块会进列表；选中后插成内联芯片，
       并且不能吞掉用户自己已经打的那段文字（截图里是 "use ... to ..."）。 */
    (function(){
      function caretEnd(el){
        var s = window.getSelection(), r = document.createRange();
        r.setStart(el, el.childNodes.length); r.collapse(true);
        s.removeAllRanges(); s.addRange(r);
      }
      function type(ed, txt){          // 在末尾追加文字并派发 input（别用 textContent=，那会冲掉芯片）
        ed.focus();
        if (txt) ed.appendChild(document.createTextNode(txt));
        caretEnd(ed);
        ed.dispatchEvent(new InputEvent('input', {bubbles:true}));
      }
      function ed(id){ return nodeEl(id).querySelector('.pEdit'); }
      function pop(id){ return nodeEl(id).querySelector('.atPop'); }
      function prompt(id){ return W.S.nodes.filter(function(n){ return n.id === id; })[0].prompt; }

      W.S.nodes.length = 0; W.S.edges.length = 0; W.S.sel = null; W.S.at = null; W.S.atNode = null;
      var v = W.addNode('video', 500, 100);
      v.params.mode = 'omni'; v.showFrames = false;
      var full = W.addNode('image', 100, 100);
      full.img = { url:'/media/a.png', name:'a.png' };
      var blank = W.addNode('image', 100, 500);      // 空块，没有 img
      W.addEdge(full.id, v.id, 'in');
      W.addEdge(blank.id, v.id, 'in');
      W.render();

      ok('omni 模式用 contenteditable 而不是 textarea', !!ed(v.id) && !nodeEl(v.id).querySelector('textarea.prompt'));

      // a) 输入 @ 弹出列表，只含有内容的块
      type(ed(v.id), '@');
      ok('输入 @ 后列表弹出', pop(v.id).classList.contains('show'));
      var titles = all('.atPop .atTitle').map(function(x){ return x.textContent; }).join('|');
      ok('列表按类型分组，标题是「图片节点」', titles === '图片节点', titles);
      var labels = all('.atPop .atItem').map(function(x){ return x.textContent.trim(); });
      ok('列表只有 1 项，空块不进列表', labels.length === 1, labels.join(','));
      ok('列表项叫「图片 1」', labels[0] === '图片 1', labels[0]);

      // b) 选中：@ 换成芯片，自己打的字要留下
      ed(v.id).textContent = 'use @';
      type(ed(v.id), '');
      click(nodeEl(v.id).querySelector('.atPop .atItem'));
      var chip = nodeEl(v.id).querySelector('.mChip');
      ok('选中后插入一个内联芯片', nodeEl(v.id).querySelectorAll('.mChip').length === 1);
      ok('芯片带引用标记', !!chip && chip.dataset.ref === '图片:1', chip && chip.dataset.ref);
      ok('芯片标签用 .mLb（.lb 已被灯箱占用）', !!nodeEl(v.id).querySelector('.mLb'));
      ok('芯片标签没被同名规则隐藏',
         getComputedStyle(nodeEl(v.id).querySelector('.mLb')).display !== 'none');
      ok('芯片有实际宽度', chip.getBoundingClientRect().width > 40,
         Math.round(chip.getBoundingClientRect().width) + 'px');
      ok('模型里存的是 @图片1', prompt(v.id).indexOf('@图片1') >= 0, JSON.stringify(prompt(v.id)));
      ok('用户自己打的 use 保留下来了', prompt(v.id).indexOf('use') === 0, JSON.stringify(prompt(v.id)));
      ok('选中后列表收起', !pop(v.id).classList.contains('show'));

      // c) 选中后还能接着打字
      ed(v.id).appendChild(document.createTextNode('to ...'));
      type(ed(v.id), '');
      ok('后续文字接得上',
         prompt(v.id).indexOf('use') === 0 && prompt(v.id).indexOf('@图片1') > 0 &&
         prompt(v.id).indexOf('to ...') > 0, JSON.stringify(prompt(v.id)));

      // d) 状态只存纯文本，重绘后芯片要还原
      W.render();
      ok('重绘后芯片从纯文本还原', nodeEl(v.id).querySelectorAll('.mChip').length === 1,
         'n=' + nodeEl(v.id).querySelectorAll('.mChip').length);

      // e) 点芯片移除
      mouse(nodeEl(v.id).querySelector('.mChip'), 'mousedown');
      ok('点芯片可移除', nodeEl(v.id).querySelectorAll('.mChip').length === 0);
      ok('移除后文字还在', prompt(v.id).indexOf('use') === 0 && prompt(v.id).indexOf('@图片1') < 0,
         JSON.stringify(prompt(v.id)));

      // f) 第二个有内容的块接上 -> 两项
      var full2 = W.addNode('image', 100, 700);
      full2.img = { url:'/media/b.png', name:'b.png' };
      W.addEdge(full2.id, v.id, 'in');
      W.render();
      type(ed(v.id), '@');
      var l2 = all('.atPop .atItem').map(function(x){ return x.textContent.trim(); }).join(',');
      ok('两个有内容的块时列出 图片 1 / 图片 2', l2 === '图片 1,图片 2', l2);

      // g) 全是空块时不弹列表
      W.S.nodes.length = 0; W.S.edges.length = 0; W.S.at = null; W.S.atNode = null;
      var v2 = W.addNode('video', 300, 100);
      v2.params.mode = 'omni'; v2.showFrames = false;
      var e1 = W.addNode('image', 40, 300);
      W.addEdge(e1.id, v2.id, 'in');
      W.render();
      type(ed(v2.id), '@');
      ok('上游全是空块时不弹列表', !pop(v2.id).classList.contains('show'));
      ok('上游全是空块时没有可选项', all('.atPop .atItem').length === 0);

      // h) 非 omni 仍是普通 textarea，@ 逻辑不该出现
      W.S.nodes.length = 0; W.S.edges.length = 0;
      var v3 = W.addNode('video', 300, 100);
      v3.params.mode = 't2v'; v3.showFrames = false;
      W.render();
      ok('非多参考模式仍是 textarea', !!nodeEl(v3.id).querySelector('textarea.prompt'));
      ok('非多参考模式没有 @ 编辑器', !nodeEl(v3.id).querySelector('.pEdit'));
    })();

    /* ---- 34. 费用按「模型 × 分辨率」分档 ----
       Prime 优速版单价是标准版的 2 倍以上。以前 videoCost() 只看分辨率，
       选 Prime 时界面显示 ¥2.10，实际扣 ¥4.50 —— 少报一半多。 */
    (function(){
      W.S.nodes.length = 0; W.S.edges.length = 0;
      var v = W.addNode('video', 300, 100);
      v.params.resolution = '720P'; v.params.duration = 5;
      W.render();

      function costOf(id){
        var el = nodeEl(id).querySelector('[data-cost]');
        return el ? el.textContent : '(没有 cost 元素)';
      }

      ok('标准版 5s/720P = ¥2.10', Math.abs(W.cost('wan3.0-video').video - 2.10) < 1e-9, W.cost('wan3.0-video').video);
      ok('Prime  5s/720P = ¥4.50', Math.abs(W.cost('wan3.0-video-prime').video - 4.50) < 1e-9, W.cost('wan3.0-video-prime').video);
      ok('Prime 明显贵于标准版', W.cost('wan3.0-video-prime').video > W.cost('wan3.0-video').video);
      ok('DOM 默认显示标准版价', costOf(v.id) === '¥2.10', costOf(v.id));

      // 切到 Prime，DOM 上的价格必须跟着变
      v.params.model = 'wan3.0-video-prime';
      W.render();
      ok('切到 Prime 后 DOM 显示 ¥4.50', costOf(v.id) === '¥4.50', costOf(v.id));

      // 三档分辨率都要分开算
      v.params.resolution = '480P';
      ok('Prime  5s/480P = ¥2.25', Math.abs(W.cost('wan3.0-video-prime').video - 2.25) < 1e-9, W.cost('wan3.0-video-prime').video);
      ok('标准版 5s/480P = ¥1.05', Math.abs(W.cost('wan3.0-video').video - 1.05) < 1e-9, W.cost('wan3.0-video').video);
      v.params.resolution = '1080P';
      ok('Prime  5s/1080P = ¥9.00', Math.abs(W.cost('wan3.0-video-prime').video - 9.00) < 1e-9, W.cost('wan3.0-video-prime').video);
      ok('标准版 5s/1080P = ¥4.20', Math.abs(W.cost('wan3.0-video').video - 4.20) < 1e-9, W.cost('wan3.0-video').video);

      // 时长照常联动
      v.params.duration = 10;
      ok('Prime 10s/720P = ¥9.00', (function(){ v.params.resolution='720P';
             return Math.abs(W.cost('wan3.0-video-prime').video - 9.00) < 1e-9; })(), W.cost('wan3.0-video-prime').video);

      // 认不出的模型退回标准版，不能变成 0 或 NaN
      v.params.model = 'wan9.9-unknown';
      var fb = W.cost().video;
      ok('未知模型退回标准版而不是 0', Math.abs(fb - 4.20) < 1e-9, fb);
    })();

    /* ---- 35. 图片费用按「模型 × 输出规格 + 参考图输入」计价 ----
       旧表只有 1K 0.2 / 2K 0.6 且不看模型，四个模型全按同一价算。 */
    (function(){
      W.S.nodes.length = 0; W.S.edges.length = 0;
      var i = W.addNode('image', 300, 100);
      i.imgParams.model = 'qwen-image-3.0-pro';
      i.imgParams.size = '1024x1024'; i.imgParams.n = 1;
      W.render();

      function ct(){ return W.costText(i); }

      ok('3.0-pro 1K ×1 = ¥0.25', ct() === '¥0.25', ct());
      i.imgParams.size = '1536x1024';
      ok('3.0-pro 2K 是 1K 的两倍 = ¥0.50', ct() === '¥0.50', ct());

      // 3.0 的 1K/2K 同价 —— 官方表里就是这样，别想当然以为 2K 更贵
      i.imgParams.model = 'qwen-image-3.0';
      ok('3.0 2K = ¥0.18', ct() === '¥0.18', ct());
      i.imgParams.size = '1024x1024';
      ok('3.0 1K 同为 ¥0.18', ct() === '¥0.18', ct());

      // 2.1 系列不区分规格，且最便宜
      i.imgParams.model = 'qwen-image-2.1-turbo';
      i.imgParams.size = '1536x1024';
      ok('2.1-turbo 不分规格 = ¥0.10', ct() === '¥0.10', ct());
      i.imgParams.model = 'qwen-image-2.1-pro';
      ok('2.1-pro 不分规格 = ¥0.25', ct() === '¥0.25', ct());

      // 数量：按张乘
      i.imgParams.model = 'qwen-image-3.0-pro';
      i.imgParams.size = '1024x1024';
      i.imgParams.n = 4;
      ok('3.0-pro 1K ×4 = ¥1.00', ct() === '¥1.00', ct());
      i.imgParams.n = 1;

      // 参考图输入费：3.0 系列每张 0.02，2.1 系列不收
      var r1 = W.addNode('image', 40, 300);
      r1.img = { url:'/media/r1.png', name:'r1.png' };
      W.addEdge(r1.id, i.id, 'in');
      W.render();
      ok('3.0-pro +1 张参考图 = ¥0.27（+0.02 输入费）', ct() === '¥0.27', ct());

      var r2 = W.addNode('image', 40, 600);
      r2.img = { url:'/media/r2.png', name:'r2.png' };
      W.addEdge(r2.id, i.id, 'in');
      W.render();
      ok('3.0-pro +2 张参考图 = ¥0.29', ct() === '¥0.29', ct());

      // 2.1-turbo 有 10 张上限，插满 3 张看输入费确实为 0
      i.imgParams.model = 'qwen-image-2.1-turbo';
      W.render();
      ok('2.1-turbo 参考图不收输入费（仍 ¥0.10）', ct() === '¥0.10', ct());

      // 断开后费用回落到 ¥0.10（两条边都要断，2.1 不收输入费所以只有一张时看不出差别）
      W.S.edges.filter(function(e){ return e.to === i.id; }).forEach(function(e){ W.delEdge(e.id); });
      W.render();
      ok('断开全部参考图后费用回落', ct() === '¥0.10', ct());

      // 认不出的模型退回默认模型，不能变 0 或 NaN
      i.imgParams.model = 'qwen-image-9.9';
      ok('未知模型退回默认模型而不是 0', ct() === '¥0.25', ct());
    })();

    /* ---- 36. 负向提示词：图片块两个框 + 文本块连图片块时两截 ----
       官方只排除 wan2.7-image-pro / wan2.7-image，qwen-image 全系列都支持，
       所以 UI 上不做任何「不支持」的提示。 */
    (function(){
      var HEAD = 'Low resolution, low quality';

      function ta(id, sel){ return nodeEl(id).querySelector(sel); }
      function cnt(id){ return nodeEl(id).querySelectorAll('textarea').length; }
      function setVal(el2, v){
        el2.value = v;
        el2.dispatchEvent(new Event('input', {bubbles:true}));
      }
      function byId(id){ return W.S.nodes.filter(function(n){ return n.id === id; })[0]; }

      // --- 新建文本块：一截 ---
      W.S.nodes.length = 0; W.S.edges.length = 0;
      var t0 = W.addNode('text', 40, 200);
      W.render();
      ok('新文本块只有一个输入框', cnt(t0.id) === 1, cnt(t0.id));
      ok('未连图片块时不显示负向那一截', !ta(t0.id, 'textarea.prompt.neg'));

      // --- 新建图片块：两框 + 默认值 ---
      var i0 = W.addNode('image', 300, 200);
      W.render();
      ok('图片块有两个输入框', cnt(i0.id) === 2, cnt(i0.id));
      ok('上面是提示词、下面是负向提示词',
         ta(i0.id, 'textarea.prompt:not(.neg)').dataset.bind === 'imgPrompt' &&
         ta(i0.id, 'textarea.prompt.neg').dataset.bind === 'negPrompt');
      ok('负向提示词默认是官方那句',
         ta(i0.id, 'textarea.prompt.neg').value.indexOf(HEAD) === 0 &&
         i0.imgParams.negativePrompt.indexOf(HEAD) === 0,
         ta(i0.id, 'textarea.prompt.neg').value.slice(0, 28));

      setVal(ta(i0.id, 'textarea.prompt.neg'), 'blurry');
      ok('负向框输入写回 imgParams.negativePrompt',
         i0.imgParams.negativePrompt === 'blurry', i0.imgParams.negativePrompt);

      // --- 文本块连上图片块 -> 两截 ---
      var t1 = W.addNode('text', 40, 500);
      t1.text = '一只猫';
      W.addEdge(t1.id, i0.id, 'in');
      W.render();
      ok('文本块连上图片块后变两截', cnt(t1.id) === 2, cnt(t1.id));
      ok('文本块第二截绑定 negText', ta(t1.id, 'textarea.prompt.neg').dataset.bind === 'negText');
      ok('文本块负向也带默认值', ta(t1.id, 'textarea.prompt.neg').value.indexOf(HEAD) === 0);

      // --- 镜像：两个框都只读，负向取上游那一截 ---
      var iv = ta(i0.id, 'textarea.prompt:not(.neg)');
      var nv = ta(i0.id, 'textarea.prompt.neg');
      ok('镜像后提示词框只读并显示上游文字', iv.readOnly === true && iv.value === '一只猫', iv.value);
      ok('镜像后负向框只读', nv.readOnly === true, String(nv.readOnly));
      ok('镜像后负向取上游的（不是图片块自己那份 blurry）',
         nv.value.indexOf(HEAD) === 0 && nv.value !== 'blurry', nv.value.slice(0, 28));
      ok('mirrorText 带回了负向', W.mirrorText(i0).neg.indexOf(HEAD) === 0);

      setVal(ta(t1.id, 'textarea.prompt.neg'), '不要糊');
      ok('文本块负向写入 negText', t1.negText === '不要糊', t1.negText);
      W.render();
      ok('上游改负向，图片块负向框跟着变',
         ta(i0.id, 'textarea.prompt.neg').value === '不要糊',
         ta(i0.id, 'textarea.prompt.neg').value);

      // --- 断开 -> 回落 ---
      W.delEdge(W.S.edges.filter(function(e){ return e.from === t1.id; })[0].id);
      W.render();
      ok('断开后图片块负向回落到自己填的 blurry',
         ta(i0.id, 'textarea.prompt.neg').value === 'blurry',
         ta(i0.id, 'textarea.prompt.neg').value);
      ok('断开后文本块回到一截', cnt(t1.id) === 1, cnt(t1.id));

      // --- 连视频块不分两截 ---
      W.S.nodes.length = 0; W.S.edges.length = 0;
      var t2 = W.addNode('text', 40, 200);
      var v2 = W.addNode('video', 400, 200);
      W.addEdge(t2.id, v2.id, 'in');
      W.render();
      ok('文本块连视频块仍然只有一截', cnt(t2.id) === 1, cnt(t2.id));

      // --- 同时连视频和图片：仍分两截 ---
      var i2 = W.addNode('image', 400, 620);
      W.addEdge(t2.id, i2.id, 'in');
      W.render();
      ok('文本块同时连视频和图片时分两截', cnt(t2.id) === 2, cnt(t2.id));

      // --- 上游没填负向时不凭空造 ---
      W.S.nodes.length = 0; W.S.edges.length = 0;
      var t3 = W.addNode('text', 40, 200);
      t3.text = '狗';
      t3.negText = '';
      var i3 = W.addNode('image', 400, 200);
      W.addEdge(t3.id, i3.id, 'in');
      W.render();
      ok('上游没填负向时镜像出来的负向是空', W.mirrorText(i3).neg === '', JSON.stringify(W.mirrorText(i3).neg));
      ok('此时图片块负向框显示空',
         ta(i3.id, 'textarea.prompt.neg').value === '', JSON.stringify(ta(i3.id, 'textarea.prompt.neg').value));

      // --- 老工作流导入：补默认值 ---
      W.loadWfText(JSON.stringify({ version:2, nodes:[
        { id:'n1', type:'image', x:0, y:0, imgParams:{ model:'qwen-image-3.0', size:'1024x1024', n:1 } },
        { id:'n2', type:'text', x:0, y:0, text:'x' }
      ], edges:[] }), null, 'old.json');
      ok('老工作流的 negativePrompt 被补成默认值',
         !!(byId('n1') || {}).imgParams &&
         String((byId('n1') || {}).imgParams.negativePrompt).indexOf(HEAD) === 0,
         String(((byId('n1') || {}).imgParams || {}).negativePrompt).slice(0, 28));
      ok('老工作流的 negText 也被补上默认值',
         String((byId('n2') || {}).negText).indexOf(HEAD) === 0);

      W.loadWfText(JSON.stringify({ version:2, nodes:[
        { id:'n1', type:'image', x:0, y:0, imgParams:{ model:'qwen-image-3.0', negativePrompt:'' } }
      ], edges:[] }), null, 'old2.json');
      ok('用户自己清空成空串的不会被默认值塞回',
         (byId('n1') || {}).imgParams && byId('n1').imgParams.negativePrompt === '',
         JSON.stringify(((byId('n1') || {}).imgParams || {}).negativePrompt));

      // --- 导出再导入不丢 ---
      W.S.nodes.length = 0; W.S.edges.length = 0;
      var t4 = W.addNode('text', 40, 200);
      t4.text = 'p'; t4.negText = 'neg-keep';
      var i4 = W.addNode('image', 400, 200);
      i4.imgParams.negativePrompt = 'own-neg';
      W.addEdge(t4.id, i4.id, 'in');
      W.loadWfText(W.wfText(), null, 'rt.json');
      ok('导出导入后文本块负向不丢',
         (W.S.nodes.filter(function(n){ return n.type === 'text'; })[0] || {}).negText === 'neg-keep');
      ok('导出导入后图片块负向不丢',
         (W.S.nodes.filter(function(n){ return n.type === 'image'; })[0] || {}).imgParams.negativePrompt === 'own-neg');

      // --- 提交时真正用的是哪一段（runImage 里那两行） ---
      W.S.nodes.length = 0; W.S.edges.length = 0;
      var t5 = W.addNode('text', 40, 200);
      t5.text = '上游提示词'; t5.negText = '上游负向';
      var i5 = W.addNode('image', 400, 200);
      i5.imgPrompt = '块内提示词'; i5.imgParams.negativePrompt = '块内负向';
      W.addEdge(t5.id, i5.id, 'in');
      var r5 = W.imgTexts(i5);
      ok('镜像时提交用的提示词来自上游', r5.prompt === '上游提示词', r5.prompt);
      ok('镜像时提交用的负向也来自上游（与提示词同源）', r5.neg === '上游负向', r5.neg);
      ok('镜像时提示词与负向取自同一个来源（on 一致）', r5.on === true);

      W.delEdge(W.S.edges.filter(function(e){ return e.from === t5.id; })[0].id);
      var r6 = W.imgTexts(i5);
      ok('未镜像时提交用块内自己填的两段', r6.prompt === '块内提示词' && r6.neg === '块内负向',
         r6.prompt + ' / ' + r6.neg);

      i5.imgParams.negativePrompt = '';
      ok('负向清空后提交时不带 undefined 之外的怪值', W.imgTexts(i5).neg === '');

      ok('textNegOn 对无出边的文本块为 false', W.textNegOn(t5) === false);
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
