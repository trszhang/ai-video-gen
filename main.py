import os
import asyncio
import aiofiles
import time
import traceback
import httpx
from fastapi import FastAPI, Request, BackgroundTasks, File, UploadFile
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from google import genai
from dotenv import load_dotenv
from PIL import Image

# 加载 .env 文件
load_dotenv()

# ================= 核心配置 =================
# ⚠️ 请确保 .env 文件中包含有效的 API KEY
APIMART_API_KEY = os.getenv("APIMART_API_KEY", "sk-ibdAt5NPqtNkzuBFonlTmr6lynjIYAl5YFTfhzdflBnefMp3")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "AIzaSyDXTkH4YPgdvOWEYPxHfiPfHqYwsJedI_w")

# 2. 备用资源 & 公网地址 (APIMart 需要公网能访问图片的 URL)
# 请替换为你部署后的实际域名
PUBLIC_BASE_URL = "https://cheng-lan-aidao-yan.onrender.com"
FALLBACK_IMAGE_URL = "https://static.uganda-coffee.com/coffee/20250302/mbEsGl0Lmep58MlTkLoHFszXgk0UTW8El3AkE0PuK0ZAKTXDx2RpfrmcRXXSMmrU."

# 3. API 端点配置
MODEL_IMAGE_GEN = "gemini-2.0-flash" 
APIMART_MODEL = "sora-2"
APIMART_GEN_ENDPOINT = "https://api.apimart.ai/v1/videos/generations"
APIMART_TASK_ENDPOINT = "https://api.apimart.ai/v1/tasks"

# ================= 📂 目录配置 =================
VIDEO_DIR = "static/videos"
UPLOAD_DIR = "static/uploads"
GEN_IMG_DIR = "static/generated_images"

# 确保所有目录存在
for path in [VIDEO_DIR, UPLOAD_DIR, GEN_IMG_DIR]:
    os.makedirs(path, exist_ok=True)

# 最终视频文件路径
FINAL_VIDEO_PATH = os.path.join(VIDEO_DIR, "playlist.mp4")

# 初始化 Google 客户端
google_client = genai.Client(api_key=GOOGLE_API_KEY)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- 1. 提示词定义 ---
# --- 1. 提示词定义 (已修复：强制纯图片输出) ---
class Prompts:
    # 修复策略：移除了所有 "输出文字分析" 的指令，改为纯视觉指令
    GEMINI_DIRECTOR = """
    <role>
    You are an expert Storyboard Artist and Cinematographer.
    Task: Create a **Single Contact Sheet Image** (3x3 Grid) based on the input reference image.
    </role>
    
    <input>
    User provided: A reference product image.
    </input>
    
    <strict_visual_rules>
    1. **NO TEXT OUTPUT:** Do not explain the shot. Do not output a script. JUST GENERATE THE IMAGE.
    2. **Consistency is Key:** The subject (product) must look 100% identical in all panels.
    3. **Grid Layout:** Create a single image containing a 3x3 grid (9 panels total).
    4. **Cinematic Style:** High budget commercial look. Good lighting.
    </strict_visual_rules>
    
    <shot_sequence_requirements>
    Panel 1: Wide establishing shot of the environment (Luxury setting).
    Panel 2: Medium shot, camera panning left.
    Panel 3: Close-up of the product details.
    Panel 4: Low angle shot looking up at the product (Hero shot).
    Panel 5: Top-down view (God's eye view).
    Panel 6: Product interacting with elements (water, steam, or light rays).
    Panel 7: Extreme close-up (Macro shot) of the texture.
    Panel 8: The product in a lifestyle setting (on a table, or in hand).
    Panel 9: Final beauty shot with logo-ready composition.
    </shot_sequence_requirements>
    
    <output_format>
    OUTPUT ONLY THE IMAGE FILE.
    </output_format>
    """

    # APIMart (Sora): TVC 导演 (保持不变)
    SORA_TVC = """你是一位专业的TVC导演，现在需要你根据我提供给你的分镜图（联络表），严格拆解分镜逻辑。
    注意：输入图是一张包含9个镜头的3x3网格图。
    请识别这9个镜头的视觉流，并将其转化为一条连贯、流畅、高质量的商业广告视频。
    严格保持产品在所有帧中的一致性。视频时长5-10秒。"""
    
