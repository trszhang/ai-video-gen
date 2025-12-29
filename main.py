import os
import time
import asyncio
import uuid
import logging
import aiofiles
import httpx
from fastapi import FastAPI, Request, BackgroundTasks, File, UploadFile, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# --- 配置初始化 ---
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DirectorAI")

# ================= 核心配置 (已注入你的Key) =================
# 即使没有 .env 文件，这些默认值也能让程序直接运行
APIMART_API_KEY = os.getenv("APIMART_API_KEY", "sk-ibdAt5NPqtNkzuBFonlTmr6lynjIYAl5YFTfhzdflBnefMp3")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "AIzaSyDKfG2kMGOOSm_e_voQRVhBpnXDM_h3rB8")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://cheng-lan-aidao-yan.onrender.com")
FALLBACK_IMAGE_URL = "https://static.uganda-coffee.com/coffee/20250302/mbEsGl0Lmep58MlTkLoHFszXgk0UTW8El3AkE0PuK0ZAKTXDx2RpfrmcRXXSMmrU."

# ================= 📂 目录配置 =================
UPLOAD_DIR = "static/uploads"
VIDEO_DIR = "static/videos"
for path in [UPLOAD_DIR, VIDEO_DIR]:
    os.makedirs(path, exist_ok=True)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- 🧠 会话状态存储 (内存版) ---
# 结构: { "session_id": { "status": "waiting", "video_url": None, "msg": "..." } }
SESSIONS = {}

# --- 🏭 核心工厂类 (Sora处理逻辑) ---
class MediaFactory:
    def __init__(self):
        self.api_url = "https://api.apimart.ai/v1/videos/generations"
        self.task_url = "https://api.apimart.ai/v1/tasks"
        self.headers = {
            "Authorization": f"Bearer {APIMART_API_KEY}",
            "Content-Type": "application/json"
        }

    async def execute_workflow(self, session_id: str, local_img_path: str):
        """执行生成任务并更新 Session 状态"""
        try:
            SESSIONS[session_id]["status"] = "processing"
            logger.info(f"[{session_id}] 🚀 任务启动: Sora Direct Mode")

            # 1. 构建公网图片地址 (供 Sora 读取)
            # 必须确保这个 URL 是外部可访问的
            relative_path = local_img_path.replace("\\", "/")
            # 移除开头的 static/ 因为它已经在相对路径里了，如果不重复就保留
            # 假设 local_img_path 是 "static/uploads/xxx.jpg"
            # 且 PUBLIC_BASE_URL 是 https://cheng-lan...
            # 最终 URL 应该是 https://cheng-lan.../static/uploads/xxx.jpg
            public_img_url = f"{PUBLIC_BASE_URL.rstrip('/')}/{relative_path}"
            
            logger.info(f"[{session_id}] 🔗 图片公网地址: {public_img_url}")

            # 2. 提交任务给 APIMart
            payload = {
                "model": "sora-2-pro",
                "prompt": "High quality cinematic commercial, product shot, 15s duration, 16:9 aspect ratio, smooth camera movement, 4k resolution, hyper-realistic, professional lighting.",
                "image_urls": [public_img_url],
                "duration": 5, # Demo用5秒省点钱，正式可用15
                "aspect_ratio": "16:9"
            }

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(self.api_url, json=payload, headers=self.headers)
                data = resp.json()
                
                task_id = None
                if data.get('code') == 200:
                    task_data = data.get('data')
                    # 兼容 APIMart 可能返回 list 或 dict
                    task_id = task_data[0].get('task_id') if isinstance(task_data, list) else task_data.get('task_id')

                if not task_id:
                    raise Exception(f"任务提交失败，API返回: {data}")

                logger.info(f"[{session_id}] ✅ 任务提交成功 ID: {task_id}")

                # 3. 轮询状态 (最多等待5分钟)
                video_url = None
                for _ in range(60): # 60 * 5s = 300s
                    await asyncio.sleep(5)
                    try:
                        check_resp = await client.get(f"{self.task_url}/{task_id}", headers=self.headers)
                        if check_resp.status_code != 200: continue
                        
                        res_data = check_resp.json().get('data', {})
                        status = res_data.get('status')

                        if status == 'completed':
                            videos = res_data.get('result', {}).get('videos', [])
                            if videos:
                                video_url = videos[0].get('url')[0]
                            break
                        elif status == 'failed':
                            raise Exception("Sora 任务状态返回 failed")
                    except Exception as poll_e:
                        logger.warning(f"轮询瞬时错误: {poll_e}")

            if video_url:
                # 4. 下载视频到本地 (避免链接过期或跨域问题)
                logger.info(f"[{session_id}] ⬇️ 下载视频中...")
                filename = f"video_{session_id}.mp4"
                local_video_path = os.path.join(VIDEO_DIR, filename)
                
                async with httpx.AsyncClient(timeout=120) as dl_client:
                    v_resp = await dl_client.get(video_url)
                    async with aiofiles.open(local_video_path, 'wb') as f:
                        await f.write(v_resp.content)
                
                # 更新状态为完成，前端通过这个 URL 播放
                SESSIONS[session_id]["video_url"] = f"/static/videos/{filename}"
                SESSIONS[session_id]["status"] = "completed"
                logger.info(f"[{session_id}] 🎉 流程全部完成!")
            else:
                raise Exception("轮询超时或未获取到视频链接")

        except Exception as e:
            logger.error(f"[{session_id}] ❌ 错误: {e}")
            SESSIONS[session_id]["status"] = "failed"
            SESSIONS[session_id]["msg"] = str(e)

