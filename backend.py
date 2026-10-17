#!/usr/bin/env python3
"""
Wan3.0 / Qwen-Image 本地节点画布 —— 后端代理（仅用 Python 标准库，无需 pip install）

职责：
  1. 静态文件服务（只放行 index.html 与 uploads/ cache/ media/ 三个素材目录）
  2. POST /api/upload     本地图片/视频/音频 -> 落盘 uploads/，返回本地路径
  3. POST /api/generate   提交 wan3.0-video 异步视频任务，返回 task_id + effective_mode
  4. POST /api/genimg     提交 qwen-image 同步生图任务，结果立即落盘 media/
  5. GET  /api/task/<id>  轮询视频任务状态，成片自动缓存到 cache/
  6. GET  /api/config     前端工具条所需的全部元数据 + API Key 状态
  7. 启动时自动清空 uploads/（临时上传素材），media/ 与 cache/ 永久保留

API Key 只存在后端环境变量里，不进浏览器。

—— 关键契约（实测确认，勿改）——
  · wan3.0 视频必须用 input.media 数组 [{type,url}]，image_urls 是 wan2.x 旧字段会报错
  · 必须带 X-DashScope-Async: enable，否则报 "current user api does not support synchronous calls"
  · first_frame/last_frame 与 reference_* 互斥；last_frame 不能脱离 first_frame
  · qwen-image 走 OpenAI 兼容模式，参数平铺在顶层，n 必须是整数，size 用字母 x
"""
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------- 配置

def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

API_KEY = os.environ.get("DASHSCOPE_API_KEY", "").strip()
BASE = os.environ.get("DASHSCOPE_BASE", "https://dashscope.aliyuncs.com").rstrip("/")
MODEL = os.environ.get("WAN_MODEL", "wan3.0-video")
PORT = int(os.environ.get("PORT", "8000"))

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
MEDIA_DIR = os.path.join(BASE_DIR, "media")
for _d in (UPLOAD_DIR, CACHE_DIR, MEDIA_DIR):
    os.makedirs(_d, exist_ok=True)

# 分档上传上限（字节）。官方限制不是一刀切：
#   图片：视频接口 20MB，图像接口 10MB（更严）
#   视频：100MB      音频：15MB
LIMITS = {
    "image": 20 * 1024 * 1024,
    "video": 100 * 1024 * 1024,
    "audio": 15 * 1024 * 1024,
}
IMAGE_API_LIMIT = 10 * 1024 * 1024          # 送 qwen-image 的参考图上限
ALLOWED_EXT = {
    "image": {".jpg", ".jpeg", ".png", ".webp", ".bmp"},
    "video": {".mp4", ".mov"},
    "audio": {".wav", ".mp3"},
}

# 前端工具条要用的元数据，全部在这里改，前端不硬编码
VIDEO_MODELS = [
    {"id": "wan3.0-video", "name": "Wan 3.0"},
    {"id": "wan3.0-video-prime", "name": "Wan 3.0 Prime"},
]
RESOLUTIONS = [
    {"label": "高清", "value": "1080P"},
    {"label": "标准", "value": "720P"},
    {"label": "流畅", "value": "480P"},
]
RATIOS = ["adaptive", "16:9", "4:3", "1:1", "3:4", "9:16"]
IMAGE_MODELS = [
    {"id": "qwen-image-3.0-pro", "name": "Qwen-Image 3.0 Pro", "maxRefs": 3},
    {"id": "qwen-image-3.0", "name": "Qwen-Image 3.0", "maxRefs": 3},
    {"id": "qwen-image-2.1-pro", "name": "Qwen-Image 2.1 Pro", "maxRefs": 10},
    {"id": "qwen-image-2.1-turbo", "name": "Qwen-Image 2.1 Turbo", "maxRefs": 10},
]
IMAGE_SIZES = ["auto", "1024x1024", "1024x1536", "1536x1024", "1440x1440", "1280x720", "720x1280"]

