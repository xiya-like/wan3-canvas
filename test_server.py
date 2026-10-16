#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""集成验证：真启动后端，检查路由/白名单/配置，再截图。全程不调用任何生成接口。"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
PORT = "8931"
BASE = "http://127.0.0.1:" + PORT
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

# 无头浏览器的 profile 建在系统临时目录，用完即弃；
# 截图 _shot.png 是给人看的成果，留在项目目录里。
WORK = tempfile.mkdtemp(prefix="wan3_server_")

R = []
def ok(name, cond, extra=""):
    R.append(("PASS" if cond else "FAIL") + " | " + name + ((" | " + str(extra)) if extra else ""))

def _probe(d, name, data=b"probe-bytes"):
    """在素材目录里埋一个探针文件，用来验证启动清理的行为。"""
    p = os.path.join(DIR, d, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


# 启动前先埋探针：uploads/ 里的应当被清掉，media/ 与 cache/ 里的必须活下来
_up = _probe("uploads", "_purge_probe.png", b"x" * 300)
_nest = os.path.join(DIR, "uploads", "_purge_probe_dir")
os.makedirs(_nest, exist_ok=True)
_up_nested = os.path.join(_nest, "n.txt")
with open(_up_nested, "wb") as f:
    f.write(b"n")
_md = _probe("media", "_keep_probe.png", b"m" * 100)
_ca = _probe("cache", "_keep_probe.bin", b"c" * 100)

env = dict(os.environ)
env["PORT"] = PORT
env["DASHSCOPE_API_KEY"] = "sk-MOCK-NOT-REAL"
env.pop("KEEP_UPLOADS", None)          # 默认行为：启动即清空
srv = subprocess.Popen([sys.executable, os.path.join(DIR, "backend.py")],
                       cwd=DIR, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def get(path, timeout=5, base=None):
    try:
        with urllib.request.urlopen((base or BASE) + path, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)
    except Exception as e:
        return 0, str(e).encode(), {}


try:
    for _ in range(50):
        st, body, _ = get("/api/config")
        if st == 200:
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("后端没起来: " + str(srv.stderr.read()[:500]))

    st, body, hdr = get("/api/config")
    cfg = json.loads(body.decode("utf-8"))
    ok("服务能启动并响应配置接口", st == 200 and cfg.get("ok") is True)
    ok("配置带出工具条元数据", bool(cfg.get("videoModels")) and bool(cfg.get("imageModels")) and bool(cfg.get("ratios")))
    ok("Key 状态字段存在", "hasKey" in cfg)

    # 启动清理：uploads/ 空了，media/ 与 cache/ 一个字节都没少
    ok("启动时 uploads/ 被清空", not os.path.exists(_up))
    ok("uploads/ 子目录里的文件也清掉了", not os.path.exists(_up_nested))
    ok("uploads/ 目录本身还在（还能继续上传）", os.path.isdir(os.path.join(DIR, "uploads")))
    ok("media/ 保留（花钱生成的成片不能丢）", os.path.exists(_md))
    ok("cache/ 保留", os.path.exists(_ca))

    # 保留开关：KEEP_UPLOADS=1 时应当原样不动
    _up2 = _probe("uploads", "_keep_uploads_probe.png", b"k" * 50)
    env2 = dict(os.environ)
    env2["PORT"] = "8932"
    env2["DASHSCOPE_API_KEY"] = "sk-MOCK-NOT-REAL"
    env2["KEEP_UPLOADS"] = "1"
    srv2 = subprocess.Popen([sys.executable, os.path.join(DIR, "backend.py")],
                            cwd=DIR, env=env2,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(50):
            st2, _, _ = get("/api/config", base="http://127.0.0.1:8932")
            if st2 == 200:
                break
            time.sleep(0.2)
        ok("第二个实例能独立起来", st2 == 200, st2)
        ok("KEEP_UPLOADS=1 时 uploads/ 原样保留", os.path.exists(_up2))
    finally:
        srv2.terminate()
        try:
            srv2.wait(timeout=5)
        except Exception:
            srv2.kill()
        if os.path.exists(_up2):
            os.remove(_up2)

    # 清理后立刻还能正常上传（目录没被删掉）
    up_now = [f for f in os.listdir(os.path.join(DIR, "uploads"))]
    ok("清理后 uploads/ 里没有残留探针",
       "_purge_probe.png" not in up_now and "_keep_uploads_probe.png" not in up_now, up_now[:5])

    st, body, hdr = get("/")
    html = body.decode("utf-8")
    ok("首页可访问且是自绘画布", st == 200 and "canvasWrap" in html and "react" not in html.lower())
    ok("首页不含任何外链脚本", "http://" not in html.replace("http://127.0.0.1", "") or "<script src" not in html)
    ok("HTML 带 UTF-8 声明", "charset=utf-8" in html.lower() or "charset=\"utf-8\"" in html.lower())

    st, _, _ = get("/.env")
    ok("静态服务拒绝 .env", st in (403, 404), st)
    st, _, _ = get("/backend.py")
    ok("静态服务拒绝后端源码", st in (403, 404), st)
    st, _, _ = get("/media/../../.env")
    ok("目录穿越被挡", st in (403, 404), st)

    # 生成接口存在但这里绝不调用；只验证方法路由存在
    st, _, _ = get("/api/nonexistent")
    ok("未知接口返回 404", st == 404, st)

    shot = os.path.join(DIR, "_shot.png")
    cmd = [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
           "--window-size=1600,1000", "--virtual-time-budget=7000",
           "--user-data-dir=" + os.path.join(WORK, "edge_profile"),
           "--screenshot=" + shot, BASE + "/"]
    subprocess.run(cmd, capture_output=True, timeout=180)
    ok("截图已生成", os.path.exists(shot) and os.path.getsize(shot) > 5000,
       os.path.getsize(shot) if os.path.exists(shot) else 0)
finally:
    srv.terminate()
    try:
        srv.wait(timeout=5)
    except Exception:
        srv.kill()
    # 探针别留在项目里
    for p in (_up, _up_nested, _md, _ca):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    try:
        if os.path.isdir(_nest):
            os.rmdir(_nest)
    except OSError:
        pass
    shutil.rmtree(WORK, ignore_errors=True)

npass = sum(1 for l in R if l.startswith("PASS"))
io = "\n".join(R) + "\n\n===== %d/%d passed =====\n" % (npass, len(R))
open(os.path.join(DIR, "test_server_out.txt"), "w", encoding="utf-8").write(io)
print("wrote test_server_out.txt : %d/%d passed" % (npass, len(R)))
sys.exit(0 if npass == len(R) else 1)