media_factory = MediaFactory()

# --- 🚦 路由控制 ---

@app.get("/", response_class=HTMLResponse)
async def pc_index(request: Request):
    """PC端主页: 生成一个新的 Session ID"""
    new_sid = str(uuid.uuid4())[:8] # 生成简短ID
    SESSIONS[new_sid] = {"status": "waiting", "video_url": None}
    
    # 构造手机扫码的 URL，指向本服务的 mobile 页面
    mobile_url = f"{PUBLIC_BASE_URL.rstrip('/')}/mobile/{new_sid}"
    
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "session_id": new_sid,
        "mobile_url": mobile_url
    })

@app.get("/mobile/{session_id}", response_class=HTMLResponse)
async def mobile_index(request: Request, session_id: str):
    """移动端上传页"""
    if session_id not in SESSIONS:
        return HTMLResponse("<h1>二维码已失效 / Invalid QR Code</h1>")
    return templates.TemplateResponse("mobile.html", {"request": request, "session_id": session_id})

@app.get("/api/status/{session_id}")
async def check_status(session_id: str):
    """PC端轮询接口"""
    return SESSIONS.get(session_id, {"status": "expired"})

@app.post("/api/upload")
async def upload_file(
    bg_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session_id: str = Form(...)
):
    """统一上传接口 (PC和手机共用)"""
    if session_id not in SESSIONS:
        return JSONResponse(status_code=400, content={"message": "Invalid Session"})

    # 保存图片
    ext = file.filename.split('.')[-1]
    safe_name = f"{session_id}_{int(time.time())}.{ext}"
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    
    async with aiofiles.open(file_path, 'wb') as f:
        content = await file.read()
        await f.write(content)

    # 标记状态并启动后台任务
    SESSIONS[session_id]["status"] = "processing" # 改为 processing 让前端立刻转圈
    bg_tasks.add_task(media_factory.execute_workflow, session_id, file_path)
    
    return {"message": "Upload successful, processing started."}

if __name__ == "__main__":
    import uvicorn
    # 适配 Render 的 PORT 环境变量，本地默认 8000
    port = int(os.environ.get("PORT", 8000))
    print(f" 服务启动: {PUBLIC_BASE_URL} (Port: {port})")
    uvicorn.run(app, host="0.0.0.0", port=port)