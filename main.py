import os
import time
import asyncio
import uuid
import logging
import random
import aiofiles
import httpx
from fastapi import FastAPI, Request, BackgroundTasks, File, UploadFile, Form, Body
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google import genai 

# ================= 🔧 核心配置 (已硬编码你的信息) =================
# 即使 Render 环境变量没配，这些默认值也会生效
APIMART_API_KEY = os.getenv("APIMART_API_KEY", "sk-ibdAt5NPqtNkzuBFonlTmr6lynjIYAl5YFTfhzdflBnefMp3")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "AIzaSyDKfG2kMGOOSm_e_voQRVhBpnXDM_h3rB8")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://cheng-lan-aidao-yan.onrender.com")

# 目录配置
UPLOAD_DIR = "static/uploads"
VIDEO_DIR = "static/videos"
for path in [UPLOAD_DIR, VIDEO_DIR]:
    os.makedirs(path, exist_ok=True)

# 日志与应用初始化
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("HaloNet")
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Google Client (备用)
google_client = genai.Client(api_key=GOOGLE_API_KEY)

# ================= 🧠 内存数据库 =================
# 会话: { "uuid": { "status": "waiting/processing/ready/playing", "video_url": "..." } }
SESSIONS = {}
# 虚拟屏幕: { "screen_id": { lat, lon, status, price } }
VIRTUAL_SCREENS = {}

# ================= 🏭 AI 核心工厂 (Sora) =================
class MediaFactory:
    def __init__(self):
        self.sora_api_url = "https://api.apimart.ai/v1/videos/generations"
        self.sora_task_url = "https://api.apimart.ai/v1/tasks"
        self.headers = {
            "Authorization": f"Bearer {APIMART_API_KEY}",
            "Content-Type": "application/json"
        }

    async def execute_workflow(self, session_id: str, local_img_path: str):
        """执行全链路：上传 -> URL化 -> Sora生成 -> 状态更新"""
        try:
            SESSIONS[session_id]["status"] = "processing"
            logger.info(f"[{session_id}] 🚀 收到图片，开始处理...")

            # 1. 构建公网图片地址 (Sora 必须能访问)
            # 移除开头的 static/ 因为 mount 路径问题，或者保留相对路径
            # 这里的逻辑是：如果文件在 static/uploads/x.jpg，URL就是 PUBLIC_URL/static/uploads/x.jpg
            relative_path = local_img_path.replace("\\", "/")
            public_img_url = f"{PUBLIC_BASE_URL.rstrip('/')}/{relative_path}"
            
            logger.info(f"[{session_id}] 🔗 素材公网地址: {public_img_url}")

            # 2. 提交 Sora 任务
            payload = {
                "model": "sora-2-pro",
                "prompt": "Cinematic product shot, 4k, hyper-realistic, commercial lighting, slow motion, 16:9 aspect ratio.",
                "image_urls": [public_img_url],
                "duration": 5, # Demo 5秒
                "aspect_ratio": "16:9"
            }

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(self.sora_api_url, json=payload, headers=self.headers)
                data = resp.json()
                
                task_id = None
                if data.get('code') == 200:
                    task_data = data.get('data')
                    task_id = task_data[0].get('task_id') if isinstance(task_data, list) else task_data.get('task_id')

                if not task_id:
                    raise Exception(f"任务提交失败: {data}")

                logger.info(f"[{session_id}] ✅ Sora ID: {task_id} (等待生成...)")

                # 3. 轮询状态
                video_url = None
                for _ in range(60): # 5分钟超时
                    await asyncio.sleep(5)
                    try:
                        check_resp = await client.get(f"{self.sora_task_url}/{task_id}", headers=self.headers)
                        if check_resp.status_code != 200: continue
                        
                        res_data = check_resp.json().get('data', {})
                        status = res_data.get('status')

                        if status == 'completed':
                            videos = res_data.get('result', {}).get('videos', [])
                            if videos:
                                video_url = videos[0].get('url')[0]
                            break
                        elif status == 'failed':
                            raise Exception("Sora Task Failed")
                    except: pass

            if video_url:
                # 4. 下载视频到本地
                filename = f"final_{session_id}.mp4"
                local_vid_path = os.path.join(VIDEO_DIR, filename)
                async with httpx.AsyncClient() as dl:
                    r = await dl.get(video_url)
                    async with aiofiles.open(local_vid_path, 'wb') as f:
                        await f.write(r.content)
                
                # 5. 标记为 Ready (等待用户在地图上点击投放)
                SESSIONS[session_id]["video_url"] = f"/static/videos/{filename}"
                SESSIONS[session_id]["status"] = "ready"
                logger.info(f"[{session_id}] ✨ 视频就绪，等待用户投放指令")
            else:
                raise Exception("未获取到视频链接")

        except Exception as e:
            logger.error(f"[{session_id}] ❌ 流程错误: {e}")
            SESSIONS[session_id]["status"] = "failed"

