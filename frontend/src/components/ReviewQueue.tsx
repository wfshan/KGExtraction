/**
 * 复核队列：draft 相对 published 的增量项，按风险排序（门控违规 > 未验证证据 > 低置信度），
 * 支持逐项通过/拒绝（留审计痕迹）。附审计日志与抽取被拒项视图。
 */
import { useEffect, useState } from 'react';
import {
    Table, Tag, Space, Button, message, Tooltip, Tabs, Popconfirm, Alert, Typography, Badge,
} from 'antd';
import {
    CheckOutlined, CloseOutlined, ReloadOutlined, WarningOutlined,
    SafetyCertificateOutlined, FileSearchOutlined, HistoryOutlined,
} from '@ant-design/icons';
import {
    getReviewQueue, postReviewDecision, getAuditLog, getRejectedItems,
} from '../api';
import type { ReviewQueue as ReviewQueueData, ReviewItem, AuditLogEntry, RejectedItemsResponse } from '../api';

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

    const loadAll = async () => {
        setLoading(true);
        try {
            const [q, a, r] = await Promise.all([
                getReviewQueue(projectId),
                getAuditLog(projectId),
                getRejectedItems(projectId),
            ]);
            setQueue(q.data);
            setAuditLogs(a.data.logs);
            setRejected(r.data);
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
            width: 110,
            render: (_: any, record: ReviewItem) => (
                <Space size="small">
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

    const auditColumns = [
        { title: '时间', dataIndex: 'ts', key: 'ts', width: 175, render: (ts: string) => ts?.replace('T', ' ').slice(0, 19) },
        { title: '操作人', dataIndex: 'actor', key: 'actor', width: 110, render: (a: string) => <Tag>{a}</Tag> },
        { title: '动作', dataIndex: 'action', key: 'action', width: 130, render: (a: string) => <Tag color="geekblue">{a}</Tag> },
        {
            title: '详情',
            dataIndex: 'detail',
            key: 'detail',
            render: (d: Record<string, any>) => <Text type="secondary" style={{ fontSize: 12 }}>{JSON.stringify(d)}</Text>,
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
            {queue && (
                <Alert
                    style={{ marginBottom: 12 }}
                    type={queue.with_violations > 0 ? 'warning' : 'info'}
                    showIcon
                    message={
                        <Space size="large">
                            <span>待复核增量 <b>{queue.pending}</b> / {queue.total} 项</span>
                            <span>门控违规 <b style={{ color: '#cf1322' }}>{queue.with_violations}</b></span>
                            <span>证据未验证 <b style={{ color: '#d46b08' }}>{queue.unverified_evidence}</b></span>
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
                    }
                />
            )}
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
                            <Table
                                columns={auditColumns}
                                dataSource={auditLogs}
                                rowKey="id"
                                size="small"
                                pagination={{ pageSize: 15 }}
                            />
                        ),
                    },
                ]}
            />
        </div>
    );
}
