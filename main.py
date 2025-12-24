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
from google.genai import types 
from dotenv import load_dotenv
from PIL import Image

# 加载 .env 文件
load_dotenv()

# ================= 核心配置 =================
APIMART_API_KEY = os.getenv("APIMART_API_KEY", "sk-ibdAt5NPqtNkzuBFonlTmr6lynjIYAl5YFTfhzdflBnefMp3")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "AIzaSyDXTkH4YPgdvOWEYPxHfiPfHqYwsJedI_w")

# 公网地址
PUBLIC_BASE_URL = "https://cheng-lan-aidao-yan.onrender.com"
FALLBACK_IMAGE_URL = "https://static.uganda-coffee.com/coffee/20250302/mbEsGl0Lmep58MlTkLoHFszXgk0UTW8El3AkE0PuK0ZAKTXDx2RpfrmcRXXSMmrU."

# 🔥 修改点：切换为 Pro 模型，指令执行力更强
MODEL_IMAGE_GEN = "gemini-3-pro-image-preview" 
APIMART_MODEL = "sora-2-pro"
APIMART_GEN_ENDPOINT = "https://api.apimart.ai/v1/videos/generations"
APIMART_TASK_ENDPOINT = "https://api.apimart.ai/v1/tasks"

# ================= 📂 目录配置 =================
VIDEO_DIR = "static/videos"
UPLOAD_DIR = "static/uploads"
GEN_IMG_DIR = "static/generated_images"

for path in [VIDEO_DIR, UPLOAD_DIR, GEN_IMG_DIR]:
    os.makedirs(path, exist_ok=True)

FINAL_VIDEO_PATH = os.path.join(VIDEO_DIR, "playlist.mp4")

google_client = genai.Client(api_key=GOOGLE_API_KEY)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- 1. 提示词定义 ---
class Prompts:
    GEMINI_DIRECTOR = """
    <role>
    You are a Visual Storyboard Artist.
    Task: Create a **Contact Sheet Image** (3x3 Grid) based on the input reference image.
    </role>
    
    <input>
    User provided: A reference product image.
    </input>
    
    <strict_visual_rules>
    1. **NO TEXT OUTPUT:** Do not explain. JUST GENERATE THE IMAGE FILE.
    2. **Consistency:** The product must look identical in all panels.
    3. **Layout:** 3x3 Grid (9 panels).
    </strict_visual_rules>
    
    <shot_list>
    1. Wide shot (Environment establishment)
    2. Medium shot (Panning camera)
    3. Close-up (Product details)
    4. Low angle (Hero shot)
    5. Top-down view
    6. Interaction (Steam, water drops, or light rays)
    7. Extreme close-up (Macro texture)
    8. Lifestyle context (on a table or shelf)
    9. Final beauty shot (Perfect composition)
    </shot_list>
    """

    SORA_TVC = """你是一位专业的TVC导演。
    任务：基于提供的图片参考（产品图或分镜图），制作一条高质量广告。
    要求：
    1. 严格保持产品视觉一致性。
    2. 视频时长15秒。
    3. 运镜流畅，光影高级，符合广告逻辑。"""

