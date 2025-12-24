import os
import asyncio
import aiofiles
import time
import traceback
import httpx
from PIL import Image
from fastapi import FastAPI, Request, BackgroundTasks, File, UploadFile
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from google import genai
from dotenv import load_dotenv

# 加载 .env 文件 (推荐安全方式)
load_dotenv()

# ⚠️ 确保 requirements.txt 包含: moviepy, imageio-ffmpeg
# 引入 moviepy 相关
from moviepy.editor import VideoFileClip, concatenate_videoclips

# ================= 🔧 核心配置 =================
# 1. API Keys (建议使用环境变量，这里保留硬编码逻辑作为备选)
APIMART_API_KEY = os.getenv("APIMART_API_KEY", "sk-ibdAt5NPqtNkzuBFonlTmr6lynjIYAl5YFTfhzdflBnefMp3")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "AIzaSyDXTkH4YPgdvOWEYPxHfiPfHqYwsJedI_w")

# 2. 备用资源 & 公网地址
FALLBACK_IMAGE_URL = "https://static.uganda-coffee.com/coffee/20250302/mbEsGl0Lmep58MlTkLoHFszXgk0UTW8El3AkE0PuK0ZAKTXDx2RpfrmcRXXSMmrU."
PUBLIC_BASE_URL = "https://cheng-lan-aidao-yan.onrender.com"

# 3. API 端点配置
MODEL_IMAGE_GEN = "gemini-2.5-flash-image"
APIMART_MODEL = "sora-2"
APIMART_GEN_ENDPOINT = "https://api.apimart.ai/v1/videos/generations"
APIMART_TASK_ENDPOINT = "https://api.apimart.ai/v1/tasks"

# ================= 📂 目录配置 =================
VIDEO_DIR = "static/videos"
TEMP_DIR = "static/videos/temp"
UPLOAD_DIR = "static/uploads"
GEN_IMG_DIR = "static/generated_images"  

# 确保所有目录存在
for path in [VIDEO_DIR, TEMP_DIR, UPLOAD_DIR, GEN_IMG_DIR]:
    os.makedirs(path, exist_ok=True)

FINAL_VIDEO_PATH = os.path.join(VIDEO_DIR, "playlist.mp4")

# 初始化 Google 客户端
google_client = genai.Client(api_key=GOOGLE_API_KEY)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- 1. 导演脚本引擎 ---
class StoryEngine:
    KEYFRAMES = [
        {
            "id": 1,
            "desc": "01 光影瞬变 (Reveal)",
            "img_prompt": "Cinematic shot of the product on a wooden table, warm sunlight hitting it from the side. High quality, photorealistic, 8k.",
            "video_prompt": "Cinematic establishing shot. Warm sunlight sweeps across the surface, revealing the product clearly. Dust motes dance in the light beam."
        },
        {
            "id": 2,
            "desc": "02 蒸汽特写 (Steam)",
            "img_prompt": "Close up of the product with hot steam rising from it. Dark moody background. High quality, photorealistic.",
            "video_prompt": "Extreme close-up macro shot. Thick, swirling hot steam rises elegantly from the product. Dark moody background with bokeh. Slow motion."
        },
        {
            "id": 3,
            "desc": "03 动态抓取 (Interaction)",
            "img_prompt": "A human hand reaching out to grab the product. First person perspective. Realistic skin texture.",
            "video_prompt": "First person view. A hand enters the frame naturally and lifts the product up smoothly from the table."
        }
    ]

