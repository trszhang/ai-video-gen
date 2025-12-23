# 1. 使用 Python 3.10 轻量版作为基础镜像
FROM python:3.10-slim

# 2. 安装系统级依赖 (FFmpeg 和 ImageMagick) 并配置权限
# 这里我们将安装和修复合并在一步，并使用 find 命令自动查找 policy.xml，防止路径错误
RUN apt-get update && \
    apt-get install -y ffmpeg imagemagick && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    # 下面这行会自动找到 policy.xml 并解除 PDF/Ghostscript/Text 限制，防止 MoviePy 报错
    find /etc -name "policy.xml" -exec sed -i 's/none/read,write/g' {} +

# 3. 设置容器内的工作目录
WORKDIR /app

# 4. 优先复制依赖文件 (利用 Docker 缓存加速构建)
COPY requirements.txt .

# 5. 安装 Python 库
RUN pip install --no-cache-dir -r requirements.txt

# 6. 复制项目的所有代码
COPY . .

# 7. 预先创建存放视频和图片的文件夹
RUN mkdir -p static/videos/temp static/uploads

# 8. 启动命令
# 注意：请确保你的 Python 主入口文件名叫 main.py
# 如果你的文件名是 script.py，请把下面的 main:app 改成 script:app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
