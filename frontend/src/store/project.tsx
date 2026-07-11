/**
 * 全局项目上下文：项目是「上下文」而非「步骤」。
 * 选中项目持久化到 localStorage（沿用旧向导的 key，升级后现场不丢）；
 * useProjectStatus 为左导航徽标提供轻量轮询（解析中/运行中/待复核/违规）。
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { listProjects, listDocuments, listRuns, getReviewQueue } from '../api';

const LS_PROJECT_ID = 'kg_workbench_project_id';
const LS_PROJECT_NAME = 'kg_workbench_project_name';

interface ProjectCtx {
    projectId: string;
    projectName: string;
    setProject: (id: string, name: string) => void;
}

const Ctx = createContext<ProjectCtx>({ projectId: '', projectName: '', setProject: () => {} });

export function ProjectProvider({ children }: { children: ReactNode }) {
    const [projectId, setProjectId] = useState(localStorage.getItem(LS_PROJECT_ID) || '');
    const [projectName, setProjectName] = useState(localStorage.getItem(LS_PROJECT_NAME) || '');

    // 恢复的项目可能已被删除：挂载时校验一次
    useEffect(() => {
        if (!projectId) return;
        listProjects().then((res) => {
            const found = res.data.find((p) => p.id === projectId);
            if (!found) {
                setProjectId('');
                setProjectName('');
            } else if (found.name !== projectName) {
                setProjectName(found.name);
            }
        }).catch(() => { /* 网络/鉴权问题不清空现场 */ });
    }, []);

    const setProject = (id: string, name: string) => {
        setProjectId(id);
        setProjectName(name);
        localStorage.setItem(LS_PROJECT_ID, id);
        localStorage.setItem(LS_PROJECT_NAME, name);
    };

    return <Ctx.Provider value={{ projectId, projectName, setProject }}>{children}</Ctx.Provider>;
}

export const useProject = () => useContext(Ctx);

export interface ProjectStatus {
    parsingDocs: number;      // 解析中的文档数 → 数据接入徽标
    running: boolean;         // 有运行中的抽取任务 → 抽取与治理脉冲
    pendingReview: number;    // 待复核增量 → 抽取与治理黄徽标
    violations: number;       // 门控违规 → 抽取与治理红徽标
}

const EMPTY: ProjectStatus = { parsingDocs: 0, running: false, pendingReview: 0, violations: 0 };

/**
 * 徽标状态轮询：20s 一次（治理状态外显的代价要足够便宜）。
 * 任一接口失败静默跳过，不打扰主流程。
 */
export function useProjectStatus(projectId: string): ProjectStatus {
    const [status, setStatus] = useState<ProjectStatus>(EMPTY);

    useEffect(() => {
        if (!projectId) { setStatus(EMPTY); return; }
        let alive = true;

        const poll = async () => {
            try {
                const [docs, runs, queue] = await Promise.allSettled([
                    listDocuments(projectId),
                    listRuns(projectId),
                    getReviewQueue(projectId),
                ]);
                if (!alive) return;
                const next = { ...EMPTY };
                if (docs.status === 'fulfilled') {
                    next.parsingDocs = docs.value.data.filter((d) => d.status === 'parsing').length;
                }
                if (runs.status === 'fulfilled') {
                    next.running = runs.value.data.some((r) => r.status === 'running');
                }
                if (queue.status === 'fulfilled') {
                    next.pendingReview = queue.value.data.pending ?? 0;
                    next.violations = queue.value.data.with_violations ?? 0;
                }
                setStatus(next);
            } catch { /* 静默 */ }
        };

        poll();
        const timer = window.setInterval(poll, 20000);
        return () => { alive = false; window.clearInterval(timer); };
    }, [projectId]);

    return status;
}
