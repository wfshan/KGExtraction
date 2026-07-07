/**
 * 知识治理面板：作用于草稿图的融合精炼、质量评测、反思案例。
 * 融合在人工复核之前提质——批次实体聚类、关系谓词规范化、后验本体修正。
 */
import { useEffect, useState } from 'react';
import {
    Card, Button, Space, message, Row, Col, Descriptions, Table, Tag,
    Alert, Divider, Tooltip, Progress, Typography,
} from 'antd';
import {
    ClusterOutlined, MergeCellsOutlined, ExperimentOutlined, ThunderboltOutlined,
    BulbOutlined, ReloadOutlined,
} from '@ant-design/icons';
import {
    clusterEntities, canonicalizeRelations, postCorrectGraph, fuseGraph,
    runMine1, getReflectionCases,
} from '../api';
import type { Mine1Result, ReflectionCase } from '../api';

const { Text, Paragraph } = Typography;

interface Props {
    projectId: string;
    onChanged?: () => void; // 融合改动草稿图后通知父组件刷新
}

// 将后端返回的统计 dict 渲染成描述列表（数值键值对）
function StatsView({ stats }: { stats: Record<string, any> }) {
    const entries = Object.entries(stats).filter(([, v]) => typeof v === 'number' || typeof v === 'string');
    if (entries.length === 0) return null;
    const labelMap: Record<string, string> = {
        candidate_clusters: '候选簇', merged_clusters: '已合并簇', nodes_merged: '合并节点',
        edges_redirected: '重定向边', final_nodes: '剩余节点', final_edges: '剩余边', llm_calls: 'LLM 调用',
        clusters: '谓词簇', predicates_merged: '合并谓词', edges_rewritten: '重写边',
        entity_violations: '实体违规', relation_violations: '关系违规', entities_remapped: '实体重映射',
        entities_removed: '删除实体', relations_remapped: '关系重映射', relations_removed: '删除关系',
        edges_removed_constraint: '约束删边', note: '说明',
    };
    return (
        <Descriptions size="small" column={2} bordered style={{ marginTop: 8 }}>
            {entries.map(([k, v]) => (
                <Descriptions.Item key={k} label={labelMap[k] || k}>{String(v)}</Descriptions.Item>
            ))}
        </Descriptions>
    );
}

