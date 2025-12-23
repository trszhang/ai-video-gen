import os
import asyncio
import aiofiles
import time
import traceback
import httpx 
from PIL import Image
from fastapi import FastAPI, Request, BackgroundTasks, File, UploadFile, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
# 注意：必须在 requirements.txt 中指定 moviepy==1.0.3
from moviepy.editor import VideoFileClip, concatenate_videoclips
from google import genai 

# ================= 🔧 核心配置 =================
# 1. API Keys
APIMART_API_KEY = "sk-ibdAt5NPqtNkzuBFonlTmr6lynjIYAl5YFTfhzdflBnefMp3"
GOOGLE_API_KEY = "AIzaSyCCmX7c3zKoaDD5b4eAAGHZXvERdthnQkU"

# 2. 备用资源 & 公网地址配置
FALLBACK_IMAGE_URL = "https://static.uganda-coffee.com/coffee/20250302/mbEsGl0Lmep58MlTkLoHFszXgk0UTW8El3AkE0PuK0ZAKTXDx2RpfrmcRXXSMmrU."

# 👇 [关键修改] 尝试从系统环境变量读取 URL，读不到则为空
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
print(f"DEBUG: 当前系统配置的公网地址是: [{PUBLIC_BASE_URL}]")

# 3. API 端点配置
MODEL_IMAGE_GEN = "gemini-2.5-flash-image" 
APIMART_MODEL = "sora-2"
APIMART_GEN_ENDPOINT = "https://api.apimart.ai/v1/videos/generations" # 提交任务
APIMART_TASK_ENDPOINT = "https://api.apimart.ai/v1/tasks"           # 查询任务

# ================= 📂 目录配置 =================
VIDEO_DIR = "static/videos"
TEMP_DIR = "static/videos/temp"
UPLOAD_DIR = "static/uploads"
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
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
            "img_prompt": "Cinematic shot of the product on a wooden table, warm sunlight hitting it from the side.",
            "video_prompt": "Cinematic establishing shot. Warm sunlight sweeps across the surface, revealing the logo clearly. Dust motes dance in the light beam."
        },
        {
            "id": 2,
            "desc": "02 蒸汽特写 (Steam)",
            "img_prompt": "Close up of the product with hot steam rising from it. Dark moody background.",
            "video_prompt": "Extreme close-up macro shot. Thick, swirling hot steam rises elegantly from the product. Dark moody background with bokeh. Slow motion."
        },
        {
            "id": 3,
            "desc": "03 动态抓取 (Interaction)",
            "img_prompt": "A human hand reaching out to grab the product.",
            "video_prompt": "First person view. A hand enters the frame naturally and lifts the product up smoothly from the table."
        }
    ]

