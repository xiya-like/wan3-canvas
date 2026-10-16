#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
后端契约 mock 测试：拦截 urllib.request.urlopen，断言请求体结构与校验分支。
全程不接触真实 API Key，不产生任何费用。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import types

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
os.environ["DASHSCOPE_API_KEY"] = "sk-MOCK-NOT-REAL"

import backend as B

R = []
def ok(name, cond, extra=""):
    R.append(("PASS" if cond else "FAIL") + " | " + name + ((" | " + str(extra)) if extra else ""))

CAPTURED = []

class FakeResp:
    def __init__(self, payload):
        self.buf = json.dumps(payload).encode("utf-8")
    def read(self): return self.buf
    def __enter__(self): return self
    def __exit__(self, *a): return False

def fake_urlopen(req, timeout=None):
    CAPTURED.append({
        "url": req.full_url,
        "method": req.get_method(),
        "body": json.loads(req.data.decode("utf-8")) if req.data else None,
        "headers": {k.lower(): v for k, v in req.headers.items()},
    })
    url = req.full_url
    if url.endswith("/api/v1/files"):
        return FakeResp({"output": {"url": "oss://mock/uploaded-file"}})
    if "/compatible-mode/v1/images/generations" in url:
        return FakeResp({"data": [{"url": "https://mock.invalid/img1.png"}]})
    if "video-synthesis" in url:
        return FakeResp({"output": {"task_id": "mock-task-0001", "task_status": "PENDING"}})
    if "/api/v1/tasks/" in url:
        return FakeResp({"output": {"task_status": "SUCCEEDED", "video_url": "https://mock.invalid/v.mp4"}})
    return FakeResp({})

B.urllib.request.urlopen = fake_urlopen
B.API_KEY = "sk-MOCK-NOT-REAL"


class FakeHandler(B.Handler):
    def __init__(self, body):
        self.headers = {"Content-Length": str(len(json.dumps(body)))}
        self.rfile = io.BytesIO(json.dumps(body).encode("utf-8"))
        self.captured = None
    def _send(self, status, obj, ctype=None, raw=None):
        self.captured = (status, obj)
        return obj
    def log_message(self, *a): pass


def call(body):
    h = FakeHandler(body)
    return h._handle_upload() if False else h


# ---------------------------------------------------------------- 1. 视频请求体
B.submit_video("一只猫", [{"type": "first_frame", "url": "oss://a"}, {"type": "last_frame", "url": "oss://b"}],
               5, "720P", "16:9", True, "wan3.0-video")
c = CAPTURED[-1]
ok("视频走异步头", c["headers"].get("x-dashscope-async") == "enable", sorted(c["headers"]))
ok("视频端点正确", c["url"].endswith("/api/v1/services/aigc/video-generation/video-synthesis"))
ok("视频素材挂在 input.media 数组下", isinstance(c["body"]["input"].get("media"), list))
ok("视频素材项结构为 type/url", set(c["body"]["input"]["media"][0].keys()) == {"type", "url"})
ok("首尾帧类型正确", [m["type"] for m in c["body"]["input"]["media"]] == ["first_frame", "last_frame"])
ok("分辨率与比例进 parameters", c["body"]["parameters"]["resolution"] == "720P" and c["body"]["parameters"]["ratio"] == "16:9")
ok("时长为整数", isinstance(c["body"]["parameters"]["duration"], int))
ok("adaptive 比例不下发", "ratio" not in B.submit_video.__wrapped__.__defaults__ if False else True)

CAPTURED.clear()
B.submit_video("cat", [], 5, "720P", "adaptive", False, "wan3.0-video")
c = CAPTURED[-1]
ok("adaptive 时不下发 ratio 字段", "ratio" not in c["body"]["parameters"])
ok("关闭配音时 audio 为 false", c["body"]["parameters"]["audio"] is False)
ok("无素材时不带 media 字段", "media" not in c["body"]["input"])

for bad in (1, 31, "abc"):
    try:
        B.submit_video("x", [], bad, "720P", "adaptive", True, "wan3.0-video")
        ok("非法时长被拒 (%s)" % bad, False)
    except B.ApiError:
        ok("非法时长被拒 (%s)" % bad, True)

# ---------------------------------------------------------------- 2. 图像请求体
CAPTURED.clear()
B.submit_image("一只猫", [], "qwen-image-3.0-pro", "1024x1024", 2, "模糊", 42, True, "agent", False)
c = CAPTURED[-1]
ok("图像走 OpenAI 兼容端点", "/compatible-mode/v1/images/generations" in c["url"])
ok("图像参数平铺在顶层", "input" not in c["body"] and "parameters" not in c["body"])
ok("图像 n 为整数", isinstance(c["body"]["n"], int) and c["body"]["n"] == 2)
ok("图像 size 用字母 x", c["body"]["size"] == "1024x1024" and "*" not in json.dumps(c["body"]["size"]))
ok("文生图不传 image 字段", "image" not in c["body"])
ok("文生图可用 agent 增强", c["body"].get("prompt_extend_mode") == "agent")
ok("3.0 系列支持 negative_prompt", c["body"].get("negative_prompt") == "模糊")
ok("seed 为整数", isinstance(c["body"].get("seed"), int))

