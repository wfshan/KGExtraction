/**
 * 复核队列：draft 相对 published 的增量项，按风险排序（门控违规 > 未验证证据 > 低置信度），
 * 支持逐项通过/拒绝（留审计痕迹）。附审计日志与抽取被拒项视图。
 */
import { useEffect, useState } from 'react';
import {
    Table, Tag, Space, Button, message, Tooltip, Tabs, Popconfirm, Alert, Typography, Badge,
    Popover, Select, Timeline,
} from 'antd';
import {
    CheckOutlined, CloseOutlined, ReloadOutlined, WarningOutlined,
    SafetyCertificateOutlined, FileSearchOutlined, HistoryOutlined, ToolOutlined,
} from '@ant-design/icons';
import {
    getReviewQueue, postReviewDecision, getAuditLog, getRejectedItems,
    getSchema, updateSchema, updateNode,
} from '../api';
import type {
    ReviewQueue as ReviewQueueData, ReviewItem, AuditLogEntry, RejectedItemsResponse, SchemaConfig,
} from '../api';

const { Text } = Typography;

interface Props {
    projectId: string;
    onChanged?: () => void; // 裁决后通知父组件刷新图数据
}

export default function ReviewQueue({ projectId, onChanged }: Props) {
    const [queue, setQueue] = useState<ReviewQueueData | null>(null);
    const [auditLogs, setAuditLogs] = useState<AuditLogEntry[]>([]);
    const [rejected, setRejected] = useState<RejectedItemsResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [onlyPending, setOnlyPending] = useState(true);
    const [riskOnly, setRiskOnly] = useState(false);
    const [violationFilter, setViolationFilter] = useState<string | null>(null);

    const [schema, setSchema] = useState<SchemaConfig | null>(null);

    const loadAll = async () => {
        setLoading(true);
        try {
            const [q, a, r, s] = await Promise.all([
                getReviewQueue(projectId),
                getAuditLog(projectId),
                getRejectedItems(projectId),
                getSchema(projectId).catch(() => null),
            ]);
            setQueue(q.data);
            setAuditLogs(a.data.logs);
            setRejected(r.data);
            if (s) setSchema(s.data);
        } catch {
            message.error('加载复核数据失败');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { loadAll(); }, [projectId]);

    const decide = async (item: ReviewItem, decision: 'approve' | 'reject') => {
        try {
            await postReviewDecision(projectId, { kind: item.kind, target_id: item.id, decision });
            message.success(decision === 'approve' ? '已标记通过' : '已拒绝并从草稿移除');
            await loadAll();
            onChanged?.();
        } catch (err: any) {
            message.error(err.response?.data?.detail || '操作失败');
        }
    };

    // 高风险判定按抽象度分道：表面知识看违规/未验证证据/低置信度；归纳知识看支撑案例数（<2 例）
    const isHighRisk = (it: ReviewItem) => {
        if (it.violations.length > 0) return true;
        if (it.abstractness === 'inductive') return (it.support_cases ?? 0) < 2;
        return !it.evidence_verified || (it.confidence ?? 1) < 0.8;
    };

    // 违规项就地修正：改类型（写回草稿节点）/ 把类型加入 Schema，免去跳页往返
    const fixNodeType = async (item: ReviewItem, newType: string) => {
        try {
            await updateNode(projectId, item.id, { entity_type: newType } as any);
            message.success(`已将「${item.title}」的类型改为「${newType}」`);
            await loadAll();
            onChanged?.();
        } catch (err: any) {
            message.error(err.response?.data?.detail || '修改失败');
        }
    };

    const addTypeToSchema = async (typeName: string) => {
        if (!schema) return;
        if (schema.entity_types.some((et) => et.name === typeName)) {
            message.info('该类型已在 Schema 中');
            return;
        }
        try {
            await updateSchema(projectId, {
                ...schema,
                entity_types: [
                    ...schema.entity_types,
                    { name: typeName, definition: '', examples: [], color: '#5CB3FF', abstractness: 'surface' as const },
                ],
            });
            message.success(`已把「${typeName}」加入 Schema，该违规将在下次校验时解除`);
            await loadAll();
            onChanged?.();
        } catch (err: any) {
            message.error(err.response?.data?.detail || '加入 Schema 失败');
        }
    };

    const allItems = (queue?.items || []).filter(it => !onlyPending || it.review_status === 'pending');

    // 按违规原因聚合：一眼看出哪类问题贡献了最多待复核项，点击即过滤
    const violationCounts: Record<string, number> = {};
    allItems.forEach(it => it.violations.forEach(v => { violationCounts[v] = (violationCounts[v] || 0) + 1; }));

    const items = allItems
        .filter(it => !riskOnly || isHighRisk(it))
        .filter(it => !violationFilter || it.violations.includes(violationFilter));

    const queueColumns = [
        {
            title: '类型',
            dataIndex: 'kind',
            key: 'kind',
            width: 70,
            render: (kind: string) => kind === 'node' ? <Tag color="blue">实体</Tag> : <Tag color="purple">关系</Tag>,
        },
        {
            title: '内容',
            dataIndex: 'title',
            key: 'title',
            render: (title: string, record: ReviewItem) => (
                <Space direction="vertical" size={0}>
                    <Space size={4}>
                        <Text strong>{title}</Text>
                        {record.entity_type && <Tag style={{ fontSize: 11 }}>{record.entity_type}</Tag>}
                        {record.abstractness === 'inductive' && (
                            <Tooltip title="归纳知识：从案例概括的抽象知识，可信度看支撑案例数与忠实度校验，而非逐字证据">
                                <Tag color="purple" style={{ fontSize: 11 }}>归纳</Tag>
                            </Tooltip>
                        )}
                        {record.change === 'changed' && <Tag color="gold" style={{ fontSize: 11 }}>变更</Tag>}
                        {record.review_status === 'approved' && <Tag color="green" style={{ fontSize: 11 }}>已通过</Tag>}
                    </Space>
                    {record.violations.length > 0 && (
                        <Text type="danger" style={{ fontSize: 12 }}>
                            <WarningOutlined /> {record.violations[0]}
                        </Text>
                    )}
                    {record.evidence_quotes?.[0]?.quote && (
                        <Text type="secondary" style={{ fontSize: 12 }} ellipsis>
                            证据：「{record.evidence_quotes[0].quote.slice(0, 60)}…」
                        </Text>
                    )}
                </Space>
            ),
        },
        {
            // 证据语义按抽象度分道：表面知识看「逐字命中原文」，归纳知识看「忠实度校验」
            title: '证据',
            key: 'evidence',
            width: 100,
            render: (_: any, record: ReviewItem) => {
                if (record.abstractness === 'inductive') {
                    return record.evidence_verified
                        ? <Tooltip title="归纳知识：忠实度校验通过（源案例支撑其概括）"><Tag color="green" icon={<SafetyCertificateOutlined />}>忠实度通过</Tag></Tooltip>
                        : <Tooltip title="归纳知识：缺少源案例摘录支撑，需人工核实其是否有据可依"><Tag color="orange">待核实</Tag></Tooltip>;
                }
                return record.evidence_verified
                    ? <Tooltip title="表面知识：证据短句逐字命中原文"><Tag color="green" icon={<SafetyCertificateOutlined />}>已验证</Tag></Tooltip>
                    : <Tooltip title="表面知识：证据短句未能逐字命中原文，或无证据"><Tag color="red">未验证</Tag></Tooltip>;
            },
        },
        {
            // 可信度按抽象度分道：归纳知识以「支撑案例数」为客观依据，表面知识用置信度百分比
            title: '可信度',
            key: 'confidence',
            width: 100,
            render: (_: any, record: ReviewItem) => {
                if (record.abstractness === 'inductive') {
                    const n = record.support_cases ?? 0;
                    return (
                        <Tooltip title="支撑案例数：有多少个来源片段独立归纳出该知识。案例越多越可信；仅 1 例需重点复核">
                            <Tag color={n >= 2 ? 'green' : 'orange'}>{n} 例支撑</Tag>
                        </Tooltip>
                    );
                }
                const v = record.confidence;
                return <Tag color={v >= 0.8 ? 'green' : 'orange'}>{((v ?? 1) * 100).toFixed(0)}%</Tag>;
            },
        },
        {
            title: '来源',
            dataIndex: 'source_chunk_count',
            key: 'sources',
            width: 60,
        },
        {
            title: '裁决',
            key: 'actions',
            width: 140,
            render: (_: any, record: ReviewItem) => (
                <Space size="small">
                    {record.kind === 'node' && record.violations.length > 0 && (
                        <Popover
                            trigger="click"
                            title="就地修正"
                            content={
                                <Space direction="vertical" size={8} style={{ width: 240 }}>
                                    <div style={{ fontSize: 12, color: 'var(--gray-500)' }}>改为 Schema 内的类型：</div>
                                    <Select
                                        size="small"
                                        style={{ width: '100%' }}
                                        placeholder="选择目标类型"
                                        options={(schema?.entity_types || []).map((et) => ({ label: et.name, value: et.name }))}
                                        onChange={(v) => fixNodeType(record, v)}
                                    />
                                    {record.entity_type && !(schema?.entity_types || []).some((et) => et.name === record.entity_type) && (
                                        <Button size="small" block onClick={() => addTypeToSchema(record.entity_type!)}>
                                            把「{record.entity_type}」加入 Schema
                                        </Button>
                                    )}
                                </Space>
                            }
                        >
                            <Tooltip title="就地修正：改类型 / 加入 Schema">
                                <Button size="small" icon={<ToolOutlined />} />
                            </Tooltip>
                        </Popover>
                    )}
                    {record.violations.length > 0 ? (
                        // 复核通过 ≠ 门控放行：违规项即使人工通过，发布时仍会被确定性门控过滤。
                        // 不给用户"通过了就能发布"的错觉。
                        <Popconfirm
                            title="该项存在门控违规"
                            description="人工通过不会豁免门控：发布时该项仍会被过滤。建议先修正类型/Schema，或直接拒绝。仍要标记通过吗？"
                            okText="仍然通过"
                            onConfirm={() => decide(record, 'approve')}
                        >
                            <Tooltip title="通过（注意：存在门控违规）">
                                <Button size="small" icon={<CheckOutlined />} disabled={record.review_status === 'approved'} />
                            </Tooltip>
                        </Popconfirm>
                    ) : (
                        <Tooltip title="通过（留痕）">
                            <Button
                                size="small" type="primary" ghost icon={<CheckOutlined />}
                                disabled={record.review_status === 'approved'}
                                onClick={() => decide(record, 'approve')}
                            />
                        </Tooltip>
                    )}
                    <Popconfirm
                        title="拒绝并从草稿移除？"
                        description={record.kind === 'node' ? '关联关系会级联删除，操作会记入审计与反思案例' : '操作会记入审计与反思案例'}
                        onConfirm={() => decide(record, 'reject')}
                    >
                        <Tooltip title="拒绝"><Button size="small" danger icon={<CloseOutlined />} /></Tooltip>
                    </Popconfirm>
                </Space>
            ),
        },
    ];

    const rejectedColumns = [
        { title: '类别', dataIndex: 'kind', key: 'kind', width: 70, render: (k: string) => k === 'entity' ? <Tag color="blue">实体</Tag> : <Tag color="purple">关系</Tag> },
        { title: '名称', dataIndex: 'name', key: 'name', width: 200 },
        { title: '类型', dataIndex: 'item_type', key: 'item_type', width: 140, render: (t: string) => t && <Tag>{t}</Tag> },
        { title: '拒绝原因', dataIndex: 'reason', key: 'reason', render: (r: string) => <Text type="danger" style={{ fontSize: 12 }}>{r}</Text> },
        { title: '片段', dataIndex: 'chunk_id', key: 'chunk', width: 100, render: (c: string) => <Text type="secondary" style={{ fontSize: 11 }}>{c?.slice(0, 8)}</Text> },
    ];

    return (
        <div>
            {queue && (() => {
                // 治理指标首屏化：进入复核区第一眼是「哪里需要我」，而非表格
                const pendingItems = (queue.items || []).filter((it) => it.review_status === 'pending');
                const inductiveRisk = pendingItems.filter(
                    (it) => it.abstractness === 'inductive' && (it.support_cases ?? 0) < 2,
                ).length;
                const surfaceItems = pendingItems.filter((it) => it.abstractness !== 'inductive');
                const evidenceRate = surfaceItems.length > 0
                    ? Math.round((surfaceItems.filter((it) => it.evidence_verified).length / surfaceItems.length) * 100)
                    : null;
                return (
                    <div style={{ marginBottom: 12 }}>
                        <div className="review-stats">
                            <div className={`review-stat${queue.with_violations > 0 ? ' hot' : ''}`}>
                                <b>{queue.with_violations}</b><span>门控违规</span>
                            </div>
                            <div className="review-stat">
                                <b>{queue.pending}</b><span>待复核增量</span>
                            </div>
                            <div className={`review-stat${inductiveRisk > 0 ? ' warm' : ''}`}>
                                <b>{inductiveRisk}</b><span>归纳知识 &lt;2 例支撑</span>
                            </div>
                            <div className="review-stat">
                                <b>{evidenceRate == null ? '—' : `${evidenceRate}%`}</b><span>证据逐字验证率</span>
                            </div>
                        </div>
                        <Space size={8} style={{ marginTop: 8 }}>
                            <Button size="small" icon={<ReloadOutlined />} onClick={loadAll}>刷新</Button>
                            <Button size="small" onClick={() => setOnlyPending(!onlyPending)}>
                                {onlyPending ? '显示已通过的项' : '隐藏已通过的项'}
                            </Button>
                            <Tooltip title="高风险 = 门控违规 / 证据未验证 / 表面知识置信度<80% / 归纳知识仅 1 例支撑">
                                <Button size="small" type={riskOnly ? 'primary' : 'default'} onClick={() => setRiskOnly(!riskOnly)}>
                                    仅看高风险
                                </Button>
                            </Tooltip>
                        </Space>
                    </div>
                );
            })()}
            {Object.keys(violationCounts).length > 0 && (
                <Space wrap size={4} style={{ marginBottom: 12 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>按违规原因过滤：</Text>
                    {Object.entries(violationCounts).sort((a, b) => b[1] - a[1]).map(([reason, count]) => (
                        <Tag
                            key={reason}
                            color={violationFilter === reason ? 'red' : 'default'}
                            style={{ cursor: 'pointer', fontSize: 11 }}
                            onClick={() => setViolationFilter(violationFilter === reason ? null : reason)}
                        >
                            {reason} × {count}
                        </Tag>
                    ))}
                    {violationFilter && (
                        <Button size="small" type="link" onClick={() => setViolationFilter(null)}>清除过滤</Button>
                    )}
                </Space>
            )}
            <Tabs
                items={[
                    {
                        key: 'queue',
                        label: <span><FileSearchOutlined /> 复核队列 <Badge count={queue?.pending || 0} size="small" /></span>,
                        children: (
                            <Table
                                columns={queueColumns}
                                dataSource={items}
                                rowKey={(r) => `${r.kind}-${r.id}`}
                                size="small"
                                loading={loading}
                                pagination={{ pageSize: 15 }}
                                // 风险色条：红=门控违规、黄=高风险（证据/支撑案例/低置信度），凭颜色分诊
                                rowClassName={(r) => r.violations.length > 0 ? 'rq-row-crit' : isHighRisk(r) ? 'rq-row-warn' : ''}
                            />
                        ),
                    },
                    {
                        key: 'rejected',
                        label: <span><WarningOutlined /> 抽取被拒项 ({rejected?.stats.total || 0})</span>,
                        children: (
                            <div>
                                {rejected && rejected.stats.entity_types.length > 0 && (
                                    <Alert
                                        style={{ marginBottom: 12 }}
                                        type="info"
                                        showIcon
                                        message="高频被拒类型是 Schema 缺口信号，可在 Schema 配置中执行「缺口诱导」补充定义"
                                        description={
                                            <Space wrap>
                                                {rejected.stats.entity_types.slice(0, 8).map(t => (
                                                    <Tag key={t.name} color="orange">{t.name} ×{t.count}</Tag>
                                                ))}
                                            </Space>
                                        }
                                    />
                                )}
                                <Table
                                    columns={rejectedColumns}
                                    dataSource={rejected?.items || []}
                                    rowKey={(r: any) => `${r.ts}-${r.chunk_id}-${r.name}-${r.item_type}`}
                                    size="small"
                                    pagination={{ pageSize: 15 }}
                                />
                            </div>
                        ),
                    },
                    {
                        key: 'audit',
                        label: <span><HistoryOutlined /> 审计日志</span>,
                        children: (
                            // 时间线呈现「谁·何时·对什么·做了什么」，演示"全程留痕"只需滚动一屏
                            auditLogs.length === 0 ? (
                                <Text type="secondary">暂无审计记录</Text>
                            ) : (
                                <Timeline
                                    style={{ marginTop: 8 }}
                                    items={auditLogs.map((log) => ({
                                        key: log.id,
                                        color: /reject|delete|remove/.test(log.action) ? 'red'
                                            : /approve|publish/.test(log.action) ? 'green' : 'blue',
                                        children: (
                                            <div style={{ fontSize: 12.5 }}>
                                                <Space size={8} wrap>
                                                    <Text type="secondary" style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>
                                                        {log.ts?.replace('T', ' ').slice(0, 19)}
                                                    </Text>
                                                    <Tag style={{ fontSize: 11 }}>{log.actor}</Tag>
                                                    <Tag color="geekblue" style={{ fontSize: 11 }}>{log.action}</Tag>
                                                    <Text style={{ fontSize: 12 }}>{log.target_kind} · {String(log.target_id).slice(0, 24)}</Text>
                                                </Space>
                                                {log.detail && Object.keys(log.detail).length > 0 && (
                                                    <div style={{ fontSize: 11, color: 'var(--gray-400)', fontFamily: 'var(--mono)', marginTop: 2, wordBreak: 'break-all' }}>
                                                        {JSON.stringify(log.detail).slice(0, 200)}
                                                    </div>
                                                )}
                                            </div>
                                        ),
                                    }))}
                                />
                            )
                        ),
                    },
                ]}
            />
        </div>
    );
}
