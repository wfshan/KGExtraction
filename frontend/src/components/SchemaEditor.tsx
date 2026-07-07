/**
 * Schema 编辑器 - 实体/关系类型定义 + 智能建议
 */
import { useEffect, useRef, useState } from 'react';
import {
    Card, Button, Input, Space, message, Spin, Empty, Popconfirm, ColorPicker, Drawer, List, Avatar, Typography, Modal, Radio, Select
} from 'antd';
import {
    PlusOutlined, DeleteOutlined, BulbOutlined, TagOutlined, SwapOutlined, MessageOutlined, RobotOutlined, UserOutlined, SendOutlined,
    DownOutlined, RightOutlined,
} from '@ant-design/icons';
import {
    getSchema, updateSchema, suggestSchema, getSchemaSources, getProfileSummaryStream, chatWithSchemaStream, generateSchemaFromChat, getRunLogs
} from '../api';
import type {
    SchemaConfig, EntityType, RelationType, SchemaSource,
} from '../api';

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
        </Card>
    );
}
