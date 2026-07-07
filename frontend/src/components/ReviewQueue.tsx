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

    const items = (queue?.items || []).filter(it => !onlyPending || it.review_status === 'pending');

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
            title: '证据',
            key: 'evidence',
            width: 90,
            render: (_: any, record: ReviewItem) =>
                record.evidence_verified
                    ? <Tag color="green" icon={<SafetyCertificateOutlined />}>已验证</Tag>
                    : <Tooltip title="证据短句未能逐字命中原文，或无证据"><Tag color="red">未验证</Tag></Tooltip>,
        },
        {
            title: '置信度',
            dataIndex: 'confidence',
            key: 'confidence',
            width: 85,
            render: (v: number) => <Tag color={v >= 0.8 ? 'green' : 'orange'}>{((v ?? 1) * 100).toFixed(0)}%</Tag>,
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
                        </Space>
                    }
                />
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
