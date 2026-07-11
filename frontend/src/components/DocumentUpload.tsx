/**
 * 文档上传组件 + 图谱数据导入（冷启动）
 */
import { useEffect, useState } from 'react';
import {
    Card, Upload, Button, Table, Tag, Space, message, Popconfirm, Empty,
    Divider, Statistic, Row, Col, Alert, Modal, Select, InputNumber
} from 'antd';
import {
    UploadOutlined, DeleteOutlined, FileTextOutlined,
    FilePdfOutlined, FileWordOutlined, FileMarkdownOutlined, FileExcelOutlined,
    DownloadOutlined, CloudUploadOutlined, NodeIndexOutlined, EyeOutlined,
} from '@ant-design/icons';
import type { UploadProps } from 'antd';
import {
    listDocuments, uploadDocument, deleteDocument,
    downloadGraphTemplate, importGraphData, getGraph, rechunkDocument,
    listDocumentChunks
} from '../api';
import type { Document, GraphData } from '../api';

interface Props {
    projectId: string;
    onNext: () => void;
    onPrev: () => void;
}

const fileTypeIcon: Record<string, React.ReactNode> = {
    pdf: <FilePdfOutlined style={{ color: '#E74C3C' }} />,
    docx: <FileWordOutlined style={{ color: '#2980B9' }} />,
    md: <FileMarkdownOutlined style={{ color: '#27AE60' }} />,
    txt: <FileTextOutlined style={{ color: '#7F8C8D' }} />,
    csv: <FileExcelOutlined style={{ color: '#16A085' }} />,
};

const statusTag: Record<string, { color: string; text: string }> = {
    uploaded: { color: 'default', text: '已上传' },
    parsing: { color: 'processing', text: '解析中' },
    parsed: { color: 'success', text: '已解析' },
    error: { color: 'error', text: '解析错误' },
};