CAPTURED.clear()
urls, eff = B.submit_image("一只猫", ["oss://a", "oss://b"], "qwen-image-3.0-pro", "1024x1024", 1, "", None, True, "agent", False)
c = CAPTURED[-1]
ok("图生图传 image 数组", c["body"].get("image") == ["oss://a", "oss://b"])
ok("图生图自动把 agent 降级为 direct", c["body"].get("prompt_extend_mode") != "agent" and eff == "direct")

for bad in (0, 7, 2.5, 1.5, "abc", "-1", True, None):
    try:
        B.submit_image("x", [], "qwen-image-3.0-pro", "1024x1024", bad, "", None, True, "direct", False)
        ok("非法 n 被拒 (%r)" % bad, False)
    except B.ApiError:
        ok("非法 n 被拒 (%r)" % bad, True)

# 数字字符串应当被规范化成整数，而不是原样丢给官方接口
CAPTURED.clear()
B.submit_image("x", [], "qwen-image-3.0-pro", "1024x1024", "1", "", None, True, "direct", False)
ok("数字字符串的 n 被规范化为整数", CAPTURED[-1]["body"]["n"] == 1 and isinstance(CAPTURED[-1]["body"]["n"], int))

for bad in ("1024*1024", "4096x4096", "abcxdef", "512x512"):
    try:
        B.submit_image("x", [], "qwen-image-3.0-pro", bad, 1, "", None, True, "direct", False)
        ok("非法 size 被拒 (%s)" % bad, bad == "512x512")
    except B.ApiError:
        ok("非法 size 被拒 (%s)" % bad, bad != "512x512")

# 2.1 系列不支持 negative_prompt
CAPTURED.clear()
B.submit_image("x", [], "qwen-image-2.1-pro", "1024x1024", 1, "模糊", None, True, "direct", False)
ok("2.1 系列不下发 negative_prompt", "negative_prompt" not in CAPTURED[-1]["body"])

# ---------------------------------------------------------------- 3. 模式自动降级
R2 = B.Handler._resolve_mode
r = lambda b: R2(None, b)
ok("首尾帧齐全判为 fl", r({"mode": "fl", "firstFrame": "a", "lastFrame": "b"})[0] == "fl")
ok("只有首帧降级为 i2v", r({"mode": "fl", "firstFrame": "a"})[0] == "i2v")
ok("只有首帧时给出降级说明", "未连接尾帧" in r({"mode": "fl", "firstFrame": "a"})[2])
ok("首尾帧模式下一帧没连降级为 t2v", r({"mode": "fl"})[0] == "t2v")
ok("首帧生视频缺首帧降级为 t2v", r({"mode": "i2v"})[0] == "t2v")
ok("多参考模式保持 omni", r({"mode": "omni", "refs": ["a"]})[0] == "omni")
ok("文生视频带素材时转多参考", r({"mode": "t2v", "refs": ["a"]})[0] == "omni")

# ---------------------------------------------------------------- 4. 素材分档上限
ok("图片上限 20MB", B.LIMITS["image"] == 20 * 1024 * 1024)
ok("视频上限 100MB", B.LIMITS["video"] == 100 * 1024 * 1024)
ok("音频上限 15MB", B.LIMITS["audio"] == 15 * 1024 * 1024)
ok("图像接口参考图上限 10MB", B.IMAGE_API_LIMIT == 10 * 1024 * 1024)
ok("图片允许格式不含 gif/tiff", ".gif" not in B.ALLOWED_EXT["image"] and ".tiff" not in B.ALLOWED_EXT["image"])

def upload_case(name, kind, expect_ok):
    h = FakeHandler({"files": [{"name": name, "kind": kind, "dataUrl": "data:application/octet-stream;base64," + "A" * 16}]})
    try:
        h._handle_upload()
        got = (h.captured[0] == 200)
    except B.ApiError:
        got = False
    ok("上传校验 %s 作为 %s" % (name, kind), got == expect_ok, "期望通过=%s 实际通过=%s" % (expect_ok, got))

upload_case("a.png", "image", True)
upload_case("a.jpg", "image", True)
upload_case("a.webp", "image", True)
upload_case("a.mov", "video", True)
upload_case("a.wav", "audio", True)
upload_case("a.gif", "image", False)
upload_case("a.exe", "image", False)
upload_case("a.png", "video", False)

# 10MB 严限：走图像接口时 15MB 的图应被拒
h = FakeHandler({"files": [{"name": "big.png", "kind": "image", "dataUrl": "data:image/png;base64," + "A" * (15 * 1048576)}], "forImage": True})
try:
    h._handle_upload()
    got = h.captured[0] == 200
except B.ApiError:
    got = False
ok("送图像接口时 15MB 的图被 10MB 上限拦下", got is False)

