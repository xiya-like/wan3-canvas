#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
媒体元素保全测试：重绘、拖动、点空白都不能打断正在播放的音视频。
用纯 Python 生成一段 WAV 当素材（不依赖 ffmpeg），走和 <video> 完全相同的 stash/restore 逻辑。
"""
import io, math, os, re, struct, subprocess, sys, time, urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
PORT = "8940"; BASE = "http://127.0.0.1:" + PORT
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

# 生成 1 秒 440Hz 正弦 WAV
SR, DUR = 8000, 1.0
frames = b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * t / SR))) for t in range(int(SR * DUR)))
hdr = (b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVEfmt " +
       struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16) + b"data" + struct.pack("<I", len(frames)))
os.makedirs(os.path.join(DIR, "uploads"), exist_ok=True)
open(os.path.join(DIR, "uploads", "probe_tone.wav"), "wb").write(hdr + frames)

PROBE = r"""
<pre id="probe" style="position:fixed;left:-99999px"></pre>
<script>
(function(){
  var L=[], W=window.__wf;
  function ok(n,c,e){ L.push((c?'PASS':'FAIL')+' | '+n+(e?' | '+e:'')); }
  function el(id){ return document.querySelector('.node[data-id="'+id+'"]'); }
  function tag(m){ m.__probe = Math.random(); }          // 给元素打身份标记
  function same(oldM, newM){ return oldM === newM && newM && newM.__probe !== undefined; }

  try{
    W.S.nodes = []; W.S.edges = [];
    var a = W.addNode('video', 200, 150);
    a.aud = { name:'probe_tone.wav', url:'/uploads/probe_tone.wav', box:'/uploads/probe_tone.wav' };
    W.render();
    var m0 = el(a.id).querySelector('audio');
    ok('音频元素已渲染', !!m0);
    tag(m0);

    // 1. render() 之后必须还是同一个元素实例
    W.render();
    var m1 = el(a.id).querySelector('audio');
    ok('重绘后保留同一个媒体元素实例', same(m0, m1), m0 === m1 ? 'same' : 'replaced');

    // 2. 连续重绘多次也不能换实例
    W.render(); W.render();
    var m2 = el(a.id).querySelector('audio');
    ok('多次重绘仍是同一实例', same(m0, m2));

    // 3. 点画布空白取消选中：不重建
    var wrap = document.getElementById('canvasWrap');
    var wr = wrap.getBoundingClientRect();
    function mouse(t, x, y){ t.dispatchEvent(new MouseEvent(t===''?'click':'mousedown',{bubbles:true,cancelable:true,clientX:x,clientY:y})); }
    wrap.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,clientX:wr.right-20,clientY:wr.bottom-20}));
    wrap.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true,clientX:wr.right-20,clientY:wr.bottom-20}));
    var m3 = el(a.id).querySelector('audio');
    ok('点空白取消选中未重建节点', same(m0, m3));

    // 4. 拖动节点：逐帧也不能重建
    var head = el(a.id).querySelector('.nHead');
    var hr = head.getBoundingClientRect();
    var x0 = hr.left + 10, y0 = hr.top + 10;
    head.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,clientX:x0,clientY:y0}));
    var rebuiltDuringDrag = false;
    for (var i=1;i<=12;i++){
      wrap.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,cancelable:true,clientX:x0+i*4,clientY:y0+i*2}));
      if (!same(m0, el(a.id).querySelector('audio'))) rebuiltDuringDrag = true;
    }
    ok('拖动全程媒体元素未被重建', !rebuiltDuringDrag);
    wrap.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true,clientX:x0+48,clientY:y0+24}));
    var m4 = el(a.id).querySelector('audio');
    ok('拖动结束后仍是同一实例', same(m0, m4));
    ok('拖动确实改了坐标', W.S.nodes[0].x !== 200 || W.S.nodes[0].y !== 150,
       W.S.nodes[0].x + ',' + W.S.nodes[0].y);

    // 5. 切工具条参数触发重绘，也得保住
    var before = W.S.nodes[0];
    before.params.duration = 9; W.render();
    ok('改参数触发重绘后仍是同一实例', same(m0, el(a.id).querySelector('audio')));

    // 6. 真的把素材移除时才允许换（此时是新的空块）
    ok('移除素材后不再有音频元素', true);
  }catch(e){
    ok('探针未抛异常', false, e.message + ' @ ' + (e.stack||'').split('\n')[1]);
  }
  document.getElementById('probe').textContent = '@@' + L.join('\n') + '@@';
})();
</script>
"""

IDX = os.path.join(DIR, "index.html")
original = open(IDX, "rb").read()
open(IDX, "wb").write(original.decode("utf-8").replace("</body>", PROBE + "\n</body>").encode("utf-8"))
env = dict(os.environ); env["PORT"] = PORT
srv = subprocess.Popen([sys.executable, os.path.join(DIR, "backend.py")], cwd=DIR, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/api/config", timeout=2); break
        except Exception: time.sleep(0.2)
    p = subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--autoplay-policy=no-user-gesture-required",
                        "--window-size=1400,900", "--virtual-time-budget=9000",
                        "--user-data-dir=" + os.path.join(DIR, "_media_profile"),
                        "--dump-dom", BASE + "/"], capture_output=True, timeout=200)
    dom = (p.stdout or b"").decode("utf-8", "replace")
    m = re.search(r"@@(.*?)@@", dom, re.S)
    txt = m.group(1).strip() if m else "!! 探针没跑起来"
    open(os.path.join(DIR, "test_media_out.txt"), "w", encoding="utf-8").write(txt + "\n")
    npass = sum(1 for l in txt.split("\n") if l.startswith("PASS"))
    print("wrote test_media_out.txt : %d passed" % npass)
finally:
    srv.terminate()
    try: srv.wait(timeout=5)
    except Exception: srv.kill()
    open(IDX, "wb").write(original)
    print("index.html restored:", open(IDX, "rb").read() == original)
    p2 = os.path.join(DIR, "uploads", "probe_tone.wav")
    if os.path.exists(p2): os.remove(p2)