# --- 2. 核心生成工厂 ---
class MediaFactory:
    def __init__(self):
        self.is_generating = False
        self.sem_process = asyncio.Semaphore(1)

    # 🍌 步骤 A: Gemini 生成分镜图 (已修复：保存到 generated_images)
    async def generate_image_nanobanana(self, prompt, ref_image_path):
        print(f"   🍌 [Gemini] 正在绘制: {prompt[:15]}...")
        try:
            def _run_genai():
                pil_img = Image.open(ref_image_path)
                response = google_client.models.generate_content(
                    model=MODEL_IMAGE_GEN,
                    contents=[prompt, pil_img]
                )
                generated_path = None
                for part in response.parts:
                    if part.inline_data:
                        img = part.as_image()
                        # ✨ 修改：保存到专门的 generated_images 文件夹，方便查看和下载
                        filename = f"gemini_gen_{int(time.time()*1000)}.png"
                        out_path = os.path.join(GEN_IMG_DIR, filename)
                        img.save(out_path)
                        generated_path = out_path
                        print(f"      ✅ Gemini 图片生成并保存成功: {out_path}")
                        break
                return generated_path
            return await asyncio.to_thread(_run_genai)
        except Exception as e:
            print(f"      ⚠️ Gemini 绘图失败: {e}")
            return None

    # 🎬 步骤 B: APIMart Sora
    async def generate_video_apimart(self, prompt, local_image_path):
        print(f"   🎬 [APIMart] 准备提交任务: {prompt[:15]}...")
        
        final_image_url = ""
        if local_image_path:
            relative_path = local_image_path.replace("\\", "/")
            if relative_path.startswith("/"):
                relative_path = relative_path[1:]
            
            base = PUBLIC_BASE_URL.rstrip("/")
            final_image_url = f"{base}/{relative_path}"
            print(f"      🔗 构建公网地址成功: {final_image_url}")

        if not final_image_url:
            final_image_url = FALLBACK_IMAGE_URL

        headers = {
            "Authorization": f"Bearer {APIMART_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": APIMART_MODEL,
            "prompt": prompt,
            "duration": 5, 
            "aspect_ratio": "16:9",
            "private": False,
            "image_urls": [final_image_url]
        }

        async with httpx.AsyncClient(timeout=60) as client:
            try:
                response = await client.post(APIMART_GEN_ENDPOINT, json=payload, headers=headers)
                resp_json = response.json()
                task_id = None
                
                if resp_json.get('code') == 200 and 'data' in resp_json:
                    data = resp_json['data']
                    task_id = data[0].get('task_id') if isinstance(data, list) else data.get('task_id')
                
                if not task_id:
                    print(f"      ❌ 提交失败: {resp_json}")
                    return None
                
                print(f"      ✅ 任务已提交 ID: {task_id}")

            except Exception as e:
                print(f"      ❌ 提交异常: {e}")
                return None

            poll_url = f"{APIMART_TASK_ENDPOINT}/{task_id}"
            for i in range(10): 
                await asyncio.sleep(60) # 等待 60 秒轮询一次
                try:
                    print(f"      🔄 第 {i+1}/10 次查询任务状态...")
                    poll_res = await client.get(poll_url, headers=headers, params={"language": "en"})
                    if poll_res.status_code != 200: continue
                    
                    data_body = poll_res.json().get('data', {})
                    status = data_body.get('status')
                    
                    if status == 'completed':
                        videos = data_body.get('result', {}).get('videos', [])
                        if videos and videos[0].get('url'):
                            print(f"      🎉 视频生成成功")
                            return videos[0]['url'][0]
                        return None
                    elif status == 'failed':
                        print(f"      ❌ 视频生成任务返回 failed")
                        return None
                except: pass
            return None

    # --- 主流程 ---
    async def execute_workflow(self, ref_image_path):
        if self.is_generating: return
        self.is_generating = True
        print(f"=== 启动自动生成流程  ===")
        
        try:
            clips = []
            for kf in StoryEngine.KEYFRAMES: 
                async with self.sem_process:
                    print(f"\n--- 制作分镜 {kf['id']} : {kf['desc']} ---")
                    
                    # 1. Gemini
                    img_prompt = f"{kf['img_prompt']} Subject: The product shown in the reference image."
                    local_gen_img = await self.generate_image_nanobanana(img_prompt, ref_image_path)
                    
                    # 如果 Gemini 成功，使用生成的图；否则使用原图兜底
                    target_local_path = local_gen_img if local_gen_img else ref_image_path
                    
                    # 2. APIMart
                    vid_prompt = f"{kf['video_prompt']} Subject: The product shown in the reference image."
                    vid_url = await self.generate_video_apimart(vid_prompt, target_local_path)
                    
                    if vid_url:
                        fname = os.path.join(TEMP_DIR, f"clip_{kf['id']}.mp4")
                        async with httpx.AsyncClient(timeout=300) as c:
                            r = await c.get(vid_url)
                            async with aiofiles.open(fname, 'wb') as f: await f.write(r.content)
                        clips.append(fname)
                        print(f"      💾 片段已下载: {fname}")
                    else:
                        print(f"      ⚠️ 片段 {kf['id']} 生成失败，将被跳过")

            if clips:
                print(f"\n--- ✂️ 剪辑合成中 (共 {len(clips)} 个片段) ---")
                await asyncio.to_thread(self._concat, clips)
                print("=== 🎉 影片发布成功 ===")
            else:
                 print("\n❌ 未能生成任何有效片段")
        except: traceback.print_exc()
        finally: self.is_generating = False

    def _concat(self, files):
        # ✅ 修复 2: 完整的错误捕捉和日志
        print("DEBUG: 开始调用 MoviePy 进行拼接...")
        try:
            if not files:
                print("DEBUG: 文件列表为空，无法拼接")
                return

            # 加载片段
            clips = []
            for f in files:
                try:
                    clip = VideoFileClip(f).resize((1280, 720)).set_fps(24)
                    clips.append(clip)
                except Exception as e:
                    print(f"❌ 加载片段失败 {f}: {e}")
            
            if not clips:
                print("❌ 没有有效的视频片段可供拼接")
                return

            # 合成
            final = concatenate_videoclips(clips, method="compose")
            
            # 写入文件 (显示进度条 logger='bar')
            # 增加 preset='ultrafast' 牺牲一点压缩率换取速度和稳定性
            final.write_videofile(
                FINAL_VIDEO_PATH, 
                codec="libx264", 
                audio_codec="aac", 
                fps=24, 
                preset="ultrafast",
                logger=None 
            )
            
            # 清理资源
            for c in clips: c.close()
            final.close()
            print(f"✅ 最终视频已写入: {FINAL_VIDEO_PATH}")
            
        except Exception as e:
            print(f"❌ 视频拼接严重错误: {e}")
            traceback.print_exc() # 打印完整堆栈，方便查错

media_factory = MediaFactory()

# --- 路由 ---
@app.post("/update_playlist")
async def update_playlist(
    bg_tasks: BackgroundTasks,
    product_image: UploadFile = File(...)
):
    # 保存上传的图片
    safe_filename = f"ref_{int(time.time())}.jpg"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)
    
    async with aiofiles.open(file_path, 'wb') as f:
        content = await product_image.read()
        await f.write(content)
    
    file_size = os.path.getsize(file_path)
    print(f"DEBUG: ✅ 图片上传成功! 路径: {file_path}, 大小: {file_size/1024:.2f} KB")
    
    # 后台启动生成
    bg_tasks.add_task(media_factory.execute_workflow, file_path)
    
    return {
        "status": "started", 
        "ref_image": file_path,
        "local_url": PUBLIC_BASE_URL 
    }

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    print(f"⚡ 服务已启动 (修复版)")
    print(f"   - 图片生成保存路径: {GEN_IMG_DIR}")
    print(f"   - 视频最终保存路径: {FINAL_VIDEO_PATH}")
    uvicorn.run(app, host="0.0.0.0", port=8000)