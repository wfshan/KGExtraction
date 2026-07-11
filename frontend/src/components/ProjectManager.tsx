/**
 * 项目管理组件 - 创建/选择/切换项目
 */
import { useEffect, useState } from 'react';
import {
    Card, Button, Input, Modal, Form, List, Tag, Empty, Popconfirm, message, Space,
} from 'antd';
import {
    PlusOutlined, DeleteOutlined, FolderOpenOutlined,
    CheckCircleOutlined, SyncOutlined, ClockCircleOutlined,
} from '@ant-design/icons';
import { listProjects, createProject, deleteProject } from '../api';
import type { Project } from '../api';

interface Props {
    selectedProjectId: string;
    onSelect: (id: string, name: string) => void;
    onNext: () => void;
}

const statusMap: Record<string, { color: string; icon: React.ReactNode; text: string }> = {
    idle: { color: 'default', icon: <ClockCircleOutlined />, text: '空闲' },
    running: { color: 'processing', icon: <SyncOutlined spin />, text: '运行中' },
    completed: { color: 'success', icon: <CheckCircleOutlined />, text: '已完成' },
};

export default function ProjectManager({ selectedProjectId, onSelect, onNext }: Props) {
    const [projects, setProjects] = useState<Project[]>([]);
    const [loading, setLoading] = useState(false);
    const [modalOpen, setModalOpen] = useState(false);
    const [form] = Form.useForm();

    const loadProjects = async () => {
        setLoading(true);
        try {
            const res = await listProjects();
            setProjects(res.data);
        } catch {
            message.error('加载项目列表失败');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadProjects();
    }, []);

    const handleCreate = async () => {
        try {
            const values = await form.validateFields();
            const res = await createProject(values);
            message.success('项目创建成功');
            setModalOpen(false);
            form.resetFields();
            onSelect(res.data.id, res.data.name);
            loadProjects();
        } catch {
            // validation failed
        }
    };

    const handleDelete = async (id: string) => {
        try {
            await deleteProject(id);
            message.success('项目已删除');
            if (selectedProjectId === id) {
                onSelect('', '');
            }
            loadProjects();
        } catch {
            message.error('删除失败');
        }
    };

    return (
        <Card
            title="📂 项目管理"
            extra={
                <Space>
                    <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
                        新建项目
                    </Button>
                    <Button type="primary" onClick={onNext} disabled={!selectedProjectId}>
                        使用此项目
                    </Button>
                </Space>
            }
            style={{ borderRadius: 12 }}
        >
            {projects.length === 0 ? (
                loading ? (
                    <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--gray-400)' }}>
                        加载项目列表中...
                    </div>
                ) : (
                    <Empty description="暂无项目，请创建新项目" />
                )
            ) : (
                <List
                    loading={loading}
                    dataSource={projects}
                    renderItem={(project) => {
                        const status = statusMap[project.status] || statusMap.idle;
                        const isSelected = project.id === selectedProjectId;
                        return (
                            <List.Item
                                style={{
                                    cursor: 'pointer',
                                    background: isSelected ? 'var(--primary-50)' : undefined,
                                    borderRadius: 8,
                                    padding: '12px 16px',
                                    border: isSelected ? '2px solid var(--primary-400)' : '1px solid var(--gray-200)',
                                    marginBottom: 8,
                                    transition: 'all 0.2s',
                                }}
                                onClick={() => onSelect(project.id, project.name)}
                                actions={[
                                    <Popconfirm
                                        key="delete"
                                        title="确定删除此项目？"
                                        description="项目下的文档、Schema、图谱与审计记录将全部删除，不可恢复"
                                        okType="danger"
                                        onConfirm={(e) => {
                                            e?.stopPropagation();
                                            handleDelete(project.id);
                                        }}
                                        onCancel={(e) => e?.stopPropagation()}
                                    >
                                        <Button
                                            danger
                                            size="small"
                                            icon={<DeleteOutlined />}
                                            onClick={(e) => e.stopPropagation()}
                                        />
                                    </Popconfirm>,
                                ]}
                            >
                                <List.Item.Meta
                                    avatar={
                                        <FolderOpenOutlined
                                            style={{ fontSize: 24, color: isSelected ? 'var(--primary-500)' : 'var(--gray-400)' }}
                                        />
                                    }
                                    title={
                                        <span style={{ fontWeight: isSelected ? 600 : 400 }}>
                                            {project.name}
                                            {isSelected && <Tag color="blue" style={{ marginLeft: 8 }}>当前</Tag>}
                                        </span>
                                    }
                                    description={
                                        <Space size="middle">
                                            <span>{project.description || '无描述'}</span>
                                            <Tag icon={status.icon} color={status.color}>{status.text}</Tag>
                                            <span style={{ color: 'var(--gray-400)', fontSize: 12 }}>
                                                {new Date(project.created_at).toLocaleDateString()}
                                            </span>
                                        </Space>
                                    }
                                />
                            </List.Item>
                        );
                    }}
                />
            )}

            <Modal
                title="🆕 创建新项目"
                open={modalOpen}
                onCancel={() => setModalOpen(false)}
                onOk={handleCreate}
                okText="创建"
                cancelText="取消"
            >
                <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
                    <Form.Item
                        name="name"
                        label="项目名称"
                        rules={[{ required: true, message: '请输入项目名称' }]}
                    >
                        <Input placeholder="如：金融领域知识图谱" />
                    </Form.Item>
                    <Form.Item name="description" label="项目描述">
                        <Input.TextArea rows={3} placeholder="描述该项目的抽取目标..." />
                    </Form.Item>
                </Form>
            </Modal>
        </Card>
    );
}
