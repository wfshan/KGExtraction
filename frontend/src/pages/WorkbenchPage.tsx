/**
 * 工作台页面 - 分步向导
 * 选中项目与当前步骤持久化到 localStorage：刷新/切页回来不丢工作现场
 */
import { useEffect, useState } from 'react';
import { Steps, Card, message } from 'antd';
import {
    FolderOutlined,
    FileAddOutlined,
    ApartmentOutlined,
    ThunderboltOutlined,
    CheckCircleOutlined,
} from '@ant-design/icons';
import ProjectManager from '../components/ProjectManager';
import SystemConfig from '../components/SystemConfig';
import DocumentUpload from '../components/DocumentUpload';
import SchemaEditor from '../components/SchemaEditor';
import ExtractionRunner from '../components/ExtractionRunner';
import HumanReview from '../components/HumanReview';
import { listProjects } from '../api';

const steps = [
    { title: '项目管理', icon: <FolderOutlined /> },
    { title: '文档上传', icon: <FileAddOutlined /> },
    { title: 'Schema 配置', icon: <ApartmentOutlined /> },
    { title: '智能抽取', icon: <ThunderboltOutlined /> },
    { title: '人工复核', icon: <CheckCircleOutlined /> },
];

const LS_PROJECT_ID = 'kg_workbench_project_id';
const LS_PROJECT_NAME = 'kg_workbench_project_name';
const LS_STEP = 'kg_workbench_step';

export default function WorkbenchPage() {
    const [currentStep, setCurrentStep] = useState(() => {
        const saved = parseInt(localStorage.getItem(LS_STEP) || '0', 10);
        return Number.isFinite(saved) && saved >= 0 && saved < steps.length ? saved : 0;
    });
    const [projectId, setProjectId] = useState<string>(localStorage.getItem(LS_PROJECT_ID) || '');
    const [projectName, setProjectName] = useState<string>(localStorage.getItem(LS_PROJECT_NAME) || '');

    // 恢复的项目可能已被删除：挂载时校验一次，失效则回到第 0 步
    useEffect(() => {
        if (!projectId) return;
        listProjects().then((res) => {
            const found = res.data.find((p) => p.id === projectId);
            if (!found) {
                message.info('上次选择的项目已不存在，请重新选择');
                setProjectId('');
                setProjectName('');
                setCurrentStep(0);
            } else if (found.name !== projectName) {
                setProjectName(found.name);
            }
        }).catch(() => {
            // 加载失败不清空现场（可能是网络/鉴权问题，401 已有全局提示）
        });
    }, []);

    // 状态变化即持久化
    useEffect(() => { localStorage.setItem(LS_STEP, String(currentStep)); }, [currentStep]);
    useEffect(() => {
        localStorage.setItem(LS_PROJECT_ID, projectId);
        localStorage.setItem(LS_PROJECT_NAME, projectName);
    }, [projectId, projectName]);

    const handleProjectSelect = (id: string, name: string) => {
        // 切换到不同项目时，后续步骤的上下文已失效，回到文档上传步
        if (id && projectId && id !== projectId && currentStep > 1) {
            setCurrentStep(1);
        }
        setProjectId(id);
        setProjectName(name);
        if (id) message.success(`已选择项目: ${name}`);
    };

    const handleNext = () => {
        if (currentStep === 0 && !projectId) {
            message.warning('请先选择或创建一个项目');
            return;
        }
        setCurrentStep((prev) => Math.min(prev + 1, steps.length - 1));
    };

    const handlePrev = () => {
        setCurrentStep((prev) => Math.max(prev - 1, 0));
    };

    const renderStepContent = () => {
        switch (currentStep) {
            case 0:
                return (
                    <ProjectManager
                        selectedProjectId={projectId}
                        onSelect={handleProjectSelect}
                        onNext={handleNext}
                    />
                );
            case 1:
                return (
                    <DocumentUpload
                        projectId={projectId}
                        onNext={handleNext}
                        onPrev={handlePrev}
                    />
                );
            case 2:
                return (
                    <SchemaEditor
                        projectId={projectId}
                        onNext={handleNext}
                        onPrev={handlePrev}
                    />
                );
            case 3:
                return (
                    <ExtractionRunner
                        projectId={projectId}
                        onNext={handleNext}
                        onPrev={handlePrev}
                    />
                );
            case 4:
                return (
                    <HumanReview
                        projectId={projectId}
                        onPrev={handlePrev}
                    />
                );
            default:
                return null;
        }
    };

    return (
        <div className="fade-in">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
                <div>
                    <h1 style={{ fontSize: 24, fontWeight: 700, color: 'var(--gray-800)' }}>
                        知识图谱抽取工作台
                    </h1>
                    {projectName && (
                        <p style={{ color: 'var(--gray-500)', marginTop: 4, fontSize: 14 }}>
                            当前项目: <strong>{projectName}</strong>
                        </p>
                    )}
                </div>
                <SystemConfig />
            </div>

            <Card style={{ borderRadius: 12, marginBottom: 24 }}>
                <Steps
                    current={currentStep}
                    onChange={(step) => {
                        if (step <= currentStep || (step === currentStep + 1 && projectId)) {
                            setCurrentStep(step);
                        }
                    }}
                    items={steps.map((s) => ({
                        title: s.title,
                        icon: s.icon,
                    }))}
                />
            </Card>

            <div className="step-content">
                {renderStepContent()}
            </div>
        </div>
    );
}