# --- 2. 核心生成工厂 ---
class MediaFactory:
    def __init__(self):
        self.is_generating = False

    # 🍌 步骤 A: Gemini Pro 生成联络表
    async def generate_contact_sheet_gemini(self, ref_image_path):
        print(f"   🍌 [Gemini Pro] 正在绘制分镜故事板...")
        try:
            def _run_genai():
                pil_img = Image.open(ref_image_path)
                
                # 安全设置：全部放行，避免误杀
                safety_settings = [
                    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                ]

                # 调用 Pro 模型
                response = google_client.models.generate_content(
                    model=MODEL_IMAGE_GEN,
                    contents=[Prompts.GEMINI_DIRECTOR, pil_img],
                    config=types.GenerateContentConfig(
                        safety_settings=safety_settings,
                        temperature=0.5 # Pro 模型稍微降低随机性，增加稳定性
                    )
                )
                
                generated_path = None
                
                # 寻找图片数据
                if response.parts:
                    for part in response.parts:
                        if part.inline_data:
                            img = part.as_image()
                            filename = f"storyboard_sheet_{int(time.time()*1000)}.png"
                            out_path = os.path.join(GEN_IMG_DIR, filename)
                            img.save(out_path)
                            generated_path = out_path
                            print(f"      ✅ Gemini Pro 绘图成功: {out_path}")
                            break
                
                if not generated_path and response.text:
                    print(f"      ⚠️ Gemini Pro 未生成图片，返回了文本: {response.text[:100]}...")

                return generated_path

            return await asyncio.to_thread(_run_genai)
        except Exception as e:
            print(f"      ⚠️ Gemini API 错误: {e}")
            return None

    # 🎬 步骤 B: APIMart Sora
    async def generate_video_tvc(self, prompt, image_path):
        print(f"   🎬 [APIMart] 准备生成 TVC 广告片...")
        
        final_image_url = ""
        if image_path:
            relative_path = image_path.replace("\\", "/")
            if relative_path.startswith("static/"):
                relative_path = relative_path
            
            base = PUBLIC_BASE_URL.rstrip("/")
            final_image_url = f"{base}/{relative_path}"
            print(f"      🔗 参考图公网地址: {final_image_url}")

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
                    print(f"      ❌ TVC 任务提交失败: {resp_json}")
                    return None
                
                print(f"      ✅ TVC 任务已提交 ID: {task_id}")

            except Exception as e:
                print(f"      ❌ 提交异常: {e}")
                return None

            # 轮询 (20分钟)
            poll_url = f"{APIMART_TASK_ENDPOINT}/{task_id}"
            for i in range(40): # 40 * 30s = 20 mins
                await asyncio.sleep(30)
                try:
                    print(f"      🔄 轮询中 ({(i+1)*30}/1200秒)...")
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
                        print(f"      ❌ 任务失败")
                        return None
                except: pass
            return None

    # --- 主流程 ---
    async def execute_workflow(self, ref_image_path):
        if self.is_generating: return
        self.is_generating = True
        print(f"=== 启动生成流程: Gemini Pro -> Sora TVC  ===")
        
        try:
            # 1. 尝试生成故事板
            storyboard_path = await self.generate_contact_sheet_gemini(ref_image_path)
            
            # 自动兜底逻辑
            if storyboard_path:
                print("✅ [流程正常] 使用 Gemini Pro 故事板生成视频")
                target_image = storyboard_path
                target_prompt = Prompts.SORA_TVC + " 严格按照分镜图逻辑执行。"
            else:
                print("⚠️ [流程降级] Gemini Pro 未出图，使用【原图】兜底生成视频")
                target_image = ref_image_path
                target_prompt = Prompts.SORA_TVC + " 这是一个产品原图，请基于此创作丰富的运镜广告。"

            # 2. 生成视频
            video_url = await self.generate_video_tvc(target_prompt, target_image)
            
            if video_url:
                print(f"      ⬇️ 正在下载最终成片...")
                async with httpx.AsyncClient(timeout=300) as c:
                    r = await c.get(video_url)
                    async with aiofiles.open(FINAL_VIDEO_PATH, 'wb') as f: 
                        await f.write(r.content)
                print(f"=== 🎉 商业广告片已发布: {FINAL_VIDEO_PATH} ===")
            else:
                print("❌ 视频生成失败")

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
    
    bg_tasks.add_task(media_factory.execute_workflow, file_path)
    
    return {
        "status": "started", 
        "message": "Using Gemini 1.5 Pro for storyboard. Fallback enabled."
    }

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    print(f"⚡ Pro 服务启动 (Gemini 1.5 Pro + Sora)")
    uvicorn.run(app, host="0.0.0.0", port=8000)