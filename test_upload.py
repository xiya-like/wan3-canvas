#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真实上传链路探针：走 uploadFiles 的完整流程，检查返回的素材对象、URL 可达性和图片能否解码。
只写本地 uploads/，不调用 DashScope，不产生任何费用。
"""
import os
import re
import subprocess
import sys
import time
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
PORT = "8934"
BASE = "http://127.0.0.1:" + PORT
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

PROBE = r"""
<pre id="probe" style="position:fixed;left:-99999px"></pre>
<script>
(function(){
  var L = [], W = window.__wf;
  function ok(n,c,e){ L.push((c?'PASS':'FAIL')+' | '+n+(e?' | '+e:'')); }
  // 造一张真实的 PNG（用 canvas 画，保证能解码）
  var cv = document.createElement('canvas'); cv.width = 320; cv.height = 240;
  var cx = cv.getContext('2d');
  cx.fillStyle = '#3a6fa8'; cx.fillRect(0,0,320,240);
  cx.fillStyle = '#e8c07a'; cx.beginPath(); cx.arc(160,120,60,0,7); cx.fill();
  cv.toBlob(function(blob){
    var file = new File([blob], '测试图片 中文名.png', { type:'image/png' });
    W.uploadFiles([file], { x: 120, y: 120 }).then(function(nodes){
      var n = nodes[0];
      ok('uploadFiles 返回的是节点', !!(n && n.id && n.type), n && n.type);
      ok('节点带上了图片素材', !!(n && n.img));
      ok('素材有 url 字段', !!(n && n.img && typeof n.img.url === 'string'), n && n.img && JSON.stringify(n.img.url));
      ok('素材 url 指向 uploads 目录', !!(n && n.img && /^\/uploads\//.test(n.img.url)), n && n.img && n.img.url);
      ok('文件名保留中文', !!(n && n.img && n.img.name === '测试图片 中文名.png'), n && n.img && n.img.name);

      // 图片能不能真的解码出来
      var im = new Image();
      im.onload = function(){
        ok('图片 URL 可访问且能解码', im.naturalWidth > 0, im.naturalWidth + 'x' + im.naturalHeight);
        W.render();                                   // 调用方负责重绘，这里补上再看 DOM
        var full = document.querySelector('.node[data-id="'+n.id+'"] img.mediaFull');
        ok('块内渲染了大图元素', !!full);
        if (full){
          ok('大图 src 指向 uploads', full.getAttribute('src').indexOf('/uploads/') === 0, full.getAttribute('src'));
          ok('大图已加载出真实尺寸', full.naturalWidth > 0, full.naturalWidth + 'x' + full.naturalHeight);
          ok('大图占满块宽', full.getBoundingClientRect().width > 200, Math.round(full.getBoundingClientRect().width));
          ok('大图高度有上限不会顶爆块', full.getBoundingClientRect().height <= 400, Math.round(full.getBoundingClientRect().height));
        }
        // 悬停素材源块应浮出「替换 / 下载」
        var head = document.querySelector('.node[data-id="'+n.id+'"] .nHead');
        head.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:0,clientY:0}));
        var ctl = Array.prototype.map.call(document.querySelectorAll('#hud .bar .ctl'), function(b){ return b.dataset.ctl; }).join(',');
        ok('素材源块工具条是替换加下载', ctl === 'swap,download', ctl);
        // 块体内不应再有文件名、说明文字、按钮
        var blk = document.querySelector('.node[data-id="'+n.id+'"]');
        ok('块内无更换移除按钮', blk.querySelectorAll('button, .mini').length === 0,
           blk.querySelectorAll('button, .mini').length + ' 个');
        ok('块内无文件名与说明文字', blk.querySelectorAll('.mediaBar, .mediaName, .hint0').length === 0);
        ok('块内只剩素材本体', blk.querySelector('.nBody').children.length === 1 &&
           blk.querySelector('.nBody > div').children.length === 1, blk.querySelector('.nBody').innerHTML.slice(0,90));
        // 点图片开灯箱 —— 必须走真实的 mousedown → mouseup → click。
        // 只派发 click 会漏掉「mousedown 重建 DOM 吃掉 click」这类致命 bug。
        if (full){
          full.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,clientX:0,clientY:0}));
          ok('mousedown 后图片仍在文档里（没被重建掉）', document.contains(full));
          full.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true,clientX:0,clientY:0}));
          var fresh = document.querySelector('.node[data-id="'+n.id+'"] img.mediaFull');
          full.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,clientX:0,clientY:0}));
          var lb = document.getElementById('lb');
          ok('点图片能开灯箱预览', lb.classList.contains('show'));
          ok('灯箱里装的是同一张图', document.getElementById('lbImg').src.indexOf(n.img.url) >= 0,
             document.getElementById('lbImg').src);
          document.getElementById('lbClose').dispatchEvent(new MouseEvent('click',{bubbles:true}));
          ok('灯箱能关闭', !document.getElementById('lb').classList.contains('show'));
        }
        var html = W.mediaHTML(n, 'image');
        ok('渲染 HTML 里没有 undefined', html.indexOf('undefined') < 0, html.slice(0, 120));
        document.getElementById('probe').textContent = '@@' + L.join('\n') + '@@';
      };
      im.onerror = function(){
        ok('图片 URL 可访问且能解码', false, '加载失败 ' + (n && n.img && n.img.url));
        document.getElementById('probe').textContent = '@@' + L.join('\n') + '@@';
      };
      im.src = n.img.url;
    }).catch(function(e){
      ok('uploadFiles 未抛异常', false, e.message);
      document.getElementById('probe').textContent = '@@' + L.join('\n') + '@@';
    });
  }, 'image/png');
})();
</script>
"""

IDX = os.path.join(DIR, "index.html")
original = open(IDX, "rb").read()
probe_version = original.decode("utf-8").replace("</body>", PROBE + "\n</body>").encode("utf-8")

env = dict(os.environ); env["PORT"] = PORT
srv = subprocess.Popen([sys.executable, os.path.join(DIR, "backend.py")], cwd=DIR, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/api/config", timeout=2); break
        except Exception: time.sleep(0.2)
    open(IDX, "wb").write(probe_version)          # 静态白名单只放行 index.html
    p = subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
                        "--window-size=1400,900", "--virtual-time-budget=9000",
                        "--user-data-dir=" + os.path.join(DIR, "_up_profile"),
                        "--dump-dom", BASE + "/"], capture_output=True, timeout=200)
    out = (p.stdout or b"").decode("utf-8", "replace")
    m = re.search(r"@@(.*?)@@", out, re.S)
    txt = m.group(1).strip() if m else "!! 探针没跑起来"
    open(os.path.join(DIR, "test_upload_out.txt"), "w", encoding="utf-8").write(txt + "\n")
    npass = sum(1 for l in txt.split("\n") if l.startswith("PASS"))
    print("wrote test_upload_out.txt : %d passed" % npass)
finally:
    srv.terminate()
    try: srv.wait(timeout=5)
    except Exception: srv.kill()
    open(IDX, "wb").write(original)
    restored = open(IDX, "rb").read()
    print("index.html 已还原:", restored == original)
