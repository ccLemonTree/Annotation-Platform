"""
数据管理平台后端服务入口
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from api.smarteye_routes import router as smarteye_router

# 创建FastAPI应用
app = FastAPI(
    title="数据管理平台API",
    description="多租户数据标注与模型训练平台",
    version="1.0.0"
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应该配置具体的域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(smarteye_router, prefix="/api/smarteye", tags=["SmartEye导入"])

@app.get("/")
async def root():
    """根路径"""
    return {
        "message": "数据管理平台API",
        "version": "1.0.0",
        "docs": "/docs"
    }

@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
