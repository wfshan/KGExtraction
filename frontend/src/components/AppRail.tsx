/**
 * 左侧导航栏（应用壳）：项目选择器置顶、四工作区入口带实时状态徽标、底部系统配置。
 * 设计原则「状态外显」的载体——用户在任何位置都能感知哪里需要处理。
 */
import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { Select, Modal, Tooltip, message } from 'antd';
import {
    DeploymentUnitOutlined, DatabaseOutlined, ApartmentOutlined,
    SafetyCertificateOutlined, ShareAltOutlined, FolderOpenOutlined,
} from '@ant-design/icons';
import ProjectManager from './ProjectManager';
import SystemConfig from './SystemConfig';
import { listProjects, type Project } from '../api';
import { useProject, useProjectStatus } from '../store/project';

export default function AppRail() {
    const { projectId, projectName, setProject } = useProject();
    const status = useProjectStatus(projectId);
    const [projects, setProjects] = useState<Project[]>([]);
    const [manageOpen, setManageOpen] = useState(false);

    const loadProjects = () => {
        listProjects().then((res) => setProjects(res.data)).catch(() => {});
    };
    useEffect(loadProjects, []);

    const handleSelect = (id: string) => {
        const p = projects.find((x) => x.id === id);
        if (p) {
            setProject(p.id, p.name);
            message.success(`已切换到项目：${p.name}`);
        }
    };

    return (
        <aside className="app-rail">
            <div className="rail-brand">
                <span className="rail-brand-icon"><DeploymentUnitOutlined /></span>
                KGExtraction
            </div>

            <div className="rail-project">
                <Select
                    size="small"
                    style={{ flex: 1, minWidth: 0 }}
                    placeholder="选择项目"
                    value={projectId || undefined}
                    onOpenChange={(open) => open && loadProjects()}
                    onChange={handleSelect}
                    options={projects.map((p) => ({ label: p.name, value: p.id }))}
                    variant="borderless"
                />
                <Tooltip title="管理项目（新建/删除）">
                    <button className="rail-iconbtn" onClick={() => setManageOpen(true)} aria-label="管理项目">
                        <FolderOpenOutlined />
                    </button>
                </Tooltip>
            </div>

            <nav className="rail-nav">
                <NavLink to="/ingest" className="rail-item">
                    <DatabaseOutlined /> 数据接入
                    <span className="rail-badges">
                        {status.parsingDocs > 0 && (
                            <span className="rail-badge run pulse">{status.parsingDocs} 解析中</span>
                        )}
                    </span>
                </NavLink>
                <NavLink to="/ontology" className="rail-item">
                    <ApartmentOutlined /> 本体与计划
                </NavLink>
                <NavLink to="/govern" className="rail-item">
                    <SafetyCertificateOutlined /> 抽取与治理
                    <span className="rail-badges">
                        {status.running && <span className="rail-badge run pulse">运行中</span>}
                        {status.violations > 0 && (
                            <Tooltip title={`${status.violations} 项门控违规`}>
                                <span className="rail-badge crit">{status.violations}</span>
                            </Tooltip>
                        )}
                        {status.pendingReview > 0 && (
                            <Tooltip title={`${status.pendingReview} 项待复核`}>
                                <span className="rail-badge warn">{status.pendingReview}</span>
                            </Tooltip>
                        )}
                    </span>
                </NavLink>
                <NavLink to="/graph" className="rail-item">
                    <ShareAltOutlined /> 图谱与问图
                </NavLink>
            </nav>

            <div className="rail-foot">
                <SystemConfig />
                {projectName && <div className="rail-foot-proj">当前：{projectName}</div>}
            </div>

            <Modal
                title="项目管理"
                open={manageOpen}
                onCancel={() => setManageOpen(false)}
                footer={null}
                width={760}
                destroyOnHidden
            >
                <ProjectManager
                    selectedProjectId={projectId}
                    onSelect={(id, name) => { setProject(id, name); loadProjects(); }}
                    onNext={() => setManageOpen(false)}
                />
            </Modal>
        </aside>
    );
}
