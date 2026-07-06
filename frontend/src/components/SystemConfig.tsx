/**
 * 系统配置 - LLM API Key / Base URL 配置 + 抽取性能选项
 */
import { useState } from 'react';
import { Button, Modal, Form, Input, InputNumber, Switch, Divider, message, Tooltip, Select, Alert } from 'antd';
import { SettingOutlined, QuestionCircleOutlined } from '@ant-design/icons';
import { getSystemConfig, updateSystemConfig } from '../api';
import type { SystemConfig as SystemConfigType } from '../api';

export default function SystemConfig() {
    const [open, setOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [form] = Form.useForm();
    const similarityBackend = Form.useWatch('similarity_backend', form) || 'keyword';
    const disambiguationEnabled = Form.useWatch('enable_disambiguation', form) ?? true;

    const withTip = (label: string, tip: string) => (
        <span>
            {label}&nbsp;
            <Tooltip title={tip}>
                <QuestionCircleOutlined style={{ color: 'rgba(0,0,0,0.45)' }} />
            </Tooltip>
        </span>
    );

    const handleOpen = async () => {
        setOpen(true);
        setLoading(true);
        try {
            const res = await getSystemConfig();
            form.setFieldsValue(res.data);
        } catch {
            message.error('加载配置失败');
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        try {
            const values = await form.validateFields();
            setLoading(true);
            await updateSystemConfig(values as SystemConfigType);
            message.success('配置已保存');
            setOpen(false);
        } catch {
            // validation error
        } finally {
            setLoading(false);
        }
    };

    return (
        <>
            <Tooltip title="系统配置">
                <Button
                    icon={<SettingOutlined />}
                    onClick={handleOpen}
                    shape="circle"
                    size="large"
                />
            </Tooltip>

            <Modal
                title="⚙️ 系统配置"
                open={open}
                onCancel={() => setOpen(false)}
                onOk={handleSave}
                confirmLoading={loading}
                okText="保存"
                cancelText="取消"
                width={600}
            >
                <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
                    <Form.Item
                        name="api_key"
                        label={withTip('API Key', '大模型服务密钥。保存时会自动脱敏显示。')}
                        rules={[{ required: true, message: '请输入 API Key' }]}
                    >
                        <Input.Password placeholder="sk-xxxxxxxx" />
                    </Form.Item>

                    <Form.Item
                        name="base_url"
                        label={withTip('Base URL', 'OpenAI 兼容接口地址；更换服务商时通常需要调整。')}
                        rules={[{ required: true, message: '请输入 Base URL' }]}
                    >
                        <Input placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
                    </Form.Item>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                        <Form.Item name="model_simple" label={withTip('轻量模型', '用于意图识别、实体提取等轻量任务，优先速度。')}>
                            <Input placeholder="qwen-turbo" />
                        </Form.Item>
                        <Form.Item name="model_normal" label={withTip('均衡模型', '用于常规问答与抽取主流程，平衡质量与成本。')}>
                            <Input placeholder="qwen-plus" />
                        </Form.Item>
                        <Form.Item name="model_complex" label={withTip('强力模型', '用于复杂推理或困难场景，质量高但耗时和成本更高。')}>
                            <Input placeholder="qwen-max" />
                        </Form.Item>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                        <Form.Item name="chunk_size" label={withTip('分片大小(字符)', '文档切分长度。越大上下文更完整，但抽取和检索成本更高。')}>
                            <InputNumber min={100} max={2000} style={{ width: '100%' }} />
                        </Form.Item>
                        <Form.Item name="chunk_overlap" label={withTip('分片重叠(字符)', '相邻分片重叠长度。适当重叠可降低信息断裂。')}>
                            <InputNumber min={0} max={500} style={{ width: '100%' }} />
                        </Form.Item>
                        <Form.Item name="parallel_processes" label={withTip('并发数 (1-20)', '抽取任务并行度。并发越高越快，但更吃 CPU/内存与接口额度。')}>
                            <InputNumber min={1} max={20} style={{ width: '100%' }} />
                        </Form.Item>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                        <Form.Item
                            name="extraction_mode"
                            label={withTip('抽取模式', 'one-pass: 单次抽取实体+关系（更快）；multi-pass: 分步抽取（更稳）。')}
                        >
                            <Select
                                options={[
                                    { value: 'one-pass', label: 'one-pass（速度优先）' },
                                    { value: 'multi-pass', label: 'multi-pass（质量优先）' },
                                ]}
                            />
                        </Form.Item>
                        <Form.Item
                            name="database_batch_size"
                            label={withTip('数据库批量写入大小', '抽取结果累计到该条数后批量落库。增大可减少 IO 次数。')}
                        >
                            <InputNumber min={1} max={200} style={{ width: '100%' }} />
                        </Form.Item>
                    </div>

                    <Divider style={{ margin: '8px 0 16px' }}>检索与相似度</Divider>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                        <Form.Item
                            name="similarity_backend"
                            label={withTip('相似度引擎', 'keyword: 全链路禁用向量，使用关键词/n-gram/编辑距离；vector: 使用 Embedding + FAISS。')}
                        >
                            <Select
                                options={[
                                    { value: 'keyword', label: '快速模式（keyword，默认）' },
                                    { value: 'vector', label: '向量模式（embedding + FAISS）' },
                                ]}
                            />
                        </Form.Item>
                        <Form.Item name="vector_top_k" label={withTip('向量召回 Top-K', '仅向量模式生效。每次向量检索返回的候选上限。')}>
                            <InputNumber min={5} max={50} style={{ width: '100%' }} disabled={similarityBackend !== 'vector'} />
                        </Form.Item>
                    </div>
                    {similarityBackend === 'keyword' && (
                        <Alert
                            type="info"
                            showIcon
                            style={{ marginBottom: 12 }}
                            message="当前为快速模式：所有向量索引/Embedding 相关链路都会被跳过，向量参数不会生效。"
                        />
                    )}
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                        <Form.Item name="score_threshold" label={withTip('向量阈值', '仅向量模式生效。用于控制向量候选最低相似度。')}>
                            <InputNumber min={0} max={1} step={0.1} style={{ width: '100%' }} disabled={similarityBackend !== 'vector'} />
                        </Form.Item>
                        <Form.Item name="fast_score_threshold" label={withTip('快速模式阈值', '仅快速模式生效。越高越严格，召回更少更准；越低召回更多。')}>
                            <InputNumber min={0} max={1} step={0.05} style={{ width: '100%' }} />
                        </Form.Item>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                        <Form.Item
                            name="model_embedding"
                            label={withTip('向量模型（兜底）', '仅向量模式生效。用于本地 embedding 模型不可用时的 API 兜底。')}
                        >
                            <Input placeholder="text-embedding-v1" disabled={similarityBackend !== 'vector'} />
                        </Form.Item>
                    </div>

                    <Divider style={{ margin: '8px 0 16px' }}>实体消歧</Divider>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                        <Form.Item
                            name="disambiguation_fast_path_score"
                            label={withTip('消歧快速直通阈值', '候选分数高于该阈值时直接判为同一实体，减少 LLM 消歧调用。')}
                        >
                            <InputNumber min={0} max={1} step={0.01} style={{ width: '100%' }} disabled={!disambiguationEnabled} />
                        </Form.Item>
                        <Form.Item
                            name="disambiguation_candidate_limit_per_entity"
                            label={withTip('每实体候选上限', '每个新实体参与消歧时最多保留的候选数量，用于控制延迟与提示词长度。')}
                        >
                            <InputNumber min={1} max={20} step={1} style={{ width: '100%' }} disabled={!disambiguationEnabled} />
                        </Form.Item>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                        <Form.Item
                            name="disambiguation_low_confidence_only"
                            label={withTip('仅低置信实体消歧', '开启后仅对低置信实体触发 LLM 消歧，可显著降低调用量。')}
                            valuePropName="checked"
                        >
                            <Switch checkedChildren="开启" unCheckedChildren="关闭" disabled={!disambiguationEnabled} />
                        </Form.Item>
                        <Form.Item name="disambiguation_entity_confidence_threshold" label={withTip('低置信阈值', '实体置信度低于该值才进入消歧流程（与“仅低置信实体消歧”配合）。')}>
                            <InputNumber min={0} max={1} step={0.01} style={{ width: '100%' }} disabled={!disambiguationEnabled} />
                        </Form.Item>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                        <Form.Item
                            name="llm_stream_log"
                            label={withTip('流式抽取日志', '开启后逐字记录模型输出，便于排障，但会增加日志和性能开销。')}
                            valuePropName="checked"
                        >
                            <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                        </Form.Item>
                    </div>

                    <Divider style={{ margin: '8px 0 16px' }}>抽取性能优化</Divider>

                    <Form.Item
                        name="enable_cross_chunk_inference"
                        label={withTip('跨段落关系推理', '每个片段会额外触发一次 LLM 进行跨段关联，召回更全但耗时明显增加。')}
                        valuePropName="checked"
                    >
                        <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                    </Form.Item>

                    <Form.Item
                        name="enable_self_correction"
                        label={withTip('自我修正', '每片段额外调用一次 LLM 对抽取结果纠错，质量更高但处理更慢。')}
                        valuePropName="checked"
                    >
                        <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                    </Form.Item>

                    <Form.Item
                        name="enable_disambiguation"
                        label={withTip('LLM 实体消歧', '候选相似实体会调用 LLM 判断是否同一实体。关闭后仅使用规则与快速匹配。')}
                        valuePropName="checked"
                    >
                        <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                    </Form.Item>
                </Form>
            </Modal>
        </>
    );
}