export default function DocumentUpload({ projectId, onNext, onPrev }: Props) {
    const [docs, setDocs] = useState<Document[]>([]);
    const [loading, setLoading] = useState(false);
    const [graphData, setGraphData] = useState<GraphData | null>(null);
    const [importLoading, setImportLoading] = useState(false);



    // Rechunk Modal State
    const [rechunkModalVisible, setRechunkModalVisible] = useState(false);
    const [currentRechunkDoc, setCurrentRechunkDoc] = useState<Document | null>(null);
    const [chunkMethod, setChunkMethod] = useState<string>('fixed_length');
    const [chunkSize, setChunkSize] = useState<number>(500);
    const [chunkOverlap, setChunkOverlap] = useState<number>(100);
    const [hierarchicalLevel, setHierarchicalLevel] = useState<number>(1);
    const [rechunking, setRechunking] = useState(false);

    // Preview Modal State
    const [previewModalVisible, setPreviewModalVisible] = useState(false);
    const [previewChunks, setPreviewChunks] = useState<any[]>([]);
    const [previewLoading, setPreviewLoading] = useState(false);
    const [previewDoc, setPreviewDoc] = useState<Document | null>(null);

    const loadDocs = async (silent = false) => {
        if (!projectId) return;
        if (!silent) setLoading(true);
        try {
            const res = await listDocuments(projectId);
            setDocs(res.data);
        } catch {
            if (!silent) message.error('加载文档列表失败');
        } finally {
            if (!silent) setLoading(false);
        }
    };

    const loadGraphStats = async () => {
        if (!projectId) return;
        try {
            const res = await getGraph(projectId, 'draft');
            setGraphData(res.data);
        } catch {
            // 图谱可能不存在，忽略
        }
    };

    useEffect(() => {
        loadDocs();
        loadGraphStats();
    }, [projectId]);

    // 解析在后台进行：有文档处于「解析中」时轮询刷新，直到全部解析完成/出错
    useEffect(() => {
        const hasParsing = docs.some((d) => d.status === 'parsing');
        if (!hasParsing) return;
        const timer = window.setInterval(() => { loadDocs(true); }, 1500);
        return () => window.clearInterval(timer);
    }, [docs]);

    const handleUpload: UploadProps['customRequest'] = async (options) => {
        const { file, onSuccess, onError } = options;
        try {
            await uploadDocument(projectId, file as File);
            message.success('文档上传成功');
            onSuccess?.(null);
            loadDocs();
        } catch (err: any) {
            message.error(err.response?.data?.detail || '上传失败');
            onError?.(err);
        }
    };

    const handleDelete = async (docId: string) => {
        try {
            await deleteDocument(projectId, docId);
            message.success('文档已删除');
            loadDocs();
        } catch {
            message.error('删除失败');
        }
    };



    // ===== 重新分片逻辑 =====
    const handleOpenRechunk = (doc: Document) => {
        setCurrentRechunkDoc(doc);
        setChunkMethod(doc.chunk_method || 'fixed_length');
        setChunkSize(doc.chunk_size || 500);
        setChunkOverlap(doc.chunk_overlap || 100);
        setHierarchicalLevel(doc.hierarchical_level || 1);
        setRechunkModalVisible(true);
    };

    const handleOpenPreview = async (doc: Document) => {
        setPreviewDoc(doc);
        setPreviewChunks([]);
        setPreviewModalVisible(true);
        setPreviewLoading(true);
        try {
            const chunksRes = await listDocumentChunks(projectId, doc.id);
            setPreviewChunks(chunksRes.data);
        } catch {
            message.error('加载片段预览失败，请确认文档已解析');
        } finally {
            setPreviewLoading(false);
        }
    };

    const handleSaveRechunk = async () => {
        if (!currentRechunkDoc) return;
        setRechunking(true);
        try {
            await rechunkDocument(projectId, currentRechunkDoc.id, {
                chunk_method: chunkMethod,
                chunk_size: chunkSize,
                chunk_overlap: chunkOverlap,
                hierarchical_level: hierarchicalLevel
            });
            message.success('文档已重新分片');
            setRechunkModalVisible(false);
            loadDocs();
        } catch (err: any) {
            message.error(err.response?.data?.detail || '重新分片失败');
        } finally {
            setRechunking(false);
        }
    };

    // ===== 图谱数据导入 =====
    const handleDownloadTemplate = async () => {
        try {
            const res = await downloadGraphTemplate(projectId);
            const blob = new Blob([res.data], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `graph_template_${projectId}.json`;
            a.click();
            URL.revokeObjectURL(url);
            message.success('模版下载成功');
        } catch {
            message.error('模版下载失败');
        }
    };

    const handleGraphImport: UploadProps['customRequest'] = async (options) => {
        const { file, onSuccess, onError } = options;
        setImportLoading(true);
        try {
            const res = await importGraphData(projectId, file as File);
            const stats = res.data.stats;
            message.success(
                `导入成功：新增 ${stats.new_nodes} 个节点，${stats.new_edges} 条边` +
                (stats.merged_nodes > 0 ? `，合并 ${stats.merged_nodes} 个同名节点` : '')
            );
            onSuccess?.(null);
            loadGraphStats();
        } catch (err: any) {
            message.error(err.response?.data?.detail || '图谱数据导入失败');
            onError?.(err);
        } finally {
            setImportLoading(false);
        }
    };

    // 统计冷启动数据
    const coldStartNodes = graphData?.nodes?.filter(
        n => n.source_chunk_ids?.includes('cold_start')
    ).length || 0;
    const totalNodes = graphData?.nodes?.length || 0;
    const totalEdges = graphData?.edges?.length || 0;

    const columns = [
        {
            title: '文件名',
            dataIndex: 'original_filename',
            key: 'name',
            render: (name: string, record: Document) => (
                <Space>
                    {fileTypeIcon[record.file_type] || <FileTextOutlined />}
                    {name}
                </Space>
            ),
        },
        {
            title: '类型',
            dataIndex: 'file_type',
            key: 'type',
            width: 80,
            render: (type: string) => <Tag>{type.toUpperCase()}</Tag>,
        },
        {
            title: '大小',
            dataIndex: 'file_size',
            key: 'size',
            width: 100,
            render: (size: number) => {
                if (size < 1024) return `${size} B`;
                if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
                return `${(size / 1024 / 1024).toFixed(1)} MB`;
            },
        },
        {
            title: '状态',
            dataIndex: 'status',
            key: 'status',
            width: 100,
            render: (status: string) => {
                const s = statusTag[status] || { color: 'default', text: status };
                return <Tag color={s.color}>{s.text}</Tag>;
            },
        },
        {
            title: '切分级数&分片',
            key: 'chunks',
            width: 140,
            render: (_: any, record: Document) => (
                <Space direction="vertical" size={2}>
                    <div><span style={{color: 'var(--gray-500)'}}>{record.chunk_count} 片</span></div>
                    {record.status === 'parsed' && (
                        <Space wrap size={4}>
                            <Tag color="blue" bordered={false} style={{ margin: 0, fontSize: 10 }}>
                                {record.chunk_method === 'paragraph' ? '按段落' : 
                                 record.chunk_method === 'recursive_character' ? '递归字符' : 
                                 record.chunk_method === 'hierarchical' ? `层级(${record.hierarchical_level}级)` : '固定长度'}
                                {record.chunk_method !== 'paragraph' && record.chunk_method !== 'hierarchical' ? ` (${record.chunk_size})` : ''}
                            </Tag>
                            {(record.max_chunk_length ?? 0) > 0 && (
                                <Tag color="cyan" bordered={false} style={{ margin: 0, fontSize: 10 }}>
                                    最大: {record.max_chunk_length}字
                                </Tag>
                            )}
                        </Space>
                    )}
                </Space>
            )
        },
        {
            title: '上传时间',
            dataIndex: 'upload_time',
            key: 'time',
            width: 180,
            render: (time: string) => new Date(time).toLocaleString(),
        },
        {
            title: '操作',
            key: 'action',
            width: 200,
            render: (_: any, record: Document) => (
                <Space size="small" wrap>
                    {record.status === 'parsed' && (
                        <>
                            <Button
                                size="small"
                                onClick={() => handleOpenRechunk(record)}
                                title="重新调整切分策略"
                            >
                                切分配置
                            </Button>
                            <Button
                                size="small"
                                icon={<EyeOutlined />}
                                onClick={() => handleOpenPreview(record)}
                                title="查看切分后的分段预览"
                            >
                                预览
                            </Button>
                        </>
                    )}
                    <Popconfirm title="确定删除？该文档相关的所有片段和数据将被清除！" onConfirm={() => handleDelete(record.id)}>
                        <Button danger size="small" icon={<DeleteOutlined />} />
                    </Popconfirm>
                </Space>
            ),
        },
    ];

    return (
        <div>
            {/* 文档上传区域 */}
            <Card
                title="📄 文档上传"
                extra={
                    <Space>
                        <Button onClick={onPrev}>← 上一步</Button>
                        <Button type="primary" onClick={onNext} disabled={docs.length === 0 && totalNodes === 0}>
                            下一步 →
                        </Button>
                    </Space>
                }
                style={{ borderRadius: 12, marginBottom: 16 }}
            >
                <Upload
                    customRequest={handleUpload}
                    accept=".pdf,.txt,.md,.docx,.csv,.xlsx"
                    showUploadList={false}
                    multiple
                >
                    <Button icon={<UploadOutlined />} type="dashed" size="large" style={{ width: '100%', height: 80, marginBottom: 16 }}>
                        点击或拖拽上传文档（支持 PDF、TXT、MD、DOCX、CSV）
                    </Button>
                </Upload>

                {docs.length === 0 ? (
                    <Empty description="暂无文档，请上传" />
                ) : (
                    <Table
                        columns={columns}
                        dataSource={docs}
                        rowKey="id"
                        loading={loading}
                        pagination={false}
                        size="middle"
                    />
                )}
            </Card>

            {/* 图谱数据导入区域（冷启动） */}
            <Card
                title={<span><NodeIndexOutlined style={{ marginRight: 8 }} />🔗 图谱数据导入（冷启动）</span>}
                style={{ borderRadius: 12 }}
            >
                <Alert
                    message="直接上传已整理好的图谱数据"
                    description="您可以下载 JSON 模版，按照模版格式整理好节点和关系数据后上传。导入的数据将直接展示在图谱可视化中，后续文档抽取会在此基础上进行实体消歧和关系构建。"
                    type="info"
                    showIcon
                    style={{ marginBottom: 16 }}
                />

                <Space style={{ marginBottom: 16 }} size="middle">
                    <Button
                        icon={<DownloadOutlined />}
                        onClick={handleDownloadTemplate}
                    >
                        下载 JSON 模版
                    </Button>
                    <Upload
                        customRequest={handleGraphImport}
                        accept=".json"
                        showUploadList={false}
                    >
                        <Button
                            icon={<CloudUploadOutlined />}
                            type="primary"
                            loading={importLoading}
                        >
                            上传图谱数据
                        </Button>
                    </Upload>
                </Space>

                {totalNodes > 0 && (
                    <>
                        <Divider style={{ margin: '12px 0' }} />
                        <Row gutter={24}>
                            <Col span={8}>
                                <Statistic
                                    title="总节点数"
                                    value={totalNodes}
                                    valueStyle={{ color: '#1677FF' }}
                                />
                            </Col>
                            <Col span={8}>
                                <Statistic
                                    title="总边数"
                                    value={totalEdges}
                                    valueStyle={{ color: '#52c41a' }}
                                />
                            </Col>
                            <Col span={8}>
                                <Statistic
                                    title="冷启动节点"
                                    value={coldStartNodes}
                                    valueStyle={{ color: '#faad14' }}
                                />
                            </Col>
                        </Row>
                    </>
                )}
            </Card>



            {/* 重新切片弹窗 */}
            <Modal
                title={`重新切分文档 - ${currentRechunkDoc?.original_filename || ''}`}
                open={rechunkModalVisible}
                onOk={handleSaveRechunk}
                onCancel={() => setRechunkModalVisible(false)}
                confirmLoading={rechunking}
                width={500}
                destroyOnClose
            >
                <Alert
                    message="高级切片策略"
                    description={<>不同形式的文档适应不同的切片方式：<br/><b>递归字符：</b> RAG黄金标准，尽最大可能保持语义块完整。<br/><b>按段落切分：</b> 强行拆解大段落，适合排版规整的大型报告。<br/><b>固定长度：</b> 简单粗暴滑动切片，适合结构松散的纯文本。</>}
                    type="info"
                    showIcon
                    style={{ marginBottom: 16 }}
                />

                <div style={{ marginBottom: 16 }}>
                    <div style={{ fontWeight: 'bold', marginBottom: 8 }}>切分算法 (Strategy)</div>
                    <Select
                        value={chunkMethod}
                        onChange={setChunkMethod}
                        style={{ width: '100%' }}
                        options={[
                            { value: 'recursive_character', label: '✂️ 递归字符切分 (推荐)' },
                            { value: 'hierarchical', label: '🧩 结构化层级切分 (新)' },
                            { value: 'paragraph', label: '📖 按段落严格切分' },
                            { value: 'fixed_length', label: '📏 固定长度滑动窗口' },
                        ]}
                    />
                </div>

                {chunkMethod === 'hierarchical' && (
                    <div style={{ marginBottom: 16 }}>
                        <div style={{ fontWeight: 'bold', marginBottom: 8 }}>目标切分层级 (Level)</div>
                        <Alert
                            message="根据标题深度切分。分片内容将包含其所属的所有上级标题作为上下文。"
                            description="适用 Markdown 与带标题样式的 DOCX；PDF 无标题结构信息，仅当正文含「第X章 / 1.1 / 一、」等编号样式时可识别层级，否则将退化为整体切分。"
                            type="success"
                            style={{marginBottom: 12, fontSize: 12}}
                        />
                        <Space style={{ width: '100%' }}>
                            <span>L1</span>
                            <InputNumber 
                                min={1} max={6} 
                                value={hierarchicalLevel} 
                                onChange={(v) => setHierarchicalLevel(v || 1)}
                                style={{ width: 100 }}
                            />
                            <span>L6</span>
                            <span style={{ color: 'var(--gray-400)', fontSize: 11 }}>数字越小片段越大，包含内容越多</span>
                        </Space>
                    </div>
                )}

                {chunkMethod !== 'paragraph' && chunkMethod !== 'hierarchical' && (
                    <Row gutter={16}>
                        <Col span={12}>
                            <div style={{ fontWeight: 'bold', marginBottom: 8 }}>最大片段长度 (Size)</div>
                            <InputNumber 
                                value={chunkSize} 
                                onChange={(v) => setChunkSize(v || 500)} 
                                min={100} 
                                max={5000} 
                                style={{ width: '100%' }} 
                            />
                        </Col>
                        <Col span={12}>
                            <div style={{ fontWeight: 'bold', marginBottom: 8 }}>重叠长度 (Overlap)</div>
                            <InputNumber 
                                value={chunkOverlap} 
                                onChange={(v) => setChunkOverlap(v || 100)} 
                                min={0} 
                                max={1000} 
                                style={{ width: '100%' }} 
                            />
                        </Col>
                        <Col span={24}>
                            <div style={{ fontSize: 12, color: 'var(--gray-400)', marginTop: 8 }}>
                                提示：段落越长，消耗大模型 Token 越多，但能赋予更多全局上下文供大模型推理。
                            </div>
                        </Col>
                    </Row>
                )}
            </Modal>

            {/* 片段预览弹窗 */}
            <Modal
                title={`片段预览 - ${previewDoc?.original_filename || ''}`}
                open={previewModalVisible}
                onCancel={() => setPreviewModalVisible(false)}
                footer={[<Button key="close" onClick={() => setPreviewModalVisible(false)}>关闭</Button>]}
                width={800}
                bodyStyle={{ maxHeight: '70vh', overflowY: 'auto', backgroundColor: '#f9f9f9' }}
            >
                {previewLoading ? (
                    <div style={{ textAlign: 'center', padding: '40px 0' }}>加载中...</div>
                ) : previewChunks.length === 0 ? (
                    <Empty description="暂无片段内容" />
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                        {previewChunks.map((chunk, index) => (
                            <Card key={chunk.id} size="small" title={`片段 #${index + 1} (${chunk.text.length} 字)`} style={{ borderRadius: 8, border: '1px solid #eee' }}>
                                <pre style={{ whiteSpace: 'pre-wrap', fontSize: 13, color: '#333', maxHeight: 300, overflowY: 'auto', margin: 0 }}>
                                    {chunk.text}
                                </pre>
                                {chunk.metadata?.parents && chunk.metadata.parents.length > 0 && (
                                    <div style={{ marginTop: 8, borderTop: '1px dashed #eee', paddingTop: 8 }}>
                                        <Space size={4} wrap>
                                            <span style={{ fontSize: 11, color: '#999' }}>层级路径:</span>
                                            {chunk.metadata.parents.map((p: string, idx: number) => (
                                                <Tag key={idx} style={{ fontSize: 10 }}>{p.replace(/^#+\s+/, '')}</Tag>
                                            ))}
                                        </Space>
                                    </div>
                                )}
                            </Card>
                        ))}
                    </div>
                )}
            </Modal>
        </div >
    );
}
