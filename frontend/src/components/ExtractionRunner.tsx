/**
 * 抽取运行器 - 抽取目标配置 + 启动任务 + 进度监控
 */
import { useEffect, useRef, useState } from 'react';
import {
    Card, Button, Space, message, Tag, Alert, Progress, Popconfirm, Result, Spin,
    Collapse, Checkbox, Divider, Modal, Descriptions
} from 'antd';
import {
    ThunderboltOutlined, SyncOutlined, CheckCircleOutlined,
    CloseCircleOutlined, ExperimentOutlined, PlayCircleOutlined,
    ReloadOutlined, WarningOutlined, RightOutlined, DownOutlined,
    FileTextOutlined
} from '@ant-design/icons';
import {
    startRun, startIncrementalRun, listRuns, getRun, resumeRun, restartRun, getRunLogs,
    listDocuments, getSchema, updateDocument, estimateRun, getExtractionPlan
} from '../api';
import type { Run, Document, SchemaConfig, RunEstimate, ExtractionPlan } from '../api';

// 步骤原语中文名（对应设计文档 6.5 步骤库）
const PRIMITIVE_LABEL: Record<string, string> = {
    segment: '文档分片', select_scope: '范围选材',
    extract_surface: '表面抽取(NER)', normalize_value: '值标准化',
    induce_from_cases: '案例归纳', aggregate_then_induce: '聚合归纳',
    extract_combined: '合并抽取(实体+关系)', extract_relations_intra: '片段内关系',
    infer_relations_cross: '跨片段关系', link_to_existing: '存量链接',
    schema_driven_linking: 'Schema驱动链接', resolve_surface: '表面消歧',
    merge_semantic: '语义归并', canonicalize_predicate: '谓词规范化',
    validate_type: '类型校验', validate_structure: '结构校验',
    verify_evidence_verbatim: '逐字证据校验', verify_faithfulness: '归纳忠实度校验',
    self_correct: '自我修正', post_correct: '后验本体修正',
    build_hierarchy: '层级构建', detect_conflict: '冲突检测',
    add_document_structure: '文档结构层',
};

interface Props {
    projectId: string;
    onNext: () => void;
    onPrev: () => void;
}

