/**
 * 人工复核组件 - 实体/关系可编辑、可删除、级联更新
 */
import { useEffect, useState, useMemo } from 'react';
import {
    Card, Button, Table, Tag, Space, Statistic, message, Modal, Result, Tabs,
    Input, Popconfirm, InputNumber, Tooltip, Checkbox,
} from 'antd';
import type { CheckboxChangeEvent } from 'antd/es/checkbox';
import {
    CheckOutlined, CloseOutlined, EyeOutlined,
    NodeIndexOutlined, SwapOutlined, ExportOutlined,
    EditOutlined, DeleteOutlined, SaveOutlined,
    DownOutlined,
} from '@ant-design/icons';
import type { MenuProps } from 'antd';
import { Dropdown } from 'antd';
import { useNavigate } from 'react-router-dom';
import { getGraph, publishGraph, rejectGraph, updateNode, deleteNode, updateEdge, deleteEdge } from '../api';
import type { GraphData, GraphNode, GraphEdge } from '../api';

interface Props {
    projectId: string;
    onPrev: () => void;
}

export default function HumanReview({ projectId, onPrev }: Props) {
    const [graph, setGraph] = useState<GraphData | null>(null);
    const [editingNodeId, setEditingNodeId] = useState<string | null>(null);
    const [editingEdgeId, setEditingEdgeId] = useState<string | null>(null);
    const [editNodeData, setEditNodeData] = useState<Partial<GraphNode>>({});
    const [editEdgeData, setEditEdgeData] = useState<Partial<GraphEdge>>({});
    
    // 过滤状态
    const [hideHighConfidence, setHideHighConfidence] = useState(false);
    
    const navigate = useNavigate();

    useEffect(() => {
        loadGraph();
    }, [projectId]);

    const loadGraph = async () => {
        try {
            const res = await getGraph(projectId, 'draft');
            setGraph(res.data);
        } catch {
            message.error('加载图谱数据失败');
        }
    };

    // 构建 ID → 节点名称映射，用于关系中显示实体名称
    const nodeMap = useMemo(() => {
        if (!graph) return {};
        const map: Record<string, GraphNode> = {};
        for (const n of graph.nodes) {
            map[n.id] = n;
        }
        return map;
    }, [graph]);

    // ===== 节点编辑相关 =====
    const startEditNode = (node: GraphNode) => {
        setEditingNodeId(node.id);
        setEditNodeData({ name: node.name, entity_type: node.entity_type, confidence: node.confidence });
    };

    const cancelEditNode = () => {
        setEditingNodeId(null);
        setEditNodeData({});
    };

    const saveNode = async (nodeId: string) => {
        try {
            await updateNode(projectId, nodeId, editNodeData);
            message.success('实体已更新');
            setEditingNodeId(null);
            setEditNodeData({});
            await loadGraph();
        } catch {
            message.error('更新失败');
        }
    };

    const handleDeleteNode = async (nodeId: string) => {
        try {
            const res = await deleteNode(projectId, nodeId);
            const data = res.data as any;
            message.success(`实体已删除，同时移除了 ${data.removed_edges?.length || 0} 条关联关系`);
            await loadGraph();
        } catch {
            message.error('删除失败');
        }
    };

    // ===== 关系编辑相关 =====
    const startEditEdge = (edge: GraphEdge) => {
        setEditingEdgeId(edge.id);
        setEditEdgeData({ relation_type: edge.relation_type, confidence: edge.confidence });
    };

    const cancelEditEdge = () => {
        setEditingEdgeId(null);
        setEditEdgeData({});
    };

    const saveEdge = async (edgeId: string) => {
        try {
            await updateEdge(projectId, edgeId, editEdgeData);
            message.success('关系已更新');
            setEditingEdgeId(null);
            setEditEdgeData({});
            await loadGraph();
        } catch {
            message.error('更新失败');
        }
    };

    const handleDeleteEdge = async (edgeId: string) => {
        try {
            await deleteEdge(projectId, edgeId);
            message.success('关系已删除');
            await loadGraph();
        } catch {
            message.error('删除失败');
        }
    };

    // ===== 发布/拒绝/导出 =====
    const handlePublish = () => {
        Modal.confirm({
            title: '确认发布图谱？',
            content: '发布后将创建新版本快照，并可在图谱可视化中查看。',
            okText: '确认发布',
            cancelText: '取消',
            onOk: async () => {
                try {
                    await publishGraph(projectId);
                    message.success('图谱已发布！');
                    loadGraph();
                } catch (err: any) {
                    message.error(err.response?.data?.detail || '发布失败');
                }
            },
        });
    };

    const handleReject = () => {
        Modal.confirm({
            title: '确认拒绝图谱？',
            content: '拒绝后草稿将被清空，可重新调整配置后再次抽取。',
            okText: '确认拒绝',
            okType: 'danger',
            cancelText: '取消',
            onOk: async () => {
                try {
                    await rejectGraph(projectId);
                    message.info('图谱已拒绝，可重新配置');
                    setGraph(null);
                } catch {
                    message.error('操作失败');
                }
            },
        });
    };

    // ===== 导出逻辑 =====
    const downloadFile = (content: string, filename: string, type: string) => {
        const blob = new Blob([content], { type });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
    };

    const handleExportJSON = () => {
        if (!graph) return;
        // 导出当前展示的（可能是过滤后的）数据
        const dataToExport = {
            nodes: displayNodes,
            edges: displayEdges,
            project_id: projectId,
            export_time: new Date().toISOString(),
            version: graph.version
        };
        downloadFile(JSON.stringify(dataToExport, null, 2), `graph_export_${projectId}.json`, 'application/json');
        message.success('JSON 导出成功');
    };

    const handleExportCSV = () => {
        if (!displayEdges) return;
        
        // CSV 表头
        const headers = ['源实体', '源类型', '关系类型', '目标实体', '目标类型', '置信度'];
        
        // 数据行
        const rows = displayEdges.map(edge => {
            const source = nodeMap[edge.source_id];
            const target = nodeMap[edge.target_id];
            return [
                source?.name || edge.source_id,
                source?.entity_type || 'Unknown',
                edge.relation_type,
                target?.name || edge.target_id,
                target?.entity_type || 'Unknown',
                (edge.confidence * 100).toFixed(0) + '%'
            ].map(val => `"${val}"`).join(','); // 使用引号包裹并用逗号分隔
        });
        
        const csvContent = '\uFEFF' + [headers.join(','), ...rows].join('\n'); // 添加 BOM 支持 Excel 中文
        downloadFile(csvContent, `relations_export_${projectId}.csv`, 'text/csv;charset=utf-8');
        message.success('CSV 关系列表导出成功');
    };

    const exportMenuItems: MenuProps['items'] = [
        {
            key: 'json',
            label: '导出 JSON (全量/过滤)',
            icon: <ExportOutlined />,
            onClick: handleExportJSON,
        },
        {
            key: 'csv',
            label: '导出 CSV (仅关系列表)',
            icon: <NodeIndexOutlined />,
            onClick: handleExportCSV,
        },
    ];

    // ===== 表格列定义 =====
    const nodeColumns = [
        {
            title: '名称',
            dataIndex: 'name',
            key: 'name',
            width: 180,
            render: (name: string, record: GraphNode) =>
                editingNodeId === record.id ? (
                    <Input
                        size="small"
                        value={editNodeData.name}
                        onChange={(e) => setEditNodeData({ ...editNodeData, name: e.target.value })}
                        style={{ width: 160 }}
                    />
                ) : (
                    <span style={{ fontWeight: 500 }}>{name}</span>
                ),
        },
        {
            title: '类型',
            dataIndex: 'entity_type',
            key: 'type',
            width: 120,
            render: (type: string, record: GraphNode) =>
                editingNodeId === record.id ? (
                    <Input
                        size="small"
                        value={editNodeData.entity_type}
                        onChange={(e) => setEditNodeData({ ...editNodeData, entity_type: e.target.value })}
                        style={{ width: 100 }}
                    />
                ) : (
                    <Tag color="blue">{type}</Tag>
                ),
        },
        {
            title: '置信度',
            dataIndex: 'confidence',
            key: 'confidence',
            width: 100,
            render: (v: number, record: GraphNode) =>
                editingNodeId === record.id ? (
                    <InputNumber
                        size="small"
                        min={0} max={1} step={0.1}
                        value={editNodeData.confidence}
                        onChange={(val) => setEditNodeData({ ...editNodeData, confidence: val ?? 1 })}
                        style={{ width: 80 }}
                    />
                ) : (
                    <Tag color={v >= 0.8 ? 'green' : 'orange'}>{(v * 100).toFixed(0)}%</Tag>
                ),
        },
        {
            title: '来源片段',
            dataIndex: 'source_chunk_ids',
            key: 'sources',
            width: 80,
            render: (ids: string[]) => ids?.length || 0,
        },
        {
            title: '关联关系',
            key: 'rel_count',
            width: 80,
            render: (_: any, record: GraphNode) => {
                const count = graph?.edges.filter(
                    (e) => e.source_id === record.id || e.target_id === record.id
                ).length || 0;
                return <Tag>{count}</Tag>;
            },
        },
        {
            title: '操作',
            key: 'actions',
            width: 120,
            render: (_: any, record: GraphNode) =>
                editingNodeId === record.id ? (
                    <Space size="small">
                        <Tooltip title="保存"><Button size="small" type="primary" icon={<SaveOutlined />} onClick={() => saveNode(record.id)} /></Tooltip>
                        <Tooltip title="取消"><Button size="small" onClick={cancelEditNode}>取消</Button></Tooltip>
                    </Space>
                ) : (
                    <Space size="small">
                        <Tooltip title="编辑"><Button size="small" icon={<EditOutlined />} onClick={() => startEditNode(record)} /></Tooltip>
                        <Popconfirm
                            title="确认删除此实体？"
                            description="关联的关系也会一并删除"
                            onConfirm={() => handleDeleteNode(record.id)}
                        >
                            <Tooltip title="删除"><Button size="small" danger icon={<DeleteOutlined />} /></Tooltip>
                        </Popconfirm>
                    </Space>
                ),
        },
    ];

    const edgeColumns = [
        {
            title: '源实体',
            dataIndex: 'source_id',
            key: 'source',
            width: 160,
            render: (id: string) => {
                const node = nodeMap[id];
                return node ? (
                    <Space size={4}>
                        <Tag color="blue" style={{ fontSize: 11 }}>{node.entity_type}</Tag>
                        <span style={{ fontWeight: 500 }}>{node.name}</span>
                    </Space>
                ) : (
                    <span style={{ color: '#999' }}>{id.slice(0, 8)}...</span>
                );
            },
        },
        {
            title: '关系',
            dataIndex: 'relation_type',
            key: 'relation',
            width: 140,
            render: (type: string, record: GraphEdge) =>
                editingEdgeId === record.id ? (
                    <Input
                        size="small"
                        value={editEdgeData.relation_type}
                        onChange={(e) => setEditEdgeData({ ...editEdgeData, relation_type: e.target.value })}
                        style={{ width: 120 }}
                    />
                ) : (
                    <Tag color="purple">{type}</Tag>
                ),
        },
        {
            title: '目标实体',
            dataIndex: 'target_id',
            key: 'target',
            width: 160,
            render: (id: string) => {
                const node = nodeMap[id];
                return node ? (
                    <Space size={4}>
                        <Tag color="blue" style={{ fontSize: 11 }}>{node.entity_type}</Tag>
                        <span style={{ fontWeight: 500 }}>{node.name}</span>
                    </Space>
                ) : (
                    <span style={{ color: '#999' }}>{id.slice(0, 8)}...</span>
                );
            },
        },
        {
            title: '置信度',
            dataIndex: 'confidence',
            key: 'conf',
            width: 100,
            render: (v: number, record: GraphEdge) =>
                editingEdgeId === record.id ? (
                    <InputNumber
                        size="small"
                        min={0} max={1} step={0.1}
                        value={editEdgeData.confidence}
                        onChange={(val) => setEditEdgeData({ ...editEdgeData, confidence: val ?? 1 })}
                        style={{ width: 80 }}
                    />
                ) : (
                    <Tag color={v >= 0.8 ? 'green' : 'orange'}>{(v * 100).toFixed(0)}%</Tag>
                ),
        },
        {
            title: '操作',
            key: 'actions',
            width: 120,
            render: (_: any, record: GraphEdge) =>
                editingEdgeId === record.id ? (
                    <Space size="small">
                        <Tooltip title="保存"><Button size="small" type="primary" icon={<SaveOutlined />} onClick={() => saveEdge(record.id)} /></Tooltip>
                        <Tooltip title="取消"><Button size="small" onClick={cancelEditEdge}>取消</Button></Tooltip>
                    </Space>
                ) : (
                    <Space size="small">
                        <Tooltip title="编辑"><Button size="small" icon={<EditOutlined />} onClick={() => startEditEdge(record)} /></Tooltip>
                        <Popconfirm
                            title="确认删除此关系？"
                            onConfirm={() => handleDeleteEdge(record.id)}
                        >
                            <Tooltip title="删除"><Button size="small" danger icon={<DeleteOutlined />} /></Tooltip>
                        </Popconfirm>
                    </Space>
                ),
        },
    ];

    // ===== 过滤逻辑 =====
    const displayNodes = hideHighConfidence 
        ? graph?.nodes.filter(n => (n.confidence ?? 1) < 0.8) || [] 
        : graph?.nodes || [];

    const displayEdges = hideHighConfidence 
        ? graph?.edges.filter(e => (e.confidence ?? 1) < 0.8) || [] 
        : graph?.edges || [];

    const lowConfidenceCount = (graph?.nodes.filter(n => (n.confidence ?? 1) < 0.8).length || 0) +
                               (graph?.edges.filter(e => (e.confidence ?? 1) < 0.8).length || 0);

    // ===== 渲染 =====
    if (!graph || (graph.nodes.length === 0 && graph.edges.length === 0)) {
        return (
            <Card title="✅ 人工复核" style={{ borderRadius: 12 }}>
                <Result
                    status="info"
                    title="暂无抽取结果"
                    subTitle="请先完成抽取任务"
                    extra={<Button onClick={onPrev}>← 返回上一步</Button>}
                />
            </Card>
        );
    }

    return (
        <Card
            title="✅ 人工复核"
            extra={
                <Space>
                    <Button onClick={onPrev}>← 上一步</Button>
                    <Button icon={<EyeOutlined />} onClick={() => navigate(`/graph/${projectId}`)}>
                        可视化预览
                    </Button>
                    <Dropdown menu={{ items: exportMenuItems }}>
                        <Button icon={<ExportOutlined />}>
                            导出 <DownOutlined />
                        </Button>
                    </Dropdown>
                    <Button danger icon={<CloseOutlined />} onClick={handleReject}>
                        拒绝
                    </Button>
                    <Button type="primary" icon={<CheckOutlined />} onClick={handlePublish}>
                        通过发布
                    </Button>
                </Space>
            }
            style={{ borderRadius: 12 }}
        >
            {/* 概览统计 */}
            <div style={{ display: 'flex', gap: 24, marginBottom: 24 }}>
                <Statistic title="实体数" value={graph.nodes.length} prefix={<NodeIndexOutlined />} />
                <Statistic title="关系数" value={graph.edges.length} prefix={<SwapOutlined />} />
                <Statistic
                    title="状态"
                    value={graph.status === 'published' ? '已发布' : '草稿'}
                    valueStyle={{ color: graph.status === 'published' ? '#52C41A' : '#FAAD14' }}
                />
                <Statistic title="版本" value={`v${graph.version}`} />
            </div>

            {/* 顶栏控制面板 */}
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
                <Space>
                    <Checkbox 
                        checked={hideHighConfidence} 
                        onChange={(e: CheckboxChangeEvent) => setHideHighConfidence(e.target.checked)}
                    >
                        仅显示低置信度（&lt; 80%）
                    </Checkbox>
                    {lowConfidenceCount > 0 && (
                        <Tag color="error">
                            需重点复核: {lowConfidenceCount} 项
                        </Tag>
                    )}
                </Space>
            </div>

            <Tabs
                items={[
                    {
                        key: 'nodes',
                        label: `实体列表 (${displayNodes.length})`,
                        children: (
                            <Table
                                columns={nodeColumns}
                                dataSource={displayNodes}
                                rowKey="id"
                                size="small"
                                pagination={{ pageSize: 15 }}
                                scroll={{ x: 700 }}
                            />
                        ),
                    },
                    {
                        key: 'edges',
                        label: `关系列表 (${displayEdges.length})`,
                        children: (
                            <Table
                                columns={edgeColumns}
                                dataSource={displayEdges}
                                rowKey="id"
                                size="small"
                                pagination={{ pageSize: 15 }}
                                scroll={{ x: 700 }}
                            />
                        ),
                    },
                ]}
            />
        </Card>
    );
}
