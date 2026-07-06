/**
 * 工作台页面 - 分步向导
 */
import { useState } from 'react';
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

const steps = [
    { title: '项目管理', icon: <FolderOutlined /> },
    { title: '文档上传', icon: <FileAddOutlined /> },
    { title: 'Schema 配置', icon: <ApartmentOutlined /> },
    { title: '智能抽取', icon: <ThunderboltOutlined /> },
    { title: '人工复核', icon: <CheckCircleOutlined /> },
];

export default function WorkbenchPage() {
    const [currentStep, setCurrentStep] = useState(0);
    const [projectId, setProjectId] = useState<string>('');
    const [projectName, setProjectName] = useState<string>('');

    const handleProjectSelect = (id: string, name: string) => {
        setProjectId(id);
        setProjectName(name);
        message.success(`已选择项目: ${name}`);
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