export default function ExtractionRunner({ projectId, onNext, onPrev }: Props) {
    const [run, setRun] = useState<Run | null>(null);
    const [loading, setLoading] = useState(false);
    const [isStalled, setIsStalled] = useState(false);
    const [logs, setLogs] = useState<string[]>([]);
    const [logsExpanded, setLogsExpanded] = useState(false);
    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const logScrollRef = useRef<HTMLDivElement>(null);

    // 文档目标配置
    const [docs, setDocs] = useState<Document[]>([]);
    const [schema, setSchema] = useState<SchemaConfig | null>(null);
    const [docTargets, setDocTargets] = useState<Record<string, { entities: string[]; relations: string[] }>>({});
    const [savingTargets, setSavingTargets] = useState(false);
    const [configVisible, setConfigVisible] = useState(false);

    // v3 抽取计划（只读展示）
    const [planVisible, setPlanVisible] = useState(false);
    const [plan, setPlan] = useState<ExtractionPlan | null>(null);
    const [planLoading, setPlanLoading] = useState(false);

    const openPlan = async () => {
        setPlanVisible(true);
        setPlanLoading(true);
        try {
            const res = await getExtractionPlan(projectId);
            setPlan(res.data.plan);
        } catch {
            message.error('加载抽取计划失败');
        } finally {
            setPlanLoading(false);
        }
    };

    // Staleness check
    useEffect(() => {
        if (run && run.status === 'running') {
            const timeDiff = Date.now() - new Date(run.updated_at).getTime();
            setIsStalled(timeDiff > 180000); // 3m timeout
        } else {
            setIsStalled(false);
        }
    }, [run]);

    useEffect(() => {
        loadLatestRun();
        loadDocsAndSchema();
        return () => {
            if (pollRef.current) clearInterval(pollRef.current);
        };
    }, [projectId]);

    const loadDocsAndSchema = async () => {
        if (!projectId) return;
        try {
            const [docsRes, schemaRes] = await Promise.all([
                listDocuments(projectId),
                getSchema(projectId),
            ]);
            const parsedDocs = docsRes.data.filter((d: Document) => d.status === 'parsed');
            setDocs(parsedDocs);
            setSchema(schemaRes.data);
            // 初始化每个文档的目标配置
            const targets: Record<string, { entities: string[]; relations: string[] }> = {};
            for (const doc of parsedDocs) {
                targets[doc.id] = {
                    entities: doc.target_entities || [],
                    relations: doc.target_relations || [],
                };
            }
            setDocTargets(targets);
        } catch {
            // ignore
        }
    };

    const loadLatestRun = async () => {
        try {
            const res = await listRuns(projectId);
            if (res.data.length > 0) {
                const latest = res.data[res.data.length - 1];
                setRun(latest);
                if (latest.status === 'running') {
                    startPolling(latest.id);
                }
            }
        } catch {
            // no runs yet
        }
    };

    const startPolling = (runId: string) => {
        if (pollRef.current) clearInterval(pollRef.current);
        const fetchStatusAndLogs = async () => {
            try {
                const res = await getRun(projectId, runId);
                setRun(res.data);
                if (res.data.status !== 'running') {
                    if (pollRef.current) clearInterval(pollRef.current);
                }

                // Fetch logs
                try {
                    const logRes = await getRunLogs(projectId, runId, 100);
                    setLogs(logRes.data.logs);
                    // auto scroll to bottom
                    if (logScrollRef.current) {
                        logScrollRef.current.scrollTop = logScrollRef.current.scrollHeight;
                    }
                } catch (e) {
                    console.error("Failed to fetch logs", e);
                }
            } catch {
                if (pollRef.current) clearInterval(pollRef.current);
            }
        };
        fetchStatusAndLogs();
        pollRef.current = setInterval(fetchStatusAndLogs, 1000);
    };

    // 保存所有文档的目标配置
    const saveAllTargets = async () => {
        setSavingTargets(true);
        try {
            const promises = docs.map((doc) => {
                const t = docTargets[doc.id];
                if (!t) return Promise.resolve();
                return updateDocument(projectId, doc.id, {
                    target_entities: t.entities,
                    target_relations: t.relations,
                });
            });
            await Promise.all(promises);
        } catch (err: any) {
            message.error('保存目标配置失败');
        } finally {
            setSavingTargets(false);
        }
    };

    const doStart = async () => {
        setLoading(true);
        try {
            // 先保存目标配置
            await saveAllTargets();
            const res = await startRun(projectId);
            setRun(res.data);
            message.success('抽取任务已启动');
            startPolling(res.data.id);
        } catch (err: any) {
            message.error(err.response?.data?.detail || '启动失败');
        } finally {
            setLoading(false);
        }
    };

    // 启动前先展示确定性成本预估（分片数 × 启用功能 × 平均 token），由用户确认
    const handleStart = async () => {
        setLoading(true);
        let est: RunEstimate | null = null;
        try {
            const res = await estimateRun(projectId);
            est = res.data;
        } catch (err: any) {
            message.error(err.response?.data?.detail || '成本预估失败');
            setLoading(false);
            return;
        }
        setLoading(false);

        Modal.confirm({
            title: '抽取成本预估',
            width: 480,
            content: (
                <Descriptions column={1} size="small" style={{ marginTop: 12 }}>
                    <Descriptions.Item label="待处理分片">{est.total_chunks} 个</Descriptions.Item>
                    <Descriptions.Item label="每分片 LLM 调用">{est.calls_per_chunk} 次（由抽取模式与消歧/跨片段/自修正开关决定）</Descriptions.Item>
                    <Descriptions.Item label="预计调用总数">约 {est.estimated_calls} 次</Descriptions.Item>
                    <Descriptions.Item label="预计 token 消耗">
                        约 {(est.estimated_total_tokens / 1000).toFixed(0)}k（输入 {(est.estimated_input_tokens / 1000).toFixed(0)}k + 输出 {(est.estimated_output_tokens / 1000).toFixed(0)}k）
                    </Descriptions.Item>
                    {est.estimated_cost != null && (
                        <Descriptions.Item label="预计费用">约 ¥{est.estimated_cost}</Descriptions.Item>
                    )}
                    {est.token_budget > 0 && (
                        <Descriptions.Item label="token 预算">
                            {est.token_budget}{' '}
                            {est.budget_sufficient
                                ? <Tag color="green">充足</Tag>
                                : <Tag color="red">可能不足，超限将提前停止</Tag>}
                        </Descriptions.Item>
                    )}
                </Descriptions>
            ),
            okText: '确认启动',
            cancelText: '取消',
            onOk: doStart,
        });
    };

    const handleResume = async () => {
        if (!run) return;
        setLoading(true);
        try {
            const res = await resumeRun(projectId, run.id);
            setRun(res.data);
            message.success('已继续抽取任务');
            startPolling(res.data.id);
        } catch (err: any) {
            message.error(err.response?.data?.detail || '继续失败');
        } finally {
            setLoading(false);
        }
    };

    // 增量抽取：仅处理上次抽取后新上传的文档，与既有草稿合并（不清空图谱）
    const handleIncremental = async () => {
        setLoading(true);
        try {
            await saveAllTargets();
            const res = await startIncrementalRun(projectId);
            setRun(res.data);
            message.success('增量抽取已启动（仅处理新文档）');
            startPolling(res.data.id);
        } catch (err: any) {
            message.error(err.response?.data?.detail || '增量抽取启动失败');
        } finally {
            setLoading(false);
        }
    };

    const handleRestart = async () => {
        if (!run) return;
        setLoading(true);
        try {
            // 先保存目标配置
            await saveAllTargets();
            const res = await restartRun(projectId, run.id);
            setRun(res.data);
            message.success('已清空图谱，重新开始抽取');
            startPolling(res.data.id);
        } catch (err: any) {
            message.error(err.response?.data?.detail || '重启失败');
        } finally {
            setLoading(false);
        }
    };

    const getStatusInfo = (status: string) => {
        switch (status) {
            case 'running':
                return { color: '#1677FF', icon: <SyncOutlined spin />, text: '运行中' };
            case 'completed':
                return { color: '#52C41A', icon: <CheckCircleOutlined />, text: '已完成' };
            case 'failed':
                return { color: '#FF4D4F', icon: <CloseCircleOutlined />, text: '失败' };
            default:
                return { color: '#D9D9D9', icon: <ExperimentOutlined />, text: '等待中' };
        }
    };

    // 全选/全不选辅助
    const handleSelectAll = (docId: string, type: 'entities' | 'relations') => {
        if (!schema) return;
        const allNames = type === 'entities'
            ? schema.entity_types.map((et) => et.name)
            : schema.relation_types.map((rt) => rt.name);
        setDocTargets((prev) => ({
            ...prev,
            [docId]: { ...prev[docId], [type]: allNames },
        }));
    };

    const handleDeselectAll = (docId: string, type: 'entities' | 'relations') => {
        setDocTargets((prev) => ({
            ...prev,
            [docId]: { ...prev[docId], [type]: [] },
        }));
    };

    // 渲染前置目标配置面板
    const renderTargetConfig = () => {
        if (docs.length === 0) return null;
        if (!schema || (schema.entity_types.length === 0 && schema.relation_types.length === 0)) return null;

        const collapseItems = docs.map((doc) => {
            const t = docTargets[doc.id] || { entities: [], relations: [] };
            const hasConfig = t.entities.length > 0 || t.relations.length > 0;

            return {
                key: doc.id,
                label: (
                    <Space>
                        <FileTextOutlined />
                        <span>{doc.original_filename}</span>
                        {hasConfig ? (
                            <Tag color="cyan" style={{ fontSize: 10 }}>已配置专属目标</Tag>
                        ) : (
                            <Tag color="default" style={{ fontSize: 10 }}>全部抽取</Tag>
                        )}
                    </Space>
                ),
                children: (
                    <div>
                        <div style={{ marginBottom: 12 }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                                <span style={{ fontWeight: 600, color: 'var(--gray-700)' }}>目标实体类型</span>
                                <Space size="small">
                                    <Button size="small" type="link" onClick={() => handleSelectAll(doc.id, 'entities')}>全选</Button>
                                    <Button size="small" type="link" onClick={() => handleDeselectAll(doc.id, 'entities')}>全不选</Button>
                                </Space>
                            </div>
                            {schema.entity_types.length > 0 ? (
                                <Checkbox.Group
                                    options={schema.entity_types.map((et) => ({ label: et.name, value: et.name }))}
                                    value={t.entities}
                                    onChange={(vals) =>
                                        setDocTargets((prev) => ({
                                            ...prev,
                                            [doc.id]: { ...prev[doc.id], entities: vals as string[] },
                                        }))
                                    }
                                />
                            ) : (
                                <div style={{ color: 'var(--gray-400)' }}>Schema 中未定义实体类型</div>
                            )}
                        </div>

                        <Divider style={{ margin: '8px 0' }} />

                        <div>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                                <span style={{ fontWeight: 600, color: 'var(--gray-700)' }}>目标关系类型</span>
                                <Space size="small">
                                    <Button size="small" type="link" onClick={() => handleSelectAll(doc.id, 'relations')}>全选</Button>
                                    <Button size="small" type="link" onClick={() => handleDeselectAll(doc.id, 'relations')}>全不选</Button>
                                </Space>
                            </div>
                            {schema.relation_types.length > 0 ? (
                                <Checkbox.Group
                                    options={schema.relation_types.map((rt) => ({ label: rt.name, value: rt.name }))}
                                    value={t.relations}
                                    onChange={(vals) =>
                                        setDocTargets((prev) => ({
                                            ...prev,
                                            [doc.id]: { ...prev[doc.id], relations: vals as string[] },
                                        }))
                                    }
                                />
                            ) : (
                                <div style={{ color: 'var(--gray-400)' }}>Schema 中未定义关系类型</div>
                            )}
                        </div>
                    </div>
                ),
            };
        });

        return (
            <div style={{ marginBottom: 24 }}>
                <Alert
                    message="抽取目标配置"
                    description="可针对每个文档选择要抽取的实体/关系类型。不勾选则默认抽取全部 Schema 定义的类型。配置目标可节省大模型 Token 消耗并提升抽取质量。"
                    type="info"
                    showIcon
                    style={{ marginBottom: 12 }}
                />
                <Collapse items={collapseItems} size="small" />
            </div>
        );
    };

    return (
        <Card
            title="⚡ 智能抽取"
            extra={
                <Space>
                    <Button onClick={onPrev}>← 上一步</Button>
                    <Button
                        type="primary"
                        onClick={onNext}
                        disabled={!run || run.status === 'running'}
                        title={run?.status === 'failed' ? '抽取失败，但已抽取的部分结果可进入人工复核查看' : undefined}
                    >
                        下一步 →
                    </Button>
                </Space>
            }
            style={{ borderRadius: 12 }}
        >
            {!run || (run.status !== 'running' && run.status !== 'completed') ? (
                <div className="extraction-status">
                    <div style={{ fontSize: 64, color: 'var(--primary-400)', marginBottom: 16 }}>
                        <ThunderboltOutlined />
                    </div>
                    <h2 style={{ color: 'var(--gray-700)', marginBottom: 8 }}>准备就绪</h2>
                    <p style={{ color: 'var(--gray-500)', marginBottom: 24 }}>
                        文档和 Schema 配置完成，可在下方配置各文档的抽取目标，然后启动知识图谱抽取
                    </p>
                    {run?.status === 'failed' && (
                        <Alert
                            type="error"
                            message="上次抽取失败"
                            description={
                                <span>
                                    {run.error_message}
                                    <br />
                                    失败前已抽取的部分结果仍保留在草稿图中，可点击右上角「下一步」进入人工复核查看。
                                </span>
                            }
                            showIcon
                            style={{ marginBottom: 16, textAlign: 'left' }}
                        />
                    )}

                    {renderTargetConfig()}

                    <Space>
                        <Button
                            type="primary"
                            size="large"
                            icon={<ThunderboltOutlined />}
                            loading={loading || savingTargets}
                            onClick={handleStart}
                            style={{ height: 48, fontSize: 16, paddingInline: 32 }}
                        >
                            启动抽取
                        </Button>
                        <Button
                            size="large"
                            icon={<FileTextOutlined />}
                            onClick={openPlan}
                            style={{ height: 48 }}
                        >
                            查看抽取计划
                        </Button>
                    </Space>
                </div>
            ) : (
                <div className="extraction-status fade-in">
                    {isStalled ? (
                        <Alert
                            type="warning"
                            showIcon
                            icon={<WarningOutlined />}
                            message="任务似乎已中断"
                            description="长时间未收到进度更新，可能服务器重启或意外终止。您可以继续上次的进度，或者抹除重来。"
                            style={{ marginBottom: 16, textAlign: 'left' }}
                            action={
                                <Space direction="vertical">
                                    <Button size="small" type="primary" onClick={handleResume} loading={loading} icon={<PlayCircleOutlined />}>
                                        继续抽取
                                    </Button>
                                    <Popconfirm title="确定重新抽取？" description="此操作将清空当前暂存的图谱状态并从头开始！" onConfirm={handleRestart}>
                                        <Button size="small" danger icon={<ReloadOutlined />}>
                                            重新抽取
                                        </Button>
                                    </Popconfirm>
                                </Space>
                            }
                        />
                    ) : (
                        <Tag
                            icon={getStatusInfo(run.status).icon}
                            color={getStatusInfo(run.status).color}
                            style={{ fontSize: 14, padding: '4px 12px', marginBottom: 16 }}
                        >
                            {getStatusInfo(run.status).text}
                        </Tag>
                    )}

                    <Progress
                        percent={Math.round(run.progress)}
                        status={run.status === 'running' ? 'active' : (run.status as string) === 'failed' ? 'exception' : 'success'}
                        style={{ maxWidth: 500, margin: '0 auto' }}
                        strokeWidth={12}
                    />

                    <p style={{ color: 'var(--gray-500)', marginTop: 12 }}>
                        {run.current_step || '处理中...'}
                    </p>

                    <div className="stats-grid">
                        <div className="stat-card">
                            <div className="stat-value">{run.stats?.processed_chunks || 0}/{run.stats?.total_chunks || 0}</div>
                            <div className="stat-label">处理片段</div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-value">{run.stats?.entities_extracted || 0}</div>
                            <div className="stat-label">抽取实体</div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-value">{run.stats?.relations_extracted || 0}</div>
                            <div className="stat-label">抽取关系</div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-value">{run.stats?.entities_deduplicated || 0}</div>
                            <div className="stat-label">实体消歧</div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-value">
                                {((run.stats?.tokens_used || 0) / 1000).toFixed(1)}k
                            </div>
                            <div className="stat-label">token 消耗</div>
                        </div>
                    </div>

                    {(run.stats as any)?.budget_stopped && (
                        <Alert
                            type="warning"
                            showIcon
                            message="token 预算已用尽，任务提前停止"
                            description="剩余分片未处理。可在系统配置中调高「单次任务 token 预算」后点击「继续抽取」处理剩余部分，已抽取结果不受影响。"
                            style={{ marginTop: 16, textAlign: 'left' }}
                            action={
                                <Button size="small" type="primary" onClick={handleResume} loading={loading} icon={<PlayCircleOutlined />}>
                                    继续抽取
                                </Button>
                            }
                        />
                    )}

                    <div style={{
                        marginTop: 24,
                        background: 'rgba(20, 20, 20, 0.75)',
                        backdropFilter: 'blur(12px)',
                        WebkitBackdropFilter: 'blur(12px)',
                        borderRadius: 8,
                        overflow: 'hidden',
                        color: 'rgba(255, 255, 255, 0.85)',
                        boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
                        border: '1px solid rgba(255, 255, 255, 0.1)',
                    }}>
                        <div
                            onClick={() => setLogsExpanded(!logsExpanded)}
                            style={{
                                padding: '10px 16px',
                                cursor: 'pointer',
                                display: 'flex',
                                alignItems: 'center',
                                gap: 8,
                                userSelect: 'none',
                                transition: 'background 0.2s',
                            }}
                            onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(255,255,255,0.05)'}
                            onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                        >
                            {logsExpanded ? <DownOutlined style={{ fontSize: 12 }} /> : <RightOutlined style={{ fontSize: 12 }} />}
                            <span style={{ fontWeight: 600, fontSize: 13, letterSpacing: '0.5px' }}>执行过程详情</span>
                            {run.status === 'running' && (
                                <Spin size="small" style={{ marginLeft: 8 }} />
                            )}
                        </div>

                        <div
                            ref={logScrollRef}
                            style={{
                                padding: '0 16px 12px 16px',
                                fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, Courier, monospace',
                                fontSize: 13,
                                lineHeight: 1.6,
                                maxHeight: logsExpanded ? 500 : '4.8em',
                                overflowY: 'auto',
                                transition: 'max-height 0.3s ease-in-out',
                                color: '#52c41a', // Matrix green typing text
                                textShadow: '0 0 2px rgba(82, 196, 26, 0.5)',
                                textAlign: 'left',
                            }}
                        >
                            {logs.length > 0 ? (
                                logs.map((log, i) => (
                                    <div key={i} style={{ wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}>
                                        {log}
                                    </div>
                                ))
                            ) : (
                                <div style={{ color: 'rgba(255,255,255,0.45)' }}>启动数据流...</div>
                            )}
                        </div>
                    </div>

                    {run.status === 'completed' && (
                        <div style={{ marginTop: 24 }}>
                            <Result
                                status="success"
                                title="抽取完成！"
                                subTitle="请点击下一步进行人工复核，或者在下方重新配置并重新抽取"
                                style={{ padding: 0 }}
                                extra={[
                                    <Button
                                        key="toggle"
                                        icon={configVisible ? <DownOutlined /> : <RightOutlined />}
                                        onClick={() => setConfigVisible(!configVisible)}
                                    >
                                        {configVisible ? '隐藏配置' : '修改配置'}
                                    </Button>,
                                    <Button
                                        key="incremental"
                                        icon={<PlayCircleOutlined />}
                                        loading={loading}
                                        onClick={handleIncremental}
                                        title="仅抽取上次任务之后新上传的文档，结果合并进现有草稿图，不清空已有数据"
                                    >
                                        增量抽取新文档
                                    </Button>,
                                    <Popconfirm
                                        key="restart"
                                        title="确定重新抽取？"
                                        description="此操作将清空当前图谱并从头开始。若只是新上传了文档，请改用「增量抽取新文档」。"
                                        onConfirm={handleRestart}
                                    >
                                        <Button type="primary" icon={<ReloadOutlined />}>
                                            重新抽取
                                        </Button>
                                    </Popconfirm>
                                ]}
                            />
                            {configVisible && (
                                <div className="fade-in" style={{ marginTop: 24, textAlign: 'left' }}>
                                    {renderTargetConfig()}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}

            {/* v3 抽取计划查看（只读） */}
            <Modal
                title="🧬 本次抽取计划（Plan）"
                open={planVisible}
                onCancel={() => setPlanVisible(false)}
                footer={[<Button key="close" onClick={() => setPlanVisible(false)}>关闭</Button>]}
                width={640}
            >
                <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 16 }}
                    message="抽取流程由本体编译而来"
                    description="当前为默认计划（等价于系统配置的固定流程）。后续版本将支持大模型按本体规划专用流程，并让抽象知识（概念/规则）走不同的归纳与校验步骤。"
                />
                {planLoading ? (
                    <div style={{ textAlign: 'center', padding: 24 }}>加载中...</div>
                ) : plan ? (
                    <div>
                        <Descriptions size="small" column={2} style={{ marginBottom: 12 }}>
                            <Descriptions.Item label="计划来源">{plan.source === 'planner-default' ? '默认反编译' : plan.source}</Descriptions.Item>
                            <Descriptions.Item label="Schema 版本">v{plan.schema_version}</Descriptions.Item>
                            <Descriptions.Item label="校验" span={2}>
                                {plan.validated ? <Tag color="green">DAG 合法</Tag> : <Tag color="red">校验未通过</Tag>}
                            </Descriptions.Item>
                        </Descriptions>

                        <div style={{ fontWeight: 600, margin: '8px 0' }}>知识类型（抽象度）</div>
                        <Space wrap style={{ marginBottom: 16 }}>
                            {Object.values(plan.knowledge_types).map((kt) => (
                                <Tag key={kt.name} color={kt.abstractness === 'inductive' ? 'purple' : kt.abstractness === 'normalized' ? 'geekblue' : 'blue'}>
                                    {kt.name}
                                    <span style={{ opacity: 0.6, marginLeft: 4 }}>
                                        {kt.abstractness === 'surface' ? '表面' : kt.abstractness === 'normalized' ? '标准化' : '归纳'}
                                    </span>
                                </Tag>
                            ))}
                            {Object.keys(plan.knowledge_types).length === 0 && <span style={{ color: 'var(--gray-400)' }}>（未定义实体类型）</span>}
                        </Space>

                        <div style={{ fontWeight: 600, margin: '8px 0' }}>抽取步骤（{plan.steps.length}）</div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            {plan.steps.map((s, i) => (
                                <div key={s.step_id} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '8px 12px', background: 'var(--gray-50, #fafafa)', borderRadius: 6 }}>
                                    <Tag color="blue" style={{ margin: 0 }}>{i + 1}</Tag>
                                    <div style={{ flex: 1 }}>
                                        <div style={{ fontWeight: 500 }}>
                                            {PRIMITIVE_LABEL[s.primitive] || s.primitive}
                                            {s.targets.length > 0 && <span style={{ color: 'var(--gray-400)', fontSize: 12, marginLeft: 6 }}>[{s.targets.join(', ')}]</span>}
                                        </div>
                                        {s.reason && <div style={{ fontSize: 12, color: 'var(--gray-500)' }}>{s.reason}</div>}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                ) : (
                    <div style={{ color: 'var(--gray-400)' }}>暂无计划</div>
                )}
            </Modal>
        </Card>
    );
}
