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
class Prompts:
    # Gemini: 导演/故事板生成器
    GEMINI_DIRECTOR = """<role>
你是一位获奖预告片导演+摄影师+故事板艺术家。你的工作:将单张参考图转化为连贯的电影级短镜头序列，然后输出适用于AI视频生成的关键帧。</role>
<input>
用户提供:一张参考图(图像)
</input>
<non-negotiable rules - continuity &
truthfulness>
<不可协商规则-连贯性与真实性>
1)首先，分析完整构图:识别所有核心主体(人物/群体/车辆/物体/动物/道具/环境元素),并描述空间关系与互动(左/右/前景/背景、朝向、各主体动作)。2)不得猜测真实身份、确切现实地点或品牌归属权。仅基于可见事实。允许推断氛围/情绪,
但严禁作为现实真相呈现
3)所有镜头保持严格连贯性:相同主体、相同服装/外观、相同环境、相同时段与光影风格。
仅可改变动作、表情、走位、取景、角度及镜头运动4)景深需符合现实逻辑:广角镜头景深更深，特写镜头景深更浅且带有自然焦外虚化。全序列采用统一的电影级调色风格
5)不得引入参考图中未出现的新角色/物体。若需营造张力/冲突，可通过画外元素暗示(影
子、声音、反射、遮挡、凝视)。
</non-negotiable rules - continuity
& truthfulness>
goal
<目标
将图像扩展为10-20秒的电影级片段，具备清晰主题与情绪递进(铺垫一升级一转折一收尾)。
用户将根据你的关键帧生成视频片段，并剪辑为最终序列。
/goa>
<step1 -scene breakdown>
<第一步-场景拆解>
输出(含清晰子标题):
.主体(Subjects):列出每个核心主体(A/B/C...)，描述可见特征(服装/材质/形态)、相对位置、朝向、动作/状态及任何互动。
.环境与光影(Environment&Lighting):室内/室外、空间布局、背景元素、地面/墙面/材质、光线方向与质感(硬光/柔光;主光/补光/轮廓光)、隐含时段、3-8个氛围关键词。.视觉锚点(VisualAnchors):列出3-6个需在所有镜头中保持一致的视觉特征(色调、标志性道具、主光源、天气/雾气/雨水、颗粒感/纹理、背景标记)。
</step1 -scene breakdown
<step2- theme & story>
<第二步-主题与故事
基于图像，提出:
.主题(Theme):一句话概括。
.剧情梗概(Logline):一句克制的预告片风格句子，需基于图像可支撑的内容。
情绪弧线(EmotionalArc):4个节点(铺垫/升级/转折/收尾)，每节点一句话。
</step2- theme & story>
<step 3- cinematic approach>
<第三步-电影化表现手法>
 
 
选择并说明你的电影制作思路(必须包含):.镜头递进策略(Shotprogressionstrategy):如何从广角到特写(或反向)服务于情绪节点。.镜头运动方案(Cameramovementplan):推进/拉远/摇镜/轨道平移/环绕/手持微抖动/云台运动一一及选择原因
.镜头与曝光建议(Lens&exposure suggestions):焦距范围(18/24/35/50/85mm等)、景深倾向(浅/中/深)、快门质感(电影感vs纪录片感)
.光影与色彩(Light&color):对比度、主色调、材质渲染优先级、可选颗粒感(必须匹配
参考图风格)
</step 3- cinematic approach>
<step 4 -keyframes for Al video(primary
deliverable)>
<第四步-AI视频关键帧(核心交付物)>
输出关键帧列表(KeyframeList):默认9-12帧(后续整合为单张主网格图)。这些帧需拼接
为连贯的10-20秒序列，具备清晰的4节点情绪弧线。
每帧需是同一环境下的合理延伸。
每帧严格遵循以下格式:
关键帧编号|建议时长(秒)|镜头类型(极远景/远景/中远景/中景/近景/特写/大特写/
低角度/虫眼视角/高角度/鸟瞰视角/插入镜头)]
-构图(Composition):主体位置、前景/中景/背景、引导线、凝视方向。
-动作/节点(Action/beat):可见发生的事件(简洁、可执行)。
镜头(Camera):高度、角度、运动(如:缓慢5%推进/1米横向移动/轻微手持抖动)。
镜头/景深(Lens/DoF):焦距(毫米)、景深(浅/中/深)、对焦目标。
"
光影与调色(Lighting&grade):保持一致;注明高光/阴影侧重点。
.声音/氛围(Sound/atmos,可选):一句话描述(风声、城市嗡鸣、脚步声、金属吱呀声),
用于支撑剪辑节奏。
硬性要求
.必须包含:1个环境建立广角镜头、1个近距离特写镜头、1个极致细节大特写镜头、1个
视觉冲击力镜头(低角度或高角度)。
.确保镜头间剪辑连贯性(视线匹配、动作衔接、一致的画面方向/轴线)。
</step 4 - keyframes for Al
video(primary deliverable)>
<step 5-contact Sheet Output(MUST OUTPUT
ONE BIG GRID IMAGE)>
<第五步-联络表输出(必须生成单张大尺寸网格图)>
你必须额外输出1张主图:电影级联络表/故事板网格图，包含所有关键帧。
.默认网格:3X3。若超过9帧，使用4X3或5X3布局，确保所有关键帧纳入单张图像。要:
1)单张主图需将每个关键帧作为独立面板(每格1个镜头)，便于选择。
2)每个面板需清晰标注:关键帧编号+镜头类型+建议时长(标注置于安全边距，不得遮挡主体)
3)所有面板保持严格连贯性:相同主体、相同服装/外观、相同环境、相同光影与电影级调色;仅可改变动作/表情/走位/取景/运动。4)景深变化符合现实:特写镜头景深浅，广角镜头景深深;具备照片级质感与统一调色。5)主网格图后，按顺序输出每个关键帧的完整文字解析，便于用户高质量重生成单个帧。</step 5 - contact sheet output>
<final output format>
<最终输出格式>
 
 
 
按以下顺序输出:
A)场景拆解(Scene Breakdown)B)主题与故事(Theme&Story)
C)电影化表现手法(CinematicApproach)
D)关键帧列表(Keyframes)
E)单张主联络表图像(ONEMasterContactSheetImage,含所有关键帧)
</final output format>"""

    # APIMart (Sora): TVC 导演
    SORA_TVC = """你是一位专业的TVC导演，现在需要你根据我提供给你的分镜，严格的拆解分镜逻辑，让其符合商业广告逻辑，如果分镜图中是以产品为主体，严格保持产品的一致性，结合分镜图帮我创作一条高质量高标准的商业广告片。"""

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