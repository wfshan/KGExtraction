/**
 * Schema 编辑器 - 实体/关系类型定义 + 智能建议
 */
import { useEffect, useRef, useState } from 'react';
import {
    Card, Button, Input, Space, message, Spin, Empty, Popconfirm, ColorPicker, Drawer, List, Avatar, Typography, Modal, Radio, Select
} from 'antd';
import {
    PlusOutlined, DeleteOutlined, BulbOutlined, TagOutlined, SwapOutlined, MessageOutlined, RobotOutlined, UserOutlined, SendOutlined,
    DownOutlined, RightOutlined, NodeExpandOutlined, HistoryOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { Tag, Descriptions, Table, Tooltip, Alert, Divider } from 'antd';
import {
    getSchema, updateSchema, suggestSchema, getSchemaSources, getProfileSummaryStream, chatWithSchemaStream, generateSchemaFromChat, getRunLogs,
    getSchemaGaps, previewSchemaEvolve, applySchemaEvolve, getSchemaVersions,
    suggestExtractionPlan, previewExtractionPlan, applyExtractionPlan,
} from '../api';
import type {
    SchemaConfig, EntityType, RelationType, SchemaSource, SchemaGaps, SchemaVersion,
    TypeSemantics, ExtractionPlan,
} from '../api';

// 步骤原语中文名（精简，与 ExtractionRunner 一致）
const PLAN_PRIMITIVE_LABEL: Record<string, string> = {
    segment: '文档分片', extract_surface: '表面抽取', normalize_value: '值标准化',
    induce_from_cases: '案例归纳', extract_combined: '合并抽取', extract_relations_intra: '片段内关系',
    infer_relations_cross: '跨片段关系', resolve_surface: '表面消歧', merge_semantic: '语义归并',
    validate_type: '类型校验', validate_structure: '结构校验', verify_evidence_verbatim: '逐字证据校验',
    verify_faithfulness: '归纳忠实度校验', self_correct: '自我修正', post_correct: '后验修正',
    add_document_structure: '文档结构层',
};

const { Text } = Typography;

interface Props {
    projectId: string;
    onNext: () => void;
    onPrev: () => void;
}

const DEFAULT_COLORS = [
    '#4A90D9', '#50C878', '#FF6B6B', '#FFD93D',
    '#9B59B6', '#1ABC9C', '#E67E22', '#3498DB',
];

// 约束字段兼容单类型（string，旧数据）与多类型（string[]）
const constraintToArray = (v: string | string[] | undefined): string[] => {
    if (!v) return [];
    return Array.isArray(v) ? v.filter(Boolean) : [v].filter(Boolean);
};

// 单选保持 string（兼容旧数据消费方），多选存 string[]
const arrayToConstraint = (arr: string[]): string | string[] =>
    arr.length <= 1 ? (arr[0] || '') : arr;

export default function SchemaEditor({ projectId, onNext, onPrev }: Props) {
    const [schema, setSchema] = useState<SchemaConfig>({ entity_types: [], relation_types: [] });
    const [loading, setLoading] = useState(false);
    const [suggesting, setSuggesting] = useState(false);

    // Schema Chat State
    const [chatVisible, setChatVisible] = useState(false);
    const [chatInput, setChatInput] = useState('');
    const [chatMessages, setChatMessages] = useState<{ role: string; content: string }[]>([]);
    const [chatting, setChatting] = useState(false);
    const profileSummaryRequestedRef = useRef(false);
    const [generatingFromChat, setGeneratingFromChat] = useState(false);

    // Schema Exec Logs State
    const [schemaLogs, setSchemaLogs] = useState<string[]>([]);
    const [logsExpanded, setLogsExpanded] = useState(false);

    // Source Selection State
    const [sourceModalVisible, setSourceModalVisible] = useState(false);
    const [sourceModalAction, setSourceModalAction] = useState<'suggest' | 'chat'>('suggest');
    const [availableSources, setAvailableSources] = useState<SchemaSource[]>([]);
    const [selectedSource, setSelectedSource] = useState<string>('');
    const selectedSourceRef = useRef<string>('auto');

    // Schema 演化（缺口检测 → 诱导 → 版本化）
    const [evolveOpen, setEvolveOpen] = useState(false);
    const [gaps, setGaps] = useState<SchemaGaps | null>(null);
    const [gapsLoading, setGapsLoading] = useState(false);
    const [evolvePreview, setEvolvePreview] = useState<SchemaConfig | null>(null);
    const [previewLoading, setPreviewLoading] = useState(false);
    const [applying, setApplying] = useState(false);
    const [versions, setVersions] = useState<SchemaVersion[]>([]);

    // v3 第③步：智能规划（LLM 建议抽取语义 → 预览 Plan → 写回 Schema）
    const [plannerOpen, setPlannerOpen] = useState(false);
    const [plannerIntent, setPlannerIntent] = useState('');
    const [plannerBusy, setPlannerBusy] = useState(false);
    const [plannerApplying, setPlannerApplying] = useState(false);
    const [semantics, setSemantics] = useState<TypeSemantics[] | null>(null);
    const [planPreview, setPlanPreview] = useState<ExtractionPlan | null>(null);

    const openPlanner = () => {
        setPlannerOpen(true);
        setSemantics(null);
        setPlanPreview(null);
    };

    const runPlanner = async () => {
        setPlannerBusy(true);
        try {
            const res = await suggestExtractionPlan(projectId, plannerIntent);
            setSemantics(res.data.semantics);
            setPlanPreview(res.data.preview_plan);
        } catch (err: any) {
            message.error(err.response?.data?.detail || '智能规划失败');
        } finally {
            setPlannerBusy(false);
        }
    };

    const changeAbstractness = async (name: string, ab: 'surface' | 'normalized' | 'inductive') => {
        if (!semantics) return;
        const next = semantics.map((s) =>
            s.name === name ? { ...s, abstractness: ab, evidence_mode: ab === 'inductive' ? 'span' : 'verbatim' } : s
        );
        setSemantics(next);
        // 调整后实时重编译预览
        try {
            const res = await previewExtractionPlan(projectId, next);
            setPlanPreview(res.data.plan);
        } catch { /* 预览失败不阻塞编辑 */ }
    };

    const applyPlanner = async () => {
        if (!semantics) return;
        setPlannerApplying(true);
        try {
            await applyExtractionPlan(projectId, semantics);
            message.success('抽取计划已应用，类型抽象度已写入 Schema');
            await loadSchema();
            setPlannerOpen(false);
        } catch (err: any) {
            message.error(err.response?.data?.detail || '应用失败');
        } finally {
            setPlannerApplying(false);
        }
    };

    useEffect(() => {
        loadSchema();
    }, [projectId]);

    const loadSchema = async () => {
        if (!projectId) return;
        setLoading(true);
        try {
            const res = await getSchema(projectId);
            setSchema(res.data);
        } catch {
            // first time, empty schema
        } finally {
            setLoading(false);
        }
    };

    // 检查数据源并决定是否弹框
    const checkSourcesAndAct = async (action: 'suggest' | 'chat') => {
        try {
            const res = await getSchemaSources(projectId);
            const sources = res.data.sources;
            if (sources.length === 0) {
                message.warning('请先上传文档或导入图谱 JSON 数据');
                return;
            }
            if (sources.length === 1) {
                // 只有一个来源，直接执行
                selectedSourceRef.current = sources[0].key;
                if (action === 'suggest') {
                    doSuggest(sources[0].key);
                } else {
                    doOpenChat();
                }
            } else {
                // 多个来源，弹框选择
                setAvailableSources(sources);
                setSelectedSource(sources[0].key);
                setSourceModalAction(action);
                setSourceModalVisible(true);
            }
        } catch {
            message.error('获取数据源失败');
        }
    };

    const handleSourceModalOk = () => {
        setSourceModalVisible(false);
        selectedSourceRef.current = selectedSource;
        if (sourceModalAction === 'suggest') {
            doSuggest(selectedSource);
        } else {
            doOpenChat();
        }
    };

    // 对话配置 Drawer 打开且无历史消息时，自动请求并展示文档开场白
    useEffect(() => {
        profileSummaryRequestedRef.current = false;
    }, [projectId]);
    useEffect(() => {
        if (!chatVisible || !projectId || chatMessages.length > 0 || profileSummaryRequestedRef.current) return;
        profileSummaryRequestedRef.current = true;
        setChatMessages([{ role: 'assistant', content: '' }]);
        setChatting(true);
        getProfileSummaryStream(
            projectId,
            (chunk) => {
                setChatMessages((prev) => {
                    const next = [...prev];
                    if (next.length > 0 && next[0].role === 'assistant') next[0].content += chunk;
                    return next;
                });
            },
            () => {
                message.error('文档总结加载失败');
                setChatting(false);
            },
            () => setChatting(false),
            selectedSourceRef.current
        );
    }, [chatVisible, projectId]);

    // Poll schema.log during LLM actions
    useEffect(() => {
        let timer: number | undefined;
        if (suggesting || generatingFromChat) {
            timer = window.setInterval(async () => {
                try {
                    const res = await getRunLogs(projectId, 'schema', 100);
                    if (res.data.logs && res.data.logs.length > 0) {
                        setSchemaLogs(res.data.logs);
                    }
                } catch {
                    // Ignore transient errors
                }
            }, 1000);
        }
        return () => {
            if (timer) window.clearInterval(timer);
        };
    }, [suggesting, generatingFromChat, projectId]);

    const saveSchema = async (newSchema: SchemaConfig) => {
        try {
            await updateSchema(projectId, newSchema);
            setSchema(newSchema);
        } catch {
            message.error('保存失败');
        }
    };

    const doSuggest = async (source: string) => {
        setSuggesting(true);
        try {
            const res = await suggestSchema(projectId, source);
            setSchema(res.data);
            await updateSchema(projectId, res.data);
            message.success('Schema 建议已生成');
        } catch (err: any) {
            message.error(err.response?.data?.detail || 'Schema 建议生成失败');
        } finally {
            setSuggesting(false);
        }
    };

    const doOpenChat = () => {
        // 重置对话状态以便用新 source 重新获取开场白
        setChatMessages([]);
        profileSummaryRequestedRef.current = false;
        setChatVisible(true);
    };

    // --- Schema Chat Helpers ---
    const handleSendChatMessage = async () => {
        if (!chatInput.trim()) return;

        const newUserMsg = { role: 'user', content: chatInput.trim() };
        const newMessages = [...chatMessages, newUserMsg];
        setChatMessages(newMessages);
        setChatInput('');
        setChatting(true);

        const aiMsgIndex = newMessages.length;
        setChatMessages((prev) => [...prev, { role: 'assistant', content: '' }]);

        await chatWithSchemaStream(
            projectId,
            newMessages,
            (textChunk) => {
                setChatMessages((prev) => {
                    const updated = [...prev];
                    updated[aiMsgIndex] = { role: 'assistant', content: updated[aiMsgIndex].content + textChunk };
                    return updated;
                });
            },
            () => {
                message.error('对话出错');
                setChatting(false);
            },
            () => {
                setChatting(false);
            },
            selectedSourceRef.current
        );
    };

    const handleGenerateFromChat = async () => {
        if (chatMessages.length === 0) {
            message.warning('请先提供对话内容');
            return;
        }
        setGeneratingFromChat(true);
        try {
            const res = await generateSchemaFromChat(projectId, chatMessages, selectedSourceRef.current);
            setSchema(res.data);
            message.success('已根据对话成功生成 Schema！');
            setChatVisible(false); // 关闭侧边栏
        } catch (err: any) {
            message.error(err.response?.data?.detail || '生成 Schema 失败');
        } finally {
            setGeneratingFromChat(false);
        }
    };

    // --- Entity CRUD ---
    const addEntityType = () => {
        const newEntity: EntityType = {
            name: '',
            definition: '',
            examples: [],
            color: DEFAULT_COLORS[schema.entity_types.length % DEFAULT_COLORS.length],
        };
        const newSchema = { ...schema, entity_types: [...schema.entity_types, newEntity] };
        setSchema(newSchema);
    };

    const updateEntityType = (index: number, field: keyof EntityType, value: any) => {
        const types = [...schema.entity_types];
        types[index] = { ...types[index], [field]: value };
        const newSchema = { ...schema, entity_types: types };
        setSchema(newSchema);
    };

    const removeEntityType = (index: number) => {
        const types = schema.entity_types.filter((_, i) => i !== index);
        const newSchema = { ...schema, entity_types: types };
        saveSchema(newSchema);
    };

    // --- Relation CRUD ---
    const addRelationType = () => {
        const newRelation: RelationType = {
            name: '',
            definition: '',
            source_entity_type: '',
            target_entity_type: '',
            examples: [],
        };
        const newSchema = { ...schema, relation_types: [...schema.relation_types, newRelation] };
        setSchema(newSchema);
    };

    const updateRelationType = (index: number, field: keyof RelationType, value: any) => {
        const types = [...schema.relation_types];
        types[index] = { ...types[index], [field]: value };
        const newSchema = { ...schema, relation_types: types };
        setSchema(newSchema);
    };

    const removeRelationType = (index: number) => {
        const types = schema.relation_types.filter((_, i) => i !== index);
        const newSchema = { ...schema, relation_types: types };
        saveSchema(newSchema);
    };

    // 失焦自动保存：静默 + key 去重，避免逐字段编辑时 toast 轰炸
    const autoSave = () => {
        saveSchema(schema);
        message.success({ content: '已自动保存', key: 'schema-autosave', duration: 1 });
    };

    // 显式保存按钮：明确反馈
    const handleSave = () => {
        saveSchema(schema);
        message.success('Schema 已保存');
    };

    // ===== Schema 演化 =====
    const openEvolve = async () => {
        setEvolveOpen(true);
        setEvolvePreview(null);
        setGapsLoading(true);
        try {
            const [g, v] = await Promise.all([getSchemaGaps(projectId), getSchemaVersions(projectId)]);
            setGaps(g.data);
            setVersions(v.data.versions);
        } catch {
            message.error('检测缺口失败，请确认已完成抽取');
        } finally {
            setGapsLoading(false);
        }
    };

    const doPreviewEvolve = async () => {
        setPreviewLoading(true);
        try {
            const res = await previewSchemaEvolve(projectId);
            setEvolvePreview(res.data);
        } catch (err: any) {
            message.error(err.response?.data?.detail || '生成演化预览失败');
        } finally {
            setPreviewLoading(false);
        }
    };

    const doApplyEvolve = async () => {
        if (!evolvePreview) return;
        setApplying(true);
        try {
            const res = await applySchemaEvolve(projectId, evolvePreview, '缺口诱导演化');
            message.success(`已应用为 Schema v${res.data.version}`);
            setSchema(evolvePreview);
            setEvolvePreview(null);
            const [g, v] = await Promise.all([getSchemaGaps(projectId), getSchemaVersions(projectId)]);
            setGaps(g.data);
            setVersions(v.data.versions);
        } catch (err: any) {
            message.error(err.response?.data?.detail || '应用失败');
        } finally {
            setApplying(false);
        }
    };

    return (
        <Card
            title="🧩 Schema 配置"
            extra={
                <Space>
                    <Button onClick={onPrev}>← 上一步</Button>
                    <Button
                        onClick={() => checkSourcesAndAct('chat')}
                        icon={<MessageOutlined />}
                    >
                        对话配置
                    </Button>
                    <Button
                        onClick={() => checkSourcesAndAct('suggest')}
                        loading={suggesting}
                        icon={<BulbOutlined />}
                    >
                        智能建议
                    </Button>
                    <Tooltip title="根据抽取结果检测 Schema 未覆盖的高频类型，诱导补全并版本化">
                        <Button onClick={openEvolve} icon={<NodeExpandOutlined />}>
                            缺口演化
                        </Button>
                    </Tooltip>
                    <Tooltip title="让大模型分析类型与文档，自动判断每个类型该走表面抽取还是归纳抽取，规划专用抽取流程">
                        <Button onClick={openPlanner} icon={<ThunderboltOutlined />}>
                            智能规划
                        </Button>
                    </Tooltip>
                    <Button onClick={handleSave}>保存</Button>
                    <Button
                        type="primary"
                        onClick={() => { handleSave(); onNext(); }}
                        disabled={schema.entity_types.length === 0}
                    >
                        下一步 →
                    </Button>
                </Space>
            }
            style={{ borderRadius: 12 }}
        >
            <Spin spinning={loading} tip={'正在加载...'}>
                
                {/* 玻璃面板流式日志监控仪 */}
                {(suggesting || generatingFromChat || schemaLogs.length > 0) && (
                    <div style={{
                        marginBottom: 24,
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
                            {(suggesting || generatingFromChat) && (
                                <Spin size="small" style={{ marginLeft: 8 }} />
                            )}
                        </div>
                        
                        <div style={{ 
                            padding: '0 16px 12px 16px',
                            fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, Courier, monospace',
                            fontSize: 13,
                            lineHeight: 1.6,
                            maxHeight: logsExpanded ? 500 : '4.8em', // Roughly 3 lines unexpanded
                            overflowY: 'auto',
                            transition: 'max-height 0.3s ease-in-out',
                            color: '#52c41a', // Matrix green typing text
                            textShadow: '0 0 2px rgba(82, 196, 26, 0.5)',
                        }}>
                            {schemaLogs.length > 0 ? (
                                schemaLogs.map((log, i) => (
                                    <div key={i} style={{ wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}>
                                        {log}
                                    </div>
                                ))
                            ) : (
                                <div style={{ color: 'rgba(255,255,255,0.45)' }}>等待数据...</div>
                            )}
                        </div>
                    </div>
                )}

                {/* 实体类型 */}
                <div className="schema-section">
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                        <h3 style={{ margin: 0 }}>
                            <TagOutlined style={{ marginRight: 8 }} />
                            实体类型 ({schema.entity_types.length})
                        </h3>
                        <Button icon={<PlusOutlined />} size="small" onClick={addEntityType}>
                            添加
                        </Button>
                    </div>

                    {schema.entity_types.length === 0 ? (
                        <Empty description="暂无实体类型，可点击智能建议或手动添加" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                    ) : (
                        schema.entity_types.map((et, i) => (
                            <div key={i} className="schema-item">
                                <Space direction="vertical" style={{ width: '100%' }} size="small">
                                    <Space>
                                        <ColorPicker
                                            value={et.color}
                                            size="small"
                                            onChange={(_, hex) => updateEntityType(i, 'color', hex)}
                                        />
                                        <Input
                                            placeholder="类型名称（如：人物）"
                                            value={et.name}
                                            onChange={(e) => updateEntityType(i, 'name', e.target.value)}
                                            onBlur={autoSave}
                                            style={{ width: 200 }}
                                        />
                                        <Tooltip title="表面=文本中存在可定位（人名/日期/文件名）；标准化=表面存在但需归一（日期/金额）；归纳=原文不存在、需从案例概括（概念/规则/模式）。归纳类型将走专门的归纳抽取与忠实度校验。">
                                            <Select
                                                size="small"
                                                value={et.abstractness || 'surface'}
                                                onChange={(v) => { updateEntityType(i, 'abstractness', v); autoSave(); }}
                                                style={{ width: 108 }}
                                                options={[
                                                    { label: '表面实体', value: 'surface' },
                                                    { label: '标准化值', value: 'normalized' },
                                                    { label: '归纳知识', value: 'inductive' },
                                                ]}
                                            />
                                        </Tooltip>
                                        <Popconfirm title="确定删除？" onConfirm={() => removeEntityType(i)}>
                                            <Button danger size="small" icon={<DeleteOutlined />} />
                                        </Popconfirm>
                                    </Space>
                                    <Input
                                        placeholder="语义定义（帮助模型理解该概念）"
                                        value={et.definition}
                                        onChange={(e) => updateEntityType(i, 'definition', e.target.value)}
                                        onBlur={autoSave}
                                    />
                                    {(et.abstractness === 'inductive') && (
                                        <Alert
                                            type="warning"
                                            showIcon
                                            style={{ fontSize: 12 }}
                                            message="归纳类型：将从案例概括抽象知识，证据为源案例摘录（不逐字校验，靠归纳忠实度把关）。建议在定义中写清期望的粒度与可判别条件。"
                                        />
                                    )}
                                    <Input
                                        placeholder="示例实例（逗号分隔，如：张三, 李四）"
                                        value={et.examples.join(', ')}
                                        onChange={(e) =>
                                            updateEntityType(i, 'examples', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))
                                        }
                                        onBlur={autoSave}
                                    />
                                </Space>
                            </div>
                        ))
                    )}
                </div>

                {/* 关系类型 */}
                <div className="schema-section" style={{ marginTop: 24 }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                        <h3 style={{ margin: 0 }}>
                            <SwapOutlined style={{ marginRight: 8 }} />
                            关系类型 ({schema.relation_types.length})
                        </h3>
                        <Button icon={<PlusOutlined />} size="small" onClick={addRelationType}>
                            添加
                        </Button>
                    </div>

                    {schema.relation_types.length === 0 ? (
                        <Empty description="暂无关系类型" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                    ) : (
                        schema.relation_types.map((rt, i) => (
                            <div key={i} className="schema-item">
                                <Space direction="vertical" style={{ width: '100%' }} size="small">
                                    <Space>
                                        <Input
                                            placeholder="关系名称（如：就职于）"
                                            value={rt.name}
                                            onChange={(e) => updateRelationType(i, 'name', e.target.value)}
                                            onBlur={autoSave}
                                            style={{ width: 200 }}
                                        />
                                        <span style={{ color: 'var(--gray-400)' }}>从</span>
                                        <Select
                                            mode="tags"
                                            placeholder="源类型（可多选，空=不限）"
                                            value={constraintToArray(rt.source_entity_type)}
                                            onChange={(vals: string[]) => updateRelationType(i, 'source_entity_type', arrayToConstraint(vals))}
                                            onBlur={autoSave}
                                            options={schema.entity_types.map(et => ({ label: et.name, value: et.name }))}
                                            style={{ minWidth: 150 }}
                                            size="small"
                                        />
                                        <span style={{ color: 'var(--gray-400)' }}>→</span>
                                        <Select
                                            mode="tags"
                                            placeholder="目标类型（可多选，空=不限）"
                                            value={constraintToArray(rt.target_entity_type)}
                                            onChange={(vals: string[]) => updateRelationType(i, 'target_entity_type', arrayToConstraint(vals))}
                                            onBlur={autoSave}
                                            options={schema.entity_types.map(et => ({ label: et.name, value: et.name }))}
                                            style={{ minWidth: 150 }}
                                            size="small"
                                        />
                                        <Popconfirm title="确定删除？" onConfirm={() => removeRelationType(i)}>
                                            <Button danger size="small" icon={<DeleteOutlined />} />
                                        </Popconfirm>
                                    </Space>
                                    <Input
                                        placeholder="语义定义"
                                        value={rt.definition}
                                        onChange={(e) => updateRelationType(i, 'definition', e.target.value)}
                                        onBlur={autoSave}
                                    />
                                </Space>
                            </div>
                        ))
                    )}
                </div>
            </Spin>

            {/* 数据源选择弹窗 */}
            <Modal
                title="📂 选择数据源"
                open={sourceModalVisible}
                onOk={handleSourceModalOk}
                onCancel={() => setSourceModalVisible(false)}
                okText="确认"
                cancelText="取消"
                width={420}
            >
                <p style={{ color: 'var(--gray-600)', marginBottom: 16 }}>
                    检测到项目中同时存在多种数据来源，请选择基于哪个数据源{sourceModalAction === 'suggest' ? '生成 Schema 建议' : '进行对话配置'}：
                </p>
                <Radio.Group
                    value={selectedSource}
                    onChange={(e) => setSelectedSource(e.target.value)}
                    style={{ width: '100%' }}
                >
                    <Space direction="vertical" style={{ width: '100%' }}>
                        {availableSources.map((s) => (
                            <Radio.Button
                                key={s.key}
                                value={s.key}
                                style={{
                                    width: '100%',
                                    height: 'auto',
                                    padding: '12px 16px',
                                    borderRadius: 8,
                                    textAlign: 'left',
                                    whiteSpace: 'normal',
                                }}
                            >
                                <div style={{ fontWeight: 600 }}>{s.label}</div>
                                <div style={{ fontSize: 12, color: 'var(--gray-500)', marginTop: 4 }}>
                                    {s.key === 'documents' ? '基于已上传并解析的文档内容生成' : '基于已导入的图谱 JSON 数据生成'}
                                </div>
                            </Radio.Button>
                        ))}
                    </Space>
                </Radio.Group>
            </Modal>

            {/* 对话配置 Drawer */}
            <Drawer
                title="对话配置 Schema"
                placement="right"
                width={500}
                onClose={() => setChatVisible(false)}
                open={chatVisible}
                extra={
                    <Button
                        type="primary"
                        onClick={handleGenerateFromChat}
                        loading={generatingFromChat}
                        icon={<BulbOutlined />}
                    >
                        生成 Schema
                    </Button>
                }
            >
                <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                    <div style={{ flex: 1, overflowY: 'auto', marginBottom: 16 }}>
                        {chatMessages.length === 0 ? (
                            <Empty description="描述你想要的知识图谱内容..." />
                        ) : (
                            <List
                                dataSource={chatMessages}
                                renderItem={(msg, index) => (
                                    <List.Item
                                        key={index}
                                        style={{ borderBottom: 'none', padding: '8px 0' }}
                                    >
                                        <div style={{
                                            display: 'flex',
                                            width: '100%',
                                            justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start'
                                        }}>
                                            <Space align="start" style={{
                                                maxWidth: '85%',
                                                flexDirection: msg.role === 'user' ? 'row-reverse' : 'row'
                                            }}>
                                                <Avatar
                                                    icon={msg.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
                                                    style={{ backgroundColor: msg.role === 'user' ? '#1890ff' : '#52c41a' }}
                                                />
                                                <div style={{
                                                    padding: '10px 14px',
                                                    borderRadius: 12,
                                                    backgroundColor: msg.role === 'user' ? '#e6f7ff' : '#f6f6f6',
                                                    boxShadow: '0 1px 2px rgba(0,0,0,0.05)',
                                                    whiteSpace: 'pre-wrap',
                                                    wordBreak: 'break-word',
                                                }}>
                                                    <Text>{msg.content}</Text>
                                                </div>
                                            </Space>
                                        </div>
                                    </List.Item>
                                )}
                            />
                        )}
                    </div>
                    <Space.Compact style={{ width: '100%' }}>
                        <Input
                            placeholder="描述业务场景或想抽取的实体..."
                            value={chatInput}
                            onChange={(e) => setChatInput(e.target.value)}
                            onPressEnter={handleSendChatMessage}
                            disabled={chatting || generatingFromChat}
                        />
                        <Button
                            type="primary"
                            icon={<SendOutlined />}
                            onClick={handleSendChatMessage}
                            loading={chatting}
                            disabled={generatingFromChat}
                        />
                    </Space.Compact>
                </div>
            </Drawer>

            {/* Schema 缺口演化 Drawer */}
            <Drawer
                title={<span><NodeExpandOutlined /> Schema 缺口检测与演化</span>}
                open={evolveOpen}
                onClose={() => setEvolveOpen(false)}
                width={640}
            >
                <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 16 }}
                    message="数据驱动的 Schema 演化"
                    description="系统对比抽取结果与当前 Schema，找出未覆盖的高频类型（含抽取时被规则拒绝的 out-of-schema 类型），诱导补全并版本化。让 Schema 从一次性静态升级为可治理演化。"
                />

                <Spin spinning={gapsLoading} tip="检测缺口中...">
                    {gaps && !gaps.has_gaps && (
                        <Empty description="未检测到 Schema 缺口，当前 Schema 已覆盖抽取出的类型" />
                    )}

                    {gaps && gaps.has_gaps && (
                        <>
                            {gaps.rejected_total > 0 && (
                                <Alert type="warning" showIcon style={{ marginBottom: 12 }}
                                    message={`抽取阶段有 ${gaps.rejected_total} 项因类型不在 Schema 中被拒绝——很可能是应补充的缺口`} />
                            )}
                            {gaps.missing_entity_types.length > 0 && (
                                <div style={{ marginBottom: 12 }}>
                                    <div style={{ fontWeight: 600, marginBottom: 6 }}>缺失实体类型</div>
                                    <Space wrap>
                                        {gaps.missing_entity_types.map((g) => (
                                            <Tooltip key={g.name} title={`示例：${g.examples.join('、') || '—'}`}>
                                                <Tag color="blue">{g.name} ×{g.count}</Tag>
                                            </Tooltip>
                                        ))}
                                    </Space>
                                </div>
                            )}
                            {gaps.missing_relation_types.length > 0 && (
                                <div style={{ marginBottom: 12 }}>
                                    <div style={{ fontWeight: 600, marginBottom: 6 }}>缺失关系类型</div>
                                    <Space wrap>
                                        {gaps.missing_relation_types.map((g) => (
                                            <Tooltip key={g.name} title={`${g.source_entity_type || '*'} → ${g.target_entity_type || '*'}；示例：${g.examples.join('、') || '—'}`}>
                                                <Tag color="purple">{g.name} ×{g.count}</Tag>
                                            </Tooltip>
                                        ))}
                                    </Space>
                                </div>
                            )}

                            <Button type="primary" icon={<BulbOutlined />} loading={previewLoading} onClick={doPreviewEvolve} style={{ marginTop: 8 }}>
                                诱导并预览新版 Schema
                            </Button>
                        </>
                    )}
                </Spin>

                {evolvePreview && (
                    <>
                        <Divider titlePlacement="start" plain>演化预览（人工确认后写入新版本）</Divider>
                        <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
                            <Descriptions.Item label="实体类型">{schema.entity_types.length} → <b>{evolvePreview.entity_types.length}</b></Descriptions.Item>
                            <Descriptions.Item label="关系类型">{schema.relation_types.length} → <b>{evolvePreview.relation_types.length}</b></Descriptions.Item>
                        </Descriptions>
                        <Space wrap style={{ marginBottom: 8 }}>
                            {evolvePreview.entity_types
                                .filter((et) => !schema.entity_types.some((e) => e.name === et.name))
                                .map((et) => <Tag key={et.name} color="green">+ {et.name}</Tag>)}
                            {evolvePreview.relation_types
                                .filter((rt) => !schema.relation_types.some((r) => r.name === rt.name))
                                .map((rt) => <Tag key={rt.name} color="green">+ {rt.name}</Tag>)}
                        </Space>
                        <div>
                            <Popconfirm title="应用为新版本 Schema？" description="当前 Schema 会存为历史版本，可随时对照。" onConfirm={doApplyEvolve}>
                                <Button type="primary" loading={applying}>确认应用为新版本</Button>
                            </Popconfirm>
                            <Button style={{ marginLeft: 8 }} onClick={() => setEvolvePreview(null)}>放弃</Button>
                        </div>
                    </>
                )}

                <Divider titlePlacement="start" plain><HistoryOutlined /> 版本历史</Divider>
                {versions.length === 0 ? (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无历史版本" />
                ) : (
                    <Table
                        size="small"
                        pagination={false}
                        rowKey="version"
                        dataSource={versions.slice().reverse()}
                        columns={[
                            { title: '版本', dataIndex: 'version', key: 'v', width: 70, render: (v: number) => <Tag>v{v}</Tag> },
                            { title: '实体/关系', key: 'counts', width: 100, render: (_: any, r: SchemaVersion) => `${r.entity_types} / ${r.relation_types}` },
                            { title: '说明', dataIndex: 'note', key: 'note' },
                            { title: '时间', dataIndex: 'created_at', key: 'ts', width: 150, render: (t: string) => t?.replace('T', ' ').slice(0, 19) },
                        ]}
                    />
                )}
            </Drawer>

            {/* 智能规划 Drawer（v3 第③步：本体编译抽取流程） */}
            <Drawer
                title={<span><ThunderboltOutlined /> 智能规划抽取流程</span>}
                open={plannerOpen}
                onClose={() => setPlannerOpen(false)}
                width={680}
            >
                <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 16 }}
                    message="让本体编译出专用抽取流程"
                    description="大模型会分析每个类型的定义与文档样本，判断它该走「表面抽取」（人名/日期等可定位实体）还是「归纳抽取」（规则/概念等需从案例概括的知识），并规划出对应的抽取步骤。你只需确认，无需配置任何技术参数。"
                />

                <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
                    <Input
                        placeholder="可选：一句话描述你想抽什么（如：从审计案例中抽出可复用的风控规则）"
                        value={plannerIntent}
                        onChange={(e) => setPlannerIntent(e.target.value)}
                        onPressEnter={runPlanner}
                    />
                    <Button type="primary" icon={<ThunderboltOutlined />} loading={plannerBusy} onClick={runPlanner}>
                        开始规划
                    </Button>
                </Space.Compact>

                {plannerBusy && <div style={{ textAlign: 'center', padding: 24, color: 'var(--gray-400)' }}>大模型分析中...</div>}

                {semantics && (
                    <>
                        <Divider titlePlacement="start" plain>类型抽取语义（可调整）</Divider>
                        <Table
                            size="small"
                            pagination={false}
                            rowKey="name"
                            dataSource={semantics}
                            columns={[
                                { title: '类型', dataIndex: 'name', key: 'name', width: 130, render: (n: string) => <b>{n}</b> },
                                {
                                    title: '抽取方式', key: 'ab', width: 130,
                                    render: (_: any, r: TypeSemantics) => (
                                        <Select
                                            size="small"
                                            value={r.abstractness}
                                            onChange={(v) => changeAbstractness(r.name, v)}
                                            style={{ width: 110 }}
                                            options={[
                                                { label: '表面抽取', value: 'surface' },
                                                { label: '标准化', value: 'normalized' },
                                                { label: '归纳抽取', value: 'inductive' },
                                            ]}
                                        />
                                    ),
                                },
                                { title: '判断依据', dataIndex: 'reason', key: 'reason', render: (t: string) => <span style={{ fontSize: 12, color: 'var(--gray-500)' }}>{t}</span> },
                            ]}
                        />

                        {planPreview && (
                            <>
                                <Divider titlePlacement="start" plain>规划出的抽取步骤（{planPreview.steps.length}）</Divider>
                                <Space wrap>
                                    {planPreview.steps.map((s, i) => (
                                        <Tag key={s.step_id} color={['induce_from_cases', 'verify_faithfulness'].includes(s.primitive) ? 'purple' : 'blue'}>
                                            {i + 1}. {PLAN_PRIMITIVE_LABEL[s.primitive] || s.primitive}
                                        </Tag>
                                    ))}
                                </Space>
                            </>
                        )}

                        <div style={{ marginTop: 20 }}>
                            <Popconfirm
                                title="应用该抽取计划？"
                                description="各类型的抽取语义（表面/归纳等）将写入 Schema，随后抽取即按此计划执行。"
                                onConfirm={applyPlanner}
                            >
                                <Button type="primary" loading={plannerApplying}>确认并应用</Button>
                            </Popconfirm>
                            <Button style={{ marginLeft: 8 }} onClick={() => setPlannerOpen(false)}>取消</Button>
                        </div>
                    </>
                )}
            </Drawer>
        </Card>
    );
}
