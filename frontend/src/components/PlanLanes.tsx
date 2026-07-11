/**
 * 抽取计划泳道视图：把 Schema 编译出的 Plan 按知识抽象度分道常驻展示。
 * 归纳道（紫）/ 表面道（蓝）/ 汇合道（灰），每步 hover 显示编排理由（reason）——
 * 「本体编译抽取流程」从 Modal 里的隐藏能力升级为页面一等公民。
 */
import { useEffect, useState } from 'react';
import { Tag, Tooltip, Button, Empty, Spin, Space, Typography } from 'antd';
import { ReloadOutlined, BranchesOutlined } from '@ant-design/icons';
import { getExtractionPlan, type ExtractionPlan, type ExtractionStep } from '../api';

const { Text } = Typography;

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
    /** 变化时重新拉取计划（Schema 保存 / 智能规划应用后由父组件递增） */
    refreshSignal?: number;
}

type LaneKey = 'inductive' | 'surface' | 'merge';

const LANE_META: Record<LaneKey, { title: string; className: string }> = {
    inductive: { title: '归纳分道 · INDUCTIVE', className: 'plan-lane-inductive' },
    surface: { title: '表面分道 · SURFACE / NORMALIZED', className: 'plan-lane-surface' },
    merge: { title: '汇合 · RELATIONS / GLOBAL', className: 'plan-lane-merge' },
};

/** 按步骤作用目标的抽象度分道：全归纳→归纳道；全表面/标准化→表面道；混合或无目标→汇合道 */
function classifyStep(step: ExtractionStep, plan: ExtractionPlan): LaneKey {
    const abs = step.targets
        .map((t) => plan.knowledge_types[t]?.abstractness)
        .filter(Boolean);
    if (abs.length === 0) return 'merge';
    if (abs.every((a) => a === 'inductive')) return 'inductive';
    if (abs.every((a) => a === 'surface' || a === 'normalized')) return 'surface';
    return 'merge';
}

export default function PlanLanes({ projectId, refreshSignal }: Props) {
    const [plan, setPlan] = useState<ExtractionPlan | null>(null);
    const [valid, setValid] = useState(true);
    const [errors, setErrors] = useState<string[]>([]);
    const [loading, setLoading] = useState(false);

    const load = async () => {
        if (!projectId) return;
        setLoading(true);
        try {
            const res = await getExtractionPlan(projectId);
            setPlan(res.data.plan);
            setValid(res.data.valid);
            setErrors(res.data.errors || []);
        } catch {
            setPlan(null);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, [projectId, refreshSignal]);

    const lanes: Record<LaneKey, ExtractionStep[]> = { inductive: [], surface: [], merge: [] };
    if (plan) {
        plan.steps.forEach((s) => { lanes[classifyStep(s, plan)].push(s); });
    }

    return (
        <div className="plan-lanes card" style={{ marginTop: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                <BranchesOutlined style={{ color: 'var(--primary-500)' }} />
                <span style={{ fontWeight: 650 }}>编译出的抽取计划</span>
                {plan && (
                    <Text type="secondary" style={{ fontSize: 12, fontFamily: 'var(--mono)' }}>
                        {plan.source === 'planner-default' ? '默认反编译' : plan.source === 'planner-llm' ? '智能规划' : plan.source}
                        {' · '}绑定 Schema v{plan.schema_version}
                    </Text>
                )}
                {plan && (valid
                    ? <Tag color="green" style={{ fontSize: 11 }}>DAG 合法</Tag>
                    : <Tooltip title={errors.join('；')}><Tag color="red" style={{ fontSize: 11 }}>校验未通过</Tag></Tooltip>
                )}
                <span style={{ flex: 1 }} />
                <Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
            </div>

            <Spin spinning={loading}>
                {!plan ? (
                    <Empty
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                        description="暂无抽取计划——定义实体类型并保存后，计划将由 Schema 自动编译生成"
                    />
                ) : (
                    <Space direction="vertical" size={10} style={{ width: '100%' }}>
                        {(Object.keys(LANE_META) as LaneKey[]).map((key) => {
                            const steps = lanes[key];
                            if (steps.length === 0) return null;
                            return (
                                <div key={key} className={`plan-lane ${LANE_META[key].className}`}>
                                    <div className="plan-lane-title">{LANE_META[key].title}</div>
                                    <div className="plan-lane-steps">
                                        {steps.map((s, i) => (
                                            <span key={s.step_id} style={{ display: 'inline-flex', alignItems: 'center' }}>
                                                {i > 0 && <span className="plan-arrow">→</span>}
                                                <Tooltip
                                                    title={
                                                        <div style={{ fontSize: 12 }}>
                                                            <div><b>{PRIMITIVE_LABEL[s.primitive] || s.primitive}</b>{s.targets.length > 0 && <> · 作用于 {s.targets.join('、')}</>}</div>
                                                            {s.reason && <div style={{ marginTop: 4, opacity: .85 }}>理由：{s.reason}</div>}
                                                            {Object.keys(s.params || {}).length > 0 && (
                                                                <div style={{ marginTop: 4, fontFamily: 'var(--mono)', fontSize: 11, opacity: .7 }}>
                                                                    {Object.entries(s.params).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(' ')}
                                                                </div>
                                                            )}
                                                        </div>
                                                    }
                                                >
                                                    <span className="plan-node">{s.primitive}</span>
                                                </Tooltip>
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            );
                        })}
                        <Text type="secondary" style={{ fontSize: 12 }}>
                            每步悬停可查看编排理由与参数。归纳类型（紫道）走归纳抽取 + 忠实度校验，表面类型（蓝道）走逐字抽取 + 证据校验；
                            如需调整各类型的抽取语义，使用上方「② 智能规划」。
                        </Text>
                    </Space>
                )}
            </Spin>
        </div>
    );
}