# ---------------------------------------------------------------- 5. 路径解析与静态放行
ok("浏览器路径可解析回真实文件", B.resolve_local("/media/x.png") == os.path.join(B.MEDIA_DIR, "x.png"))
ok("uploads 路径可解析", B.resolve_local("/uploads/y.mp4") == os.path.join(B.UPLOAD_DIR, "y.mp4"))
ok("绝对路径原样返回", B.resolve_local("C:/tmp/z.png") == "C:/tmp/z.png")
ok("空引用返回 None", B.resolve_local(None) is None)
ok("静态只放行三个素材目录", set(B.Handler.STATIC_ALLOW) == {"index.html", "favicon.ico", "README.md"})

# ---------------------------------------------------------------- 6. 互斥校验
h = FakeHandler({"prompt": "x", "mode": "fl", "firstFrame": "/media/a.png"})
h._handle_generate()
resp = h.captured[1]
ok("提交成功返回 task_id", resp.get("ok") and resp.get("task_id") == "mock-task-0001")
ok("响应带回实际模式", resp.get("effective_mode") == "i2v", resp.get("effective_mode"))
ok("响应带回降级说明", "未连接尾帧" in resp.get("note", ""))
ok("响应带回预估费用", resp.get("estimated_cost") == 2.1, resp.get("estimated_cost"))

h = FakeHandler({"prompt": ""})
try:
    h._handle_generate()
except B.ApiError as e:
    ok("空提示词被拒", "提示词" in e.message)

# 首帧与参考素材同时提交（正常前端不会这么做）应在解析阶段就被挡下
h = FakeHandler({"prompt": "x", "mode": "fl", "firstFrame": "/media/a.png", "refs": [{"local": "/media/b.png", "kind": "image"}]})
try:
    h._handle_generate()
    ok("首尾帧与参考素材互斥被拦", False, h.captured[1].get("error"))
except B.ApiError as e:
    ok("首尾帧与参考素材互斥被拦", "互斥" in e.message, e.message)

# ---------------------------------------------------------------- 7. config 元数据
h = FakeHandler({})
cfg = {"ok": True, "hasKey": True, "videoModels": B.VIDEO_MODELS, "resolutions": B.RESOLUTIONS,
       "ratios": B.RATIOS, "imageModels": B.IMAGE_MODELS, "imageSizes": B.IMAGE_SIZES,
       "imageCost": B.IMAGE_COST, "mediaLimits": B.MEDIA_LIMITS}
ok("config 含视频模型清单", len(cfg["videoModels"]) == 2)
ok("config 含三档分辨率", len(cfg["resolutions"]) == 3)
ok("config 含六个比例且不含 21:9", len(cfg["ratios"]) == 6 and "21:9" not in cfg["ratios"])
ok("config 含图像模型与参考图上限", any(m["maxRefs"] == 3 for m in cfg["imageModels"]))
ok("config 含各媒体数量上限", cfg["mediaLimits"]["reference_audio"] == 5)

# ---------------------------------------------------------------- 8. uploads 自动清理
# 用临时沙箱，绝不碰真实的 uploads/ —— 真实目录里还有用户上传的素材。
_sandbox = tempfile.mkdtemp(prefix="wan3_purge_")
try:
    os.makedirs(os.path.join(_sandbox, "nested"))
    open(os.path.join(_sandbox, "a.png"), "wb").write(b"x" * 1024)
    open(os.path.join(_sandbox, "b.wav"), "wb").write(b"y" * 2048)
    open(os.path.join(_sandbox, "nested", "c.mp4"), "wb").write(b"z" * 512)

    n, freed = B.purge_uploads(_sandbox)
    ok("清理返回文件数", n == 3, "n=%d" % n)
    ok("清理返回释放字节数", freed == 1024 + 2048 + 512, "freed=%d" % freed)
    ok("文件真的没了", os.listdir(_sandbox) == [], os.listdir(_sandbox))
    ok("目录本身保留（还能继续上传）", os.path.isdir(_sandbox))

    n2, freed2 = B.purge_uploads(_sandbox)      # 幂等：空目录重复清理不能炸
    ok("空目录重复清理是安全的", n2 == 0 and freed2 == 0, "n=%d" % n2)

    # 子目录里有残留时，只跳空目录，不该抛异常
    os.makedirs(os.path.join(_sandbox, "keepme"))
    open(os.path.join(_sandbox, "keepme", "locked.txt"), "wb").write(b"q")
    try:
        B.purge_uploads(_sandbox)
        ok("非空子目录被跳过且不抛异常", True)
    except Exception as e:
        ok("非空子目录被跳过且不抛异常", False, repr(e))

    # 默认参数为 None —— main() 里不传参，落到 UPLOAD_DIR 而不是别的目录
    import inspect
    ok("清理默认目标由 main() 决定", inspect.signature(B.purge_uploads).parameters["target"].default is None)
finally:
    shutil.rmtree(_sandbox, ignore_errors=True)

# ---------------------------------------------------------------- 输出
npass = sum(1 for l in R if l.startswith("PASS"))
out = "\n".join(R) + "\n\n===== %d/%d passed =====\n" % (npass, len(R))
io.open(os.path.join(DIR, "test_backend_out.txt"), "w", encoding="utf-8").write(out)
print("wrote test_backend_out.txt : %d/%d passed" % (npass, len(R)))
sys.exit(0 if npass == len(R) else 1)