# 图片按「模型 × 输出规格」计价，单位：元/张。in 是每张参考图的输入价。
# 取自百炼控制台价格表 2026-10-17。两个反直觉的点：
#   · qwen-image-3.0 的 1K 与 2K 同价（0.18），只有 3.0-pro 的 2K 翻倍
#   · 3.0 系列按输入张数收费（每张 0.02），2.1 系列不收输入费
DEFAULT_IMAGE_MODEL = "qwen-image-3.0-pro"
IMAGE_COST = {
    "qwen-image-3.0-pro":   {"out": {"1K": 0.25, "2K": 0.50}, "in": 0.02},
    "qwen-image-3.0":       {"out": {"1K": 0.18, "2K": 0.18}, "in": 0.02},
    "qwen-image-2.1-pro":   {"out": {"1K": 0.25, "2K": 0.25}, "in": 0.0},
    "qwen-image-2.1-turbo": {"out": {"1K": 0.10, "2K": 0.10}, "in": 0.0},
}


def size_class(size):
    """把 1024x1536 之类的尺寸归到价格表的 1K / 2K 两档：含 1536 或 2048 算 2K。
    判定必须与前端 imageCost() 里的写法一致，两边算得不一样就会少报。"""
    s = str(size or "")
    return "2K" if ("1536" in s or "2048" in s) else "1K"


def image_row(model):
    """模型认不出就退回默认模型 —— 宁可估算也不让前端拿到 0。"""
    return IMAGE_COST.get(model) or IMAGE_COST[DEFAULT_IMAGE_MODEL]


def image_unit_cost(model, size):
    """每张输出图的单价。"""
    out = image_row(model)["out"]
    return out.get(size_class(size), out["1K"])


def image_ref_cost(model):
    """每张参考图的输入单价（2.1 系列为 0，不收输入费）。"""
    return image_row(model)["in"]


# 视频按「模型 × 分辨率」两档计价 —— Prime 优速版单价是标准版的 2 倍以上，
# 合成一张表会把费用显示少报一半多。单位：元/秒（华东以外地域以控制台为准）。
# 数值取自百炼控制台价格表 2026-10-17：标准版当时仍在 7 折活动期。
DEFAULT_VIDEO_MODEL = "wan3.0-video"
VIDEO_COST = {
    # 标准版：原价 0.3 / 0.6 / 1.2，限时 7 折
    "wan3.0-video": {"480P": 0.21, "720P": 0.42, "1080P": 0.84},
    # Prime 优速版：无折扣
    "wan3.0-video-prime": {"480P": 0.45, "720P": 0.90, "1080P": 1.80},
}


def video_unit_cost(model, resolution):
    """每秒单价。未知模型退回标准版，未知分辨率退回 720P —— 宁可估算也别让前端拿到 0。"""
    table = VIDEO_COST.get(model) or VIDEO_COST[DEFAULT_VIDEO_MODEL]
    return table.get(resolution, table["720P"])


# 官方数量上限
MEDIA_LIMITS = {
    "first_frame": 1, "last_frame": 1,
    "reference_image": 10, "reference_video": 5, "reference_audio": 5,
}
FRAME_TYPES = {"first_frame", "last_frame"}
REF_TYPES = {"reference_image", "reference_video", "reference_audio"}


# ---------------------------------------------------------------- DashScope 调用