# --- 2. 核心生成工厂 ---
class MediaFactory:
    def __init__(self):
        self.is_generating = False
        self.sem_process = asyncio.Semaphore(1)

    # 🍌 步骤 A: Gemini 生成分镜图
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
                        filename = f"nano_{int(time.time()*1000)}.png"
                        out_path = os.path.join(TEMP_DIR, filename)
                        img.save(out_path)
                        generated_path = out_path
                        print("      ✅ 图片生成成功")
                        break
                return generated_path
            return await asyncio.to_thread(_run_genai)
        except Exception as e:
            print(f"      ⚠️ Gemini 绘图失败: {e}")
            return None

    # 🎬 步骤 B: APIMart Sora
    async def generate_video_apimart(self, prompt, local_image_path):
        print(f"   🎬 [APIMart] 准备提交任务: {prompt[:15]}...")
        
        # 1. 图片链接处理 (关键逻辑)
        final_image_url = ""
        # 只有当 PUBLIC_BASE_URL 存在且不为空时，才尝试构建 URL
        if local_image_path and PUBLIC_BASE_URL and PUBLIC_BASE_URL.strip():
            relative_path = local_image_path.replace("\\", "/")
            if "static" in relative_path:
                part = relative_path.split("static")[-1]
                # 确保路径拼接正确，避免出现双斜杠
                base = PUBLIC_BASE_URL.rstrip("/")
                final_image_url = f"{base}/static{part}"
            else:
                base = PUBLIC_BASE_URL.rstrip("/")
                final_image_url = f"{base}/{relative_path}"
            print(f"      🔗 使用本地图公网地址: {final_image_url}")

        if not final_image_url:
            print("      ⚠️ 无本地公网地址，切换至【指定备用图】")
            final_image_url = FALLBACK_IMAGE_URL

        headers = {
            "Authorization": f"Bearer {APIMART_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # 2. 构造 Payload
        payload = {
            "model": APIMART_MODEL,
            "prompt": prompt,
            "duration": 5, # 缩短到5秒测试更稳
            "aspect_ratio": "16:9",
            "private": False,
            "image_urls": [final_image_url]
        }

        async with httpx.AsyncClient(timeout=60) as client:
            # === 提交任务 ===
            task_id = None
            try:
                response = await client.post(APIMART_GEN_ENDPOINT, json=payload, headers=headers)
                resp_json = response.json()
                
                if resp_json.get('code') == 200 and 'data' in resp_json:
                    data_obj = resp_json['data']
                    if isinstance(data_obj, list) and len(data_obj) > 0:
                        task_id = data_obj[0].get('task_id')
                    elif isinstance(data_obj, dict):
                        task_id = data_obj.get('task_id')
                
                if not task_id:
                    print(f"      ❌ 提交失败，未获取到 Task ID。响应: {resp_json}")
                    return None
                
                print(f"      ✅ 任务已提交 ID: {task_id}")
                print(f"      ⏳ 开始长轮询 (1分钟/次, 共10次)...")

            except Exception as e:
                print(f"      ❌ 提交请求异常: {e}")
                return None

            # === 轮询结果 ===
            poll_url = f"{APIMART_TASK_ENDPOINT}/{task_id}"
            for i in range(10): 
                await asyncio.sleep(60)
                try:
                    print(f"      🔄 第 {i+1}/10 次查询任务状态...")
                    poll_res = await client.get(poll_url, headers=headers, params={"language": "en"})
                    if poll_res.status_code != 200: continue

                    poll_data = poll_res.json()
                    data_body = poll_data.get('data', {})
                    status = data_body.get('status')
                    
                    if status == 'completed':
                        result = data_body.get('result', {})
                        videos = result.get('videos', [])
                        if videos and len(videos) > 0:
                            url_list = videos[0].get('url', [])
                            if url_list:
                                print(f"      🎉 视频生成成功")
                                return url_list[0]
                        return None
                    elif status == 'failed':
                        print(f"      ❌ 任务失败: {data_body}")
                        return None
                    else:
                        print(f"      ⏳ 状态: {status} (进度: {data_body.get('progress')}%)")
                        
                except Exception as e:
                    print(f"      ⚠️ 轮询出错: {e}")
                    
            print("      ❌ 视频生成超时")
            return None

    # --- 主流程 ---
    async def execute_workflow(self, brand, product, ref_image_path):
        if self.is_generating: return
        self.is_generating = True
        print(f"=== 🚀 启动流程: {brand} {product} ===")
        
        try:
            clips = []
            # 仅生成前2个镜头
            for kf in StoryEngine.KEYFRAMES[:2]: 
                async with self.sem_process:
                    print(f"\n--- 制作分镜 {kf['id']} ---")
                    
                    # 1. Gemini
                    img_prompt = f"{kf['img_prompt']} Subject: {brand} {product}."
                    local_gen_img = await self.generate_image_nanobanana(img_prompt, ref_image_path)
                    target_local_path = local_gen_img if local_gen_img else ref_image_path
                    
                    # 2. APIMart
                    vid_prompt = f"{kf['video_prompt']} Subject: {brand} {product}."
                    vid_url = await self.generate_video_apimart(vid_prompt, target_local_path)
                    
                    # 3. 下载
                    if vid_url:
                        fname = os.path.join(TEMP_DIR, f"clip_{kf['id']}.mp4")
                        async with httpx.AsyncClient(timeout=300) as c:
                            r = await c.get(vid_url)
                            async with aiofiles.open(fname, 'wb') as f: await f.write(r.content)
                        clips.append(fname)
                    else:
                        print("      ⚠️ 跳过此镜头")

            # 4. 合成
            if clips:
                print("\n--- ✂️ 剪辑合成中 ---")
                await asyncio.to_thread(self._concat, clips)
                print("=== 🎉 影片发布成功 ===")
            else:
                 print("\n❌ 未能生成任何有效片段")

        except Exception: traceback.print_exc()
        finally: self.is_generating = False

    def _concat(self, files):
        try:
            clips = [VideoFileClip(f).resize((1280, 720)).set_fps(24) for f in files]
            final = concatenate_videoclips(clips, method="compose")
            final.write_videofile(FINAL_VIDEO_PATH, codec="libx264", audio_codec="aac", fps=24, logger=None)
            for c in clips: c.close()
            final.close()
        except: pass

media_factory = MediaFactory()

# --- 路由 ---
@app.post("/update_playlist")
async def update_playlist(
    bg_tasks: BackgroundTasks,
    brand: str = Form(...),
    product: str = Form(...),
    product_image: UploadFile = File(...)
):
    safe_filename = f"ref_{int(time.time())}.jpg"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)
    async with aiofiles.open(file_path, 'wb') as f:
        await f.write(await product_image.read())
    
    bg_tasks.add_task(media_factory.execute_workflow, brand, product, file_path)
    return {"status": "started", "ref_image": file_path}

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    print(f"⚡ 服务已启动 - APIMart/Render 版")
    uvicorn.run(app, host="0.0.0.0", port=8000)