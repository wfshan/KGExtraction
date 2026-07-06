"""
KGExtraction 后端 FastAPI 入口
"""
import os
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from routers import system, projects, documents, schema, runs, graph, graph_rag, governance
from config import DATA_DIR

app = FastAPI(
    title="KGExtraction API",
    description="知识图谱抽取系统后端服务",
    version="1.0.0",
)

# CORS 配置 - 允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== 最小访问控制 =====
# .env 中配置 KG_ACCESS_TOKEN 后，所有 /api/*（健康检查除外）要求
# Authorization: Bearer <token>。未配置时不启用（本机单人使用场景）。
# 面向团队/网络部署时必须配置，否则任何能访问端口的人都可读写全部数据。
_ACCESS_TOKEN = os.getenv("KG_ACCESS_TOKEN", "").strip()

_AUTH_EXEMPT_PATHS = {"/api/health", "/docs", "/openapi.json", "/redoc"}


@app.middleware("http")
async def access_token_middleware(request: Request, call_next):
    if _ACCESS_TOKEN and request.url.path.startswith("/api") and request.url.path not in _AUTH_EXEMPT_PATHS:
        if request.method != "OPTIONS":  # CORS 预检放行
            auth = request.headers.get("Authorization", "")
            token = auth[7:] if auth.startswith("Bearer ") else ""
            if not secrets.compare_digest(token, _ACCESS_TOKEN):
                return JSONResponse(status_code=401, content={"detail": "无效或缺失的访问令牌（Authorization: Bearer <KG_ACCESS_TOKEN>）"})
    return await call_next(request)

# 挂载路由
app.include_router(system.router, prefix="/api/system", tags=["系统配置"])
app.include_router(projects.router, prefix="/api/projects", tags=["项目管理"])
app.include_router(documents.router, prefix="/api/projects", tags=["文档管理"])
app.include_router(schema.router, prefix="/api/projects", tags=["Schema 配置"])
app.include_router(runs.router, prefix="/api/projects", tags=["抽取任务"])
app.include_router(graph.router, prefix="/api/projects", tags=["图谱数据"])
app.include_router(graph_rag.router, prefix="/api/projects", tags=["问图"])
app.include_router(governance.router, prefix="/api/projects", tags=["知识治理与演进"])


@app.on_event("startup")
async def startup_event():
    """应用启动时初始化数据目录"""
    (DATA_DIR / "projects").mkdir(parents=True, exist_ok=True)


@app.get("/api/health", tags=["健康检查"])
async def health_check():
    return {"status": "ok", "service": "KGExtraction"}
