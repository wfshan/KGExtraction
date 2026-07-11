/**
 * 四工作区薄封装：把原七步向导的内容无损平移进「数据接入 / 本体与计划 /
 * 抽取与治理 / 图谱与问图」。组件内部逻辑不动，onNext/onPrev 映射为工作区间跳转。
 * 未选项目时以空状态引导承接（取代旧向导的强制第一步）。
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Result, Tabs } from 'antd';
import { FolderOpenOutlined, ThunderboltOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import DocumentUpload from '../components/DocumentUpload';
import SchemaEditor from '../components/SchemaEditor';
import ExtractionRunner from '../components/ExtractionRunner';
import HumanReview from '../components/HumanReview';
import { useProject } from '../store/project';

function NeedProject({ hint }: { hint: string }) {
    return (
        <Result
            icon={<FolderOpenOutlined style={{ color: 'var(--primary-500)' }} />}
            title="先选择或创建一个项目"
            subTitle={`使用左上角的项目选择器切换项目，或点击旁边的文件夹图标新建。${hint}`}
        />
    );
}

export function IngestPage() {
    const { projectId } = useProject();
    const navigate = useNavigate();
    if (!projectId) return <NeedProject hint="项目就绪后，在这里上传文档、配置切片、或从已有图谱冷启动导入。" />;
    return (
        <div className="fade-in">
            <div className="ws-header">
                <h1>数据接入</h1>
                <p>上传文档 → 解析切片 → （可选）冷启动导入已有图谱</p>
            </div>
            <DocumentUpload
                projectId={projectId}
                onNext={() => navigate('/ontology')}
                onPrev={() => navigate('/ingest')}
            />
        </div>
    );
}

export function OntologyPage() {
    const { projectId } = useProject();
    const navigate = useNavigate();
    if (!projectId) return <NeedProject hint="项目就绪后，在这里定义本体（Schema）并编译抽取计划。" />;
    return (
        <div className="fade-in">
            <div className="ws-header">
                <h1>本体与计划</h1>
                <p>定义知识类型（标注抽象度）→ 智能规划抽取语义 → 编译出可确认的抽取计划</p>
            </div>
            <SchemaEditor
                projectId={projectId}
                onNext={() => navigate('/govern')}
                onPrev={() => navigate('/ingest')}
            />
        </div>
    );
}

export function GovernPage() {
    const { projectId } = useProject();
    const navigate = useNavigate();
    const [tab, setTab] = useState('run');
    if (!projectId) return <NeedProject hint="项目就绪后，在这里启动抽取、复核增量、发布图谱。" />;
    return (
        <div className="fade-in">
            <div className="ws-header">
                <h1>抽取与治理</h1>
                <p>运行抽取 → 复核增量（门控违规优先）→ 发布图谱，全程审计留痕</p>
            </div>
            <Tabs
                activeKey={tab}
                onChange={setTab}
                items={[
                    {
                        key: 'run',
                        label: <span><ThunderboltOutlined /> 抽取运行</span>,
                        children: (
                            <ExtractionRunner
                                projectId={projectId}
                                onNext={() => setTab('review')}
                                onPrev={() => navigate('/ontology')}
                            />
                        ),
                    },
                    {
                        key: 'review',
                        label: <span><SafetyCertificateOutlined /> 复核与发布</span>,
                        children: <HumanReview projectId={projectId} onPrev={() => setTab('run')} />,
                    },
                ]}
            />
        </div>
    );
}
