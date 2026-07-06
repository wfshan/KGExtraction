"""
KGExtraction 后端 FastAPI 入口
"""
from fastapi import FastAPI
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