media_factory = MediaFactory()

# ================= 🌍 LBS 模拟器 =================
def generate_fake_screens(lat, lon, count=200):
    """生成虚拟屏幕"""
    global VIRTUAL_SCREENS
    VIRTUAL_SCREENS = {} # 简单起见，每次刷新清空旧的
    screens = []
    
    for _ in range(count):
        # 1度 ≈ 111km -> 0.015 ≈ 1.6km
        offset_lat = random.uniform(-0.015, 0.015)
        offset_lon = random.uniform(-0.015, 0.015)
        sid = f"scr_{uuid.uuid4().hex[:4]}"
        
        s = {
            "id": sid,
            "lat": float(lat) + offset_lat,
            "lon": float(lon) + offset_lon,
            "price": round(random.uniform(0.5, 3.0), 1),
            "status": "idle"
        }
        screens.append(s)
        VIRTUAL_SCREENS[sid] = s
    return screens

# ================= 🚦 路由接口 =================

@app.get("/", response_class=HTMLResponse)
async def pc_index(request: Request):
    """PC端：展示二维码"""
    sid = str(uuid.uuid4())[:8]
    SESSIONS[sid] = {"status": "waiting", "video_url": None}
    
    # 二维码指向手机上传页
    mobile_url = f"{PUBLIC_BASE_URL.rstrip('/')}/mobile/{sid}"
    
    return templates.TemplateResponse("index.html", {
        "request": request, "session_id": sid, "mobile_url": mobile_url
    })

@app.get("/mobile/{session_id}", response_class=HTMLResponse)
async def mobile_upload_page(request: Request, session_id: str):
    """手机端：Step 1 上传"""
    if session_id not in SESSIONS:
        return HTMLResponse("Session Not Found")
    return templates.TemplateResponse("mobile_upload.html", {"request": request, "session_id": session_id})

@app.get("/mobile/map/{session_id}", response_class=HTMLResponse)
async def mobile_map_page(request: Request, session_id: str):
    """手机端：Step 2 地图投放"""
    return templates.TemplateResponse("mobile_map.html", {"request": request, "session_id": session_id})

# --- API ---

@app.post("/api/upload")
async def api_upload(bg_tasks: BackgroundTasks, file: UploadFile = File(...), session_id: str = Form(...)):
    """接收图片 -> 保存 -> 触发AI -> 返回"""
    if session_id not in SESSIONS:
        return JSONResponse(status_code=400, content={"error": "invalid session"})
    
    # 保存图片
    ext = file.filename.split('.')[-1] if '.' in file.filename else "jpg"
    filename = f"{session_id}.{ext}"
    path = os.path.join(UPLOAD_DIR, filename)
    
    async with aiofiles.open(path, 'wb') as f:
        await f.write(await file.read())
        
    # 后台启动 Sora
    bg_tasks.add_task(media_factory.execute_workflow, session_id, path)
    
    # 告诉前端跳转到地图页
    return {"status": "ok", "next_url": f"/mobile/map/{session_id}"}

@app.get("/api/status/{session_id}")
async def api_status(session_id: str):
    """轮询接口"""
    return SESSIONS.get(session_id, {"status": "expired"})

@app.get("/api/lbs/nearby")
async def api_lbs(lat: float, lon: float):
    """获取附近的虚拟屏幕"""
    data = generate_fake_screens(lat, lon)
    return {"code": 200, "data": data}

@app.post("/api/broadcast")
async def api_broadcast(payload: dict = Body(...)):
    """投放指令: 地图点击 -> 更新所有状态"""
    sid = payload.get("session_id")
    screen_ids = payload.get("screen_ids", [])
    
    if sid in SESSIONS and SESSIONS[sid]["video_url"]:
        # PC端检测到 playing 会自动播放
        SESSIONS[sid]["status"] = "playing" 
        
        # 更新虚拟屏幕状态
        for scr_id in screen_ids:
            if scr_id in VIRTUAL_SCREENS:
                VIRTUAL_SCREENS[scr_id]["status"] = "playing"
                
        logger.info(f"[{sid}] 📡 BROADCAST: 投放到 {len(screen_ids)} 个屏幕")
        return {"code": 200, "msg": "success"}
    
    return {"code": 400, "msg": "not ready"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"⚡ Halo-Net 启动: {PUBLIC_BASE_URL} (Port: {port})")
    uvicorn.run(app, host="0.0.0.0", port=port)