export default function GovernancePanel({ projectId, onChanged }: Props) {
    const [busy, setBusy] = useState<string>('');
    const [clusterStats, setClusterStats] = useState<Record<string, any> | null>(null);
    const [canonStats, setCanonStats] = useState<Record<string, any> | null>(null);
    const [correctStats, setCorrectStats] = useState<Record<string, any> | null>(null);
    const [mine1, setMine1] = useState<Mine1Result | null>(null);
    const [cases, setCases] = useState<ReflectionCase[]>([]);

    const loadCases = async () => {
        try {
            const res = await getReflectionCases(projectId);
            setCases(res.data.cases);
        } catch { /* 忽略 */ }
    };
    useEffect(() => { loadCases(); }, [projectId]);

    const run = async (key: string, fn: () => Promise<any>, onOk: (data: any) => void) => {
        setBusy(key);
        try {
            const res = await fn();
            onOk(res.data);
            message.success('执行完成');
            if (key !== 'mine1') onChanged?.();
        } catch (err: any) {
            message.error(err.response?.data?.detail || '执行失败');
        } finally {
            setBusy('');
        }
    };

    const runFuseAll = async () => {
        setBusy('fuse');
        try {
            const res = await fuseGraph(projectId, { do_entity_clustering: true, do_relation_canonicalize: true, do_post_correction: true, use_llm: true });
            const d = res.data as any;
            if (d.entity_clustering) setClusterStats(d.entity_clustering);
            if (d.relation_canonicalize) setCanonStats(d.relation_canonicalize);
            if (d.post_correction) setCorrectStats(d.post_correction);
            message.success('一键融合完成');
            onChanged?.();
        } catch (err: any) {
            message.error(err.response?.data?.detail || '融合失败');
        } finally {
            setBusy('');
        }
    };

    const caseColumns = [
        { title: '类别', dataIndex: 'kind', key: 'kind', width: 70, render: (k: string) => k === 'entity' ? <Tag color="blue">实体</Tag> : <Tag color="purple">关系</Tag> },
        { title: '动作', dataIndex: 'action', key: 'action', width: 70, render: (a: string) => <Tag color={a === 'delete' ? 'red' : 'orange'}>{a === 'delete' ? '删除' : '修改'}</Tag> },
        {
            title: '修正', key: 'fix', render: (_: any, r: ReflectionCase) => {
                const before = r.before?.entity_type || r.before?.relation_type || r.before?.name || '';
                const after = r.after?.entity_type || r.after?.relation_type || r.after?.name || '';
                return <Text style={{ fontSize: 12 }}>{r.before?.name || ''} {before && after && before !== after ? `「${before}」→「${after}」` : (r.action === 'delete' ? `删除「${before}」` : '')}</Text>;
            },
        },
        { title: '时间', dataIndex: 'ts', key: 'ts', width: 160, render: (t: string) => t?.replace('T', ' ').slice(0, 19) },
    ];

    return (
        <div>
            <Alert
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
                message="融合精炼在人工复核之前对草稿图提质"
                description="高召回抽取会留下冗余：同一实体的多种写法、语义重复的关系谓词、本体违规。这里用批次算法一次性对齐，减少逐条人工复核的工作量。操作直接改动草稿图，可先在复核队列查看效果。"
            />

            <Card size="small" title={<span><MergeCellsOutlined /> 融合与精炼</span>} style={{ marginBottom: 16 }}
                extra={
                    <Tooltip title="依次执行：实体聚类 → 关系规范化 → 后验修正">
                        <Button type="primary" icon={<ThunderboltOutlined />} loading={busy === 'fuse'} onClick={runFuseAll}>
                            一键融合
                        </Button>
                    </Tooltip>
                }
            >
                <Space wrap>
                    <Button
                        icon={<ClusterOutlined />}
                        loading={busy === 'cluster'}
                        onClick={() => run('cluster', () => clusterEntities(projectId, true), setClusterStats)}
                    >
                        批次实体聚类
                    </Button>
                    <Button
                        icon={<MergeCellsOutlined />}
                        loading={busy === 'canon'}
                        onClick={() => run('canon', () => canonicalizeRelations(projectId), setCanonStats)}
                    >
                        关系谓词规范化
                    </Button>
                    <Button
                        icon={<BulbOutlined />}
                        loading={busy === 'correct'}
                        onClick={() => run('correct', () => postCorrectGraph(projectId), setCorrectStats)}
                    >
                        后验本体修正
                    </Button>
                </Space>
                {clusterStats && <><Divider style={{ margin: '12px 0 4px' }} titlePlacement="start" plain>实体聚类</Divider><StatsView stats={clusterStats} /></>}
                {canonStats && <><Divider style={{ margin: '12px 0 4px' }} titlePlacement="start" plain>关系规范化</Divider><StatsView stats={canonStats} /></>}
                {correctStats && <><Divider style={{ margin: '12px 0 4px' }} titlePlacement="start" plain>后验修正</Divider><StatsView stats={correctStats} /></>}
            </Card>

            <Card size="small" title={<span><ExperimentOutlined /> 质量评测（MINE-1 信息保留率）</span>} style={{ marginBottom: 16 }}
                extra={
                    <Button icon={<ReloadOutlined />} loading={busy === 'mine1'}
                        onClick={() => run('mine1', () => runMine1(projectId, 10, 'draft'), setMine1)}>
                        评测草稿图
                    </Button>
                }
            >
                <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
                    从原文采样生成原子事实，判断有多少被图谱三元组覆盖，衡量"抽取过程丢了多少信息"。评测本身消耗 LLM 调用。
                </Paragraph>
                {mine1 ? (
                    mine1.error ? <Alert type="warning" showIcon message={mine1.error} /> : (
                        <Row gutter={24} align="middle">
                            <Col>
                                <Progress type="dashboard" percent={Math.round((mine1.retention_rate || 0) * 100)} size={120}
                                    format={(p) => <span style={{ fontSize: 20 }}>{p}%</span>} />
                            </Col>
                            <Col flex="auto">
                                <Descriptions column={1} size="small">
                                    <Descriptions.Item label="信息保留率">{(mine1.retention_rate * 100).toFixed(1)}%</Descriptions.Item>
                                    <Descriptions.Item label="采样片段">{mine1.sampled_chunks}</Descriptions.Item>
                                    <Descriptions.Item label="原子事实">{mine1.supported_facts} / {mine1.total_facts} 被覆盖</Descriptions.Item>
                                </Descriptions>
                            </Col>
                        </Row>
                    )
                ) : (
                    <Text type="secondary">尚未评测。参考：Wikontic ≈86%，GraphRAG ≈48%，KGGen ≈44%。</Text>
                )}
            </Card>

            <Card size="small" title={<span><BulbOutlined /> 反思案例库（{cases.length}）</span>}
                extra={<Button size="small" icon={<ReloadOutlined />} onClick={loadCases}>刷新</Button>}
            >
                <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
                    你在复核中的删除/改类型会沉淀为案例，自动注入下一次抽取的提示词，让系统少犯同样的错。
                </Paragraph>
                {cases.length === 0 ? (
                    <Text type="secondary">暂无案例。在复核队列/列表中删除或修改节点、关系后，会自动记录。</Text>
                ) : (
                    <Table columns={caseColumns} dataSource={cases.slice().reverse()} rowKey={(_, i) => String(i)} size="small" pagination={{ pageSize: 8 }} />
                )}
            </Card>
        </div>
    );
}