class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def ds_headers(extra=None):
    if not API_KEY:
        raise ApiError(401, "服务端未配置 DASHSCOPE_API_KEY。请在项目根目录创建 .env 写入 DASHSCOPE_API_KEY=sk-xxx")
    h = {"Authorization": "Bearer " + API_KEY, "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def ds_request(method, url, payload=None, headers=None, timeout=120):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
            err = parsed.get("error") if isinstance(parsed.get("error"), dict) else {}
            msg = err.get("message") or parsed.get("message") or parsed.get("code") or body
            code = err.get("code") or parsed.get("code") or ""
        except Exception:
            msg, code = body[:400], ""
        if code:
            msg = "%s %s" % (code, msg)
        raise ApiError(e.code, "DashScope 返回 %s: %s" % (e.code, msg))
    except urllib.error.URLError as e:
        raise ApiError(502, "无法连接 DashScope: %s（国内网络请检查直连/代理）" % e.reason)


SERVE_DIRS = {"uploads": UPLOAD_DIR, "cache": CACHE_DIR, "media": MEDIA_DIR}


def ds_body(data):
    """DashScope 原生接口把业务字段放在 data，老 SDK 放在 output，两种外壳都要认。"""
    if not isinstance(data, dict):
        return {}
    for key in ("data", "output"):
        sub = data.get(key)
        if isinstance(sub, dict):
            return sub
    return {}


def file_url_by_id(file_id):
    """file_id -> 可下载 url。文件管理 API 只在北京 Region 开放。"""
    if not API_KEY:
        raise ApiError(401, "服务端未配置 DASHSCOPE_API_KEY")
    data = ds_request(
        "GET",
        BASE + "/api/v1/files/" + urllib.parse.quote(str(file_id), safe=""),
        headers={"Authorization": "Bearer " + API_KEY, "Accept": "application/json"},
    )
    return ds_body(data).get("url")


def resolve_local(ref):
    """前端传来的 /media/xxx.png 之类的浏览器路径 -> 服务器真实路径；已是绝对路径的原样返回。"""
    if not ref:
        return None
    ref = str(ref).replace("\\", "/")
    if ref.startswith("/"):
        parts = ref.lstrip("/").split("/")
        if parts[0] in SERVE_DIRS:
            return os.path.join(SERVE_DIRS[parts[0]], *parts[1:])
    return ref


def upload_to_dashscope(local_path):
    """本地素材 -> DashScope 文件服务，返回可直接喂给 input.media[].url 的地址。图/视/音通用。

    上传接口（POST /api/v1/files）只回 file_id，url 要再用
    GET /api/v1/files/{file_id} 换回来；老结构直接给 url 的也仍然兼容。
    """
    name = os.path.basename(local_path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with open(local_path, "rb") as f:
        blob = f.read()
    b64 = base64.b64encode(blob).decode("ascii")

    boundary = "----wan3" + uuid.uuid4().hex
    parts = [
        ("--%s\r\n" % boundary).encode(),
        (
            'Content-Disposition: form-data; name="file"; filename="%s"\r\n'
            "Content-Type: %s\r\n\r\n" % (name, ctype)
        ).encode(),
        blob,
        b"\r\n",
        ("--%s--\r\n" % boundary).encode(),
    ]

    req = urllib.request.Request(
        BASE + "/api/v1/files",
        data=b"".join(parts),
        headers={
            "Authorization": "Bearer " + API_KEY,
            "X-DashScope-File-Upload": "true",
            "Content-Type": "multipart/form-data; boundary=%s" % boundary,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ApiError(e.code, "素材上传失败 %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:300]))

    body = ds_body(data)

    # 老结构：上传即给 url
    if body.get("url"):
        return body["url"]

    failed = body.get("failed_uploads") or []
    if failed:
        raise ApiError(502, "素材上传被拒绝: %s" % json.dumps(failed, ensure_ascii=False)[:300])

    files = body.get("uploaded_files") or []
    if not files:
        raise ApiError(502, "素材上传未返回 url: %s" % json.dumps(data, ensure_ascii=False)[:300])

    file_id = (files[0] or {}).get("file_id")
    if not file_id:
        raise ApiError(502, "素材上传未返回 file_id: %s" % json.dumps(data, ensure_ascii=False)[:300])

    url = file_url_by_id(file_id)
    if not url:
        raise ApiError(
            502,
            "素材 %s 已上传但查不到 url（file_id=%s）；文件管理 API 仅在北京 Region 开放，"
            "请检查 DASHSCOPE_BASE" % (name, file_id),
        )
    return url


def submit_video(prompt, media, duration, resolution, ratio, audio, model):
    """wan3.0 视频。media 必须是 input.media 数组（wan2.x 的 image_urls 在 3.0 会报错）。"""
    try:
        dur = int(duration)
    except (TypeError, ValueError):
        raise ApiError(400, "duration 必须是整数秒")
    if not 2 <= dur <= 30:
        raise ApiError(400, "duration 需在 2–30 秒之间，当前 %s" % dur)

    params = {
        "duration": dur,
        "resolution": resolution,
        "prompt_extend": True,
    }
    if ratio and ratio != "adaptive":
        params["ratio"] = ratio
    if not audio:
        params["audio"] = False

    payload = {
        "model": model or MODEL,
        "input": {"prompt": prompt},
        "parameters": params,
    }
    if media:
        payload["input"]["media"] = media

    return ds_request(
        "POST",
        BASE + "/api/v1/services/aigc/video-generation/video-synthesis",
        payload,
        ds_headers({"X-DashScope-Async": "enable"}),
    )


def submit_image(prompt, images, model, size, n, negative_prompt, seed, prompt_extend, extend_mode, watermark):
    """
    qwen-image，走 OpenAI 兼容同步接口。
    参数平铺在顶层，没有 input / parameters 嵌套。
    images 为空列表 = 文生图（不传 image 字段）；非空 = 图生图。
    """
    # n 必须是整数。直接 int() 会把 2.5 静默截断成 2、把 "abc" 变成异常值，必须先卡死
    if isinstance(n, bool) or n is None:
        raise ApiError(400, "n 必须是 1–6 的整数")
    if isinstance(n, float) and not float(n).is_integer():
        raise ApiError(400, "n 必须是整数，不能是小数（当前 %s）" % n)
    if isinstance(n, str):
        s = n.strip()
        if not s.isdigit():
            raise ApiError(400, "n 必须是 1–6 的整数（收到字符串 %r）" % n)
        cnt = int(s)
    elif isinstance(n, int):
        cnt = n
    else:
        raise ApiError(400, "n 必须是 1–6 的整数")
    if not 1 <= cnt <= 6:
        raise ApiError(400, "n 需在 1–6 之间，当前 %s" % cnt)

    if size and size != "auto":
        s = str(size).lower()
        if "x" not in s or "*" in s:
            raise ApiError(400, "size 格式应为 1024x1024（用字母 x，不是星号）")
        try:
            w, h = (int(v) for v in s.split("x", 1))
        except ValueError:
            raise ApiError(400, "size 解析失败：%s" % size)
        if not (512 <= w <= 2048 and 512 <= h <= 2048):
            raise ApiError(400, "size 需在 512×512 – 2048×2048 之间，当前 %s" % size)

    payload = {
        "model": model or "qwen-image-3.0-pro",
        "prompt": prompt,
        "size": size or "auto",
        "n": cnt,
        "prompt_extend": bool(prompt_extend),
        "watermark": bool(watermark),
    }
    # agent 模式仅支持文生图，图生图传 agent 官方会 400 —— 这里主动降级
    eff_mode = extend_mode or "direct"
    if images:
        eff_mode = "direct"
    if eff_mode == "agent":
        payload["prompt_extend_mode"] = "agent"
    if negative_prompt and str(model or "").startswith("qwen-image-3.0"):
        payload["negative_prompt"] = negative_prompt
    if seed is not None and seed != "":
        try:
            payload["seed"] = int(seed)
        except (TypeError, ValueError):
            raise ApiError(400, "seed 必须是 0–2147483647 的整数")
    if images:
        # 不传 null 或空数组，否则 400
        payload["image"] = images

    data = ds_request(
        "POST",
        BASE + "/compatible-mode/v1/images/generations",
        payload,
        ds_headers(),
        timeout=300,
    )
    urls = []
    for item in (data.get("data") or []):
        if item.get("url"):
            urls.append(item["url"])
        elif item.get("b64_json"):
            urls.append("data:image/png;base64," + item["b64_json"])
    if not urls:
        raise ApiError(502, "生图未返回结果: %s" % json.dumps(data, ensure_ascii=False)[:300])
    return urls, eff_mode


def query_task(task_id):
    return ds_request("GET", BASE + "/api/v1/tasks/" + task_id, None, ds_headers())


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Wan3Canvas/2.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ---- helpers
    def _send(self, status, obj, ctype="application/json; charset=utf-8", raw=None):
        if raw is None:
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def _cache_remote(self, url, folder, ext):
        """把官方 24 小时就过期的链接抓到本地"""
        if not url:
            return None
        if url.startswith("data:"):
            head, b64 = url.split(",", 1)
            ext = ".png" if "png" in head else ext
            dest = os.path.join(folder, uuid.uuid4().hex + ext)
            with open(dest, "wb") as f:
                f.write(base64.b64decode(b64))
            return "/" + os.path.basename(folder) + "/" + os.path.basename(dest)
        dest = os.path.join(folder, os.path.basename(url.split("?")[0]) or uuid.uuid4().hex)
        if not os.path.splitext(dest)[1]:
            dest += ext
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return "/" + os.path.basename(folder) + "/" + os.path.basename(dest)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wan3-local/2.0"})
            with urllib.request.urlopen(req, timeout=180) as r, open(dest, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            return "/" + os.path.basename(folder) + "/" + os.path.basename(dest)
        except Exception as e:
            self.log_message("cache download failed: %s", e)
            return None

    # ---- routes
    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/config":
                return self._send(200, {
                    "ok": True,
                    "hasKey": bool(API_KEY),
                    "model": MODEL,
                    "videoModels": VIDEO_MODELS,
                    "resolutions": RESOLUTIONS,
                    "ratios": RATIOS,
                    "videoCost": VIDEO_COST,
                    "imageModels": IMAGE_MODELS,
                    "imageSizes": IMAGE_SIZES,
                    "imageCost": IMAGE_COST,
                    "mediaLimits": MEDIA_LIMITS,
                })
            if path.startswith("/api/task/"):
                return self._handle_task(path.rsplit("/", 1)[-1])
            if path.startswith("/api/"):
                return self._send(404, {"ok": False, "error": "unknown api endpoint"})
            return self._serve_static(path)
        except ApiError as e:
            return self._send(e.status, {"ok": False, "error": e.message})
        except Exception as e:
            return self._send(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/upload":
                return self._handle_upload()
            if path == "/api/generate":
                return self._handle_generate()
            if path == "/api/genimg":
                return self._handle_genimg()
            return self._send(404, {"ok": False, "error": "not found"})
        except ApiError as e:
            return self._send(e.status, {"ok": False, "error": e.message})
        except Exception as e:
            return self._send(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

    # ---- handlers
    def _handle_task(self, task_id):
        data = query_task(task_id)
        out = data.get("output") or {}
        video = out.get("video_url")
        cached = None
        if out.get("task_status") == "SUCCEEDED" and video:
            cached = self._cache_remote(video, CACHE_DIR, ".mp4")
        return self._send(200, {
            "ok": True,
            "task_id": task_id,
            "status": out.get("task_status"),
            "message": out.get("message") or out.get("code") or "",
            "video_url": cached or video,
            "usage": data.get("usage") or {},
        })

    def _handle_upload(self):
        b = self._body()
        items = b.get("files") or []
        if not items:
            raise ApiError(400, "没有收到文件")
        strict = bool(b.get("forImage"))   # 送 qwen-image 的参考图，上限更严
        out = []
        for it in items:
            name = (it.get("name") or "ref.png").replace("\\", "/").split("/")[-1]
            kind = it.get("kind") or "image"
            if kind not in LIMITS:
                raise ApiError(400, "未知素材类型 %s" % kind)
            ext = os.path.splitext(name)[1].lower()
            if ext not in ALLOWED_EXT[kind]:
                raise ApiError(400, "%s 不是可用的%s格式（支持 %s）" % (name, kind, "/".join(sorted(ALLOWED_EXT[kind]))))
            data_url = it.get("dataUrl") or ""
            if "," in data_url:
                data_url = data_url.split(",", 1)[1]
            try:
                blob = base64.b64decode(data_url)
            except Exception:
                raise ApiError(400, "%s 解码失败" % name)
            limit = IMAGE_API_LIMIT if (strict and kind == "image") else LIMITS[kind]
            if len(blob) > limit:
                raise ApiError(400, "%s 超过 %.0fMB（%s）" % (name, limit / 1048576.0, "图像接口" if strict else kind))
            saved = os.path.join(UPLOAD_DIR, uuid.uuid4().hex + ext)
            with open(saved, "wb") as f:
                f.write(blob)
            out.append({"name": name, "local": saved, "kind": kind, "size": len(blob)})
        return self._send(200, {"ok": True, "files": out})

    def _resolve_mode(self, b):
        """
        运行时解析实际模式 —— 优先级高于 UI 上显示的模式。
        首尾帧模式只连了首帧 -> 自动降级为首帧生视频。
        返回 (effective_mode, media 输入字典, 降级说明)
        """
        mode = b.get("mode") or "t2v"
        first = b.get("firstFrame")
        last = b.get("lastFrame")
        refs = b.get("refs") or []

        # 官方硬约束：首尾帧与参考素材互斥。正常前端不会同时传，这里先挡一层，避免下游把互斥媒体混装进去
        if (first or last) and refs:
            raise ApiError(400, "首帧/尾帧与参考素材不能同时提交（官方互斥规则），请只保留一种")

        if mode == "omni":
            return "omni", {"refs": refs}, ""
        if mode == "fl":
            if first and last:
                return "fl", {"first": first, "last": last}, ""
            if first:
                return "i2v", {"first": first}, "未连接尾帧，按首帧生视频模式生成"
            return "t2v", {}, "首尾帧模式下未连接任何帧位，已降级为文生视频"
        if mode == "i2v":
            if first:
                return "i2v", {"first": first}, ""
            return "t2v", {}, "首帧生视频模式下未连接首帧，已降级为文生视频"
        # t2v：若仍传了参考素材则退回多参考
        if refs:
            return "omni", {"refs": refs}, "文生视频模式下检测到参考素材，已按多参考模式提交"
        return "t2v", {}, ""

    def _handle_generate(self):
        b = self._body()
        prompt = (b.get("prompt") or "").strip()
        if not prompt:
            raise ApiError(400, "提示词不能为空")

        effective, inp, note = self._resolve_mode(b)
        if note:
            self.log_message("mode downgrade: %s", note)

        media = []
        seen = {}

        def push(kind, ref):
            local = resolve_local(ref)
            if not local or not os.path.exists(local):
                return
            seen[kind] = seen.get(kind, 0) + 1
            cap = MEDIA_LIMITS.get(kind, 1)
            if seen[kind] > cap:
                raise ApiError(400, "%s 最多 %d 个，当前已超" % (kind, cap))
            media.append({"type": kind, "url": upload_to_dashscope(local)})

        if effective == "i2v":
            push("first_frame", inp["first"])
        elif effective == "fl":
            push("first_frame", inp["first"])
            push("last_frame", inp["last"])
        elif effective == "omni":
            for r in inp["refs"]:
                # 前端按来源分好 kind：图片 -> reference_image，视频/音频同理
                if isinstance(r, dict):
                    push("reference_" + (r.get("kind") or "image"), r.get("local"))
                else:
                    push("reference_image", r)

        # 官方硬约束：首尾帧与参考素材互斥
        kinds = {m["type"] for m in media}
        if kinds & FRAME_TYPES and kinds - FRAME_TYPES:
            raise ApiError(400, "首帧/尾帧与参考素材不能同时使用（官方互斥规则）")
        # 尾帧不能脱离首帧
        if "last_frame" in kinds and "first_frame" not in kinds:
            raise ApiError(400, "尾帧不能脱离首帧单独存在")

        # 计价要用「实际提交的模型」，否则前端没传 model 时会按 wan3.0-video 算，Prime 就少报了
        eff_model = b.get("model") or MODEL
        eff_res = b.get("resolution") or "720P"
        data = submit_video(
            prompt=prompt,
            media=media,
            duration=b.get("duration", 5),
            resolution=b.get("resolution", "720P"),
            ratio=b.get("ratio", "adaptive"),
            audio=bool(b.get("audio", True)),
            model=eff_model,
        )
        out = data.get("output") or {}
        if not out.get("task_id"):
            raise ApiError(502, "提交未返回 task_id: %s" % json.dumps(data, ensure_ascii=False)[:300])
        cost = video_unit_cost(eff_model, eff_res)
        return self._send(200, {
            "ok": True,
            "task_id": out["task_id"],
            "status": out.get("task_status"),
            "media_count": len(media),
            "media_types": [m["type"] for m in media],
            "requested_mode": b.get("mode") or "t2v",
            "effective_mode": effective,
            "note": note,
            "estimated_cost": round(cost * int(b.get("duration", 5)), 2),
        })

    def _handle_genimg(self):
        b = self._body()
        prompt = (b.get("prompt") or "").strip()
        if not prompt:
            raise ApiError(400, "提示词不能为空")

        model = b.get("model") or "qwen-image-3.0-pro"
        max_refs = 3 if "3.0" in model else 10
        refs = []
        for r in (b.get("refs") or []):
            local = resolve_local(r)
            if local and os.path.exists(local):
                refs.append(local)
        if len(refs) > max_refs:
            raise ApiError(400, "%s 最多接受 %d 张参考图，当前 %d 张" % (model, max_refs, len(refs)))
        for r in refs:
            if os.path.getsize(r) > IMAGE_API_LIMIT:
                raise ApiError(400, "%s 超过图像接口 10MB 上限" % os.path.basename(r))

        urls = []
        if refs:
            for r in refs:
                urls.append(upload_to_dashscope(r))

        remote, eff_mode = submit_image(
            prompt=prompt,
            images=urls,
            model=model,
            size=b.get("size") or "auto",
            n=b.get("n", 1),
            negative_prompt=b.get("negativePrompt"),
            seed=b.get("seed"),
            prompt_extend=b.get("promptExtend", True),
            extend_mode=b.get("promptExtendMode"),
            watermark=b.get("watermark", False),
        )
        # 官方链接 24 小时过期，必须立刻落盘
        saved = [p for p in (self._cache_remote(u, MEDIA_DIR, ".png") for u in remote) if p]
        # 估价按「提交的 n」算，不是 len(saved)：图已经生成出来并计费了，
        # 后面下载落盘失败不影响扣费，用 len(saved) 会把费用报低。
        # 输出按张算，参考图另按张收输入费（2.1 系列为 0）
        try:
            want = max(1, int(b.get("n", 1)))
        except (TypeError, ValueError):
            want = 1
        est = image_unit_cost(model, b.get("size")) * want + image_ref_cost(model) * len(refs)
        return self._send(200, {
            "ok": True,
            "images": saved,
            "count": len(saved),
            "model": model,
            "size_class": size_class(b.get("size")),
            "effective_prompt_extend_mode": eff_mode,
            "note": "图生图不支持 agent 增强，已自动降级为 direct" if (refs and (b.get("promptExtendMode") == "agent")) else "",
            "estimated_cost": round(est, 2),
        })

    # 只放行 index.html 和三个素材目录，.env / .py 一律不供
    STATIC_ALLOW = {"index.html", "favicon.ico", "README.md"}

    def _serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        safe = os.path.normpath(path.lstrip("/")).replace("\\", "/")
        if safe.startswith("..") or safe.startswith("/"):
            return self._send(403, {"ok": False, "error": "forbidden"})
        head = safe.split("/")[0]
        if safe not in self.STATIC_ALLOW and head not in ("uploads", "cache", "media"):
            return self._send(403, {"ok": False, "error": "forbidden"})
        full = os.path.join(BASE_DIR, safe)
        if not os.path.isfile(full):
            return self._send(404, {"ok": False, "error": "not found"})
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            raw = f.read()
        return self._send(200, None, ctype=ctype, raw=raw)


def purge_uploads(target=None):
    """清空 uploads/，返回 (文件数, 释放字节)。

    页面上传的只是本次会话的临时素材（做首尾帧/参考图），刷新页面就没用了，
    长期堆积白占磁盘。media/（花钱生成的成片）和 cache/（轮询缓存）绝不碰。

    单独抽出来是为了能传 target 给测试，不许测试碰真实的 uploads/。
    """
    target = target or UPLOAD_DIR
    n = total = 0
    for root, _dirs, files in os.walk(target, topdown=False):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                total += os.path.getsize(p)
                os.unlink(p)
                n += 1
            except OSError:
                pass          # 被占用或无权限就跳过，绝不让清理失败拖垮启动
        if root != target:
            try:
                os.rmdir(root)   # 只删空目录，有残留的无所谓
            except OSError:
                pass
    return n, total


def main():
    # 顺序很关键：先占端口再清 uploads/。
    # 万一重复启动，端口占用会在上面直接抛错退出，不会把已跑着的实例的素材删掉。
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)

    if os.environ.get("KEEP_UPLOADS", "").strip().lower() in ("1", "true", "yes"):
        print("\n  uploads/ 保留（.env 设了 KEEP_UPLOADS=1）")
    else:
        n, freed = purge_uploads()
        if n:
            print("\n  已清空 uploads/：%d 个文件，%.2f MB（media/ 与 cache/ 保留）"
                  % (n, freed / 1048576.0))

    print("\n  Wan3.0 节点画布创作台  ->  http://127.0.0.1:%d" % PORT)
    print("  视频模型: %s" % MODEL)
    print("  图像模型: %s" % IMAGE_MODELS[0]["id"])
    print("  API Key: %s\n" % ("已配置 (%s...)" % API_KEY[:6] if API_KEY else "未配置 —— 请在 .env 写入 DASHSCOPE_API_KEY"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  bye")


if __name__ == "__main__":
    main()
