"""
项目管理路由
"""
import json
from pathlib import Path
from typing import List
from fastapi import APIRouter, HTTPException
from models.project import Project, ProjectCreate
from config import DATA_DIR, get_project_dir

router = APIRouter()

PROJECTS_FILE = DATA_DIR / "projects.json"


def _load_projects() -> List[Project]:
    """加载项目列表"""
    if not PROJECTS_FILE.exists():
        return []
    with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [Project(**p) for p in data]


def _save_projects(projects: List[Project]):
    """保存项目列表"""
    with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
        json.dump([p.model_dump() for p in projects], f, ensure_ascii=False, indent=2)


@router.post("", response_model=Project)
async def create_project(req: ProjectCreate):
    """创建新项目"""
    projects = _load_projects()
    project = Project(name=req.name, description=req.description)
    projects.append(project)
    _save_projects(projects)
    # 初始化项目目录结构
    project_dir = get_project_dir(project.id)
    (project_dir / "documents").mkdir(exist_ok=True)
    (project_dir / "chunks").mkdir(exist_ok=True)
    (project_dir / "runs").mkdir(exist_ok=True)
    return project


@router.get("", response_model=List[Project])
async def list_projects():
    """获取项目列表"""
    return _load_projects()


@router.get("/{project_id}", response_model=Project)
async def get_project(project_id: str):
    """获取项目详情"""
    projects = _load_projects()
    for p in projects:
        if p.id == project_id:
            return p
    raise HTTPException(status_code=404, detail="项目不存在")


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    """删除项目"""
    projects = _load_projects()
    found = False
    new_projects = []
    for p in projects:
        if p.id == project_id:
            found = True
        else:
            new_projects.append(p)
    if not found:
        raise HTTPException(status_code=404, detail="项目不存在")
    _save_projects(new_projects)
    # 可选：清理项目数据目录
    import shutil
    project_dir = get_project_dir(project_id)
    if project_dir.exists():
        shutil.rmtree(project_dir)
    return {"message": "项目已删除"}