# --- 2. 核心生成工厂 ---
class MediaFactory:
    def __init__(self):
        self.is_generating = False

    # 🍌 步骤 A: Gemini 生成联络表
    async def generate_contact_sheet_gemini(self, ref_image_path):
        print(f"   🍌 [Gemini] 正在构思并绘制分镜故事板 (Contact Sheet)...")
        try:
            def _run_genai():
                pil_img = Image.open(ref_image_path)
                response = google_client.models.generate_content(
                    model=MODEL_IMAGE_GEN,
                    contents=[Prompts.GEMINI_DIRECTOR, pil_img]
                )
                generated_path = None
                
                for part in response.parts:
                    if part.inline_data:
                        img = part.as_image()
                        filename = f"storyboard_sheet_{int(time.time()*1000)}.png"
                        out_path = os.path.join(GEN_IMG_DIR, filename)
                        img.save(out_path)
                        generated_path = out_path
                        print(f"      ✅ Gemini 故事板生成成功: {out_path}")
                        break
                
                if response.text:
                    print(f"      📝 [Gemini 分析摘要]: {response.text[:100]}...")
                return generated_path
            return await asyncio.to_thread(_run_genai)
        except Exception as e:
            print(f"      ⚠️ Gemini 绘图失败: {e}")
            traceback.print_exc()
            return None

    # 🎬 步骤 B: APIMart Sora (含20分钟超时逻辑)
    async def generate_video_tvc(self, local_contact_sheet_path):
        print(f"   🎬 [APIMart] 准备根据故事板生成 TVC 广告片...")
        
        final_image_url = ""
        # 构造公网 URL
        if local_contact_sheet_path:
            relative_path = local_contact_sheet_path.replace("\\", "/")
            if relative_path.startswith("static/"):
                relative_path = relative_path 
            
            base = PUBLIC_BASE_URL.rstrip("/")
            final_image_url = f"{base}/{relative_path}"
            print(f"      🔗 故事板公网地址: {final_image_url}")

        if not final_image_url:
            final_image_url = FALLBACK_IMAGE_URL

        headers = {
            "Authorization": f"Bearer {APIMART_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": APIMART_MODEL,
            "prompt": Prompts.SORA_TVC,
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
                    print(f"      ❌ TVC 任务提交失败: {resp_json}")
                    return None
                
                print(f"      ✅ TVC 任务已提交 ID: {task_id}")

            except Exception as e:
                print(f"      ❌ 提交异常: {e}")
                return None

            # --- 轮询逻辑 (修改为20分钟) ---
            poll_url = f"{APIMART_TASK_ENDPOINT}/{task_id}"
            
            # 策略: 每30秒检查一次，共40次，总计1200秒(20分钟)
            MAX_RETRIES = 40
            POLL_INTERVAL = 30 
            
            print(f"      ⏳ 开始轮询状态，最大等待时间: 20分钟...")

            for i in range(MAX_RETRIES): 
                await asyncio.sleep(POLL_INTERVAL)
                try:
                    current_time_waited = (i + 1) * POLL_INTERVAL
                    print(f"      🔄 轮询中 ({current_time_waited}/1200秒)...")
                    
                    poll_res = await client.get(poll_url, headers=headers, params={"language": "en"})
                    if poll_res.status_code != 200: continue
                    
                    data_body = poll_res.json().get('data', {})
                    status = data_body.get('status')
                    
                    if status == 'completed':
                        videos = data_body.get('result', {}).get('videos', [])
                        if videos and videos[0].get('url'):
                            print(f"      🎉 TVC 视频生成成功!")
                            return videos[0]['url'][0]
                        return None
                    elif status == 'failed':
                        print(f"      ❌ TVC 生成任务报告失败 (failed)")
                        return None
                        
                except Exception as e:
                    print(f"      ⚠️ 轮询网络抖动: {e}")
                    pass
            
            print(f"      ❌ 错误: 任务超时 (已等待20分钟仍未完成)")
            return None

    # --- 主流程 ---
    async def execute_workflow(self, ref_image_path):
        if self.is_generating: return
        self.is_generating = True
        print(f"=== 启动生成流程: Gemini 故事板 -> Sora TVC  ===")
        
        try:
            # 1. 生成故事板
            contact_sheet_path = await self.generate_contact_sheet_gemini(ref_image_path)
            
            if not contact_sheet_path:
                print("❌ 故事板生成失败")
                return

            # 2. 生成视频 (含20分钟超时)
            video_url = await self.generate_video_tvc(contact_sheet_path)
            
            if video_url:
                print(f"      ⬇️ 正在下载最终成片...")
                async with httpx.AsyncClient(timeout=300) as c:
                    r = await c.get(video_url)
                    async with aiofiles.open(FINAL_VIDEO_PATH, 'wb') as f: 
                        await f.write(r.content)
                print(f"=== 🎉 商业广告片已发布: {FINAL_VIDEO_PATH} ===")
            else:
                print("❌ 视频生成失败或超时")

        except: 
            traceback.print_exc()
        finally: 
            self.is_generating = False

media_factory = MediaFactory()

# --- 路由 ---
@app.post("/update_playlist")
async def update_playlist(
    bg_tasks: BackgroundTasks,
    product_image: UploadFile = File(...)
):
    safe_filename = f"ref_{int(time.time())}.jpg"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)
    
    async with aiofiles.open(file_path, 'wb') as f:
        content = await product_image.read()
        await f.write(content)
    
    print(f"DEBUG: ✅ 用户参考图上传: {file_path}")
    bg_tasks.add_task(media_factory.execute_workflow, file_path)
    
    return {
        "status": "started", 
        "ref_image": file_path,
        "message": "Generating storyboard and video. Timeout set to 20 mins."
    }

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    print(f"   - 故事板路径: {GEN_IMG_DIR}")
    print(f"   - 最终视频路径: {FINAL_VIDEO_PATH}")
    uvicorn.run(app, host="0.0.0.0", port=8000)