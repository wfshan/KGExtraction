/**
 * 图谱可视化页面 - Cytoscape.js 力导向图 + 侧边栏
 */
import { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
    Select, Tag, Descriptions, Empty, Space, Button, Checkbox, message, Badge,
    Collapse, Spin, Typography, Drawer, Input, List, Avatar, Popconfirm, Tooltip, InputNumber,
    AutoComplete, Menu
} from 'antd';
import {
    FullscreenOutlined, FullscreenExitOutlined, ExportOutlined,
    ReloadOutlined, ZoomInOutlined, ZoomOutOutlined, AimOutlined,
    FileTextOutlined, MessageOutlined, UserOutlined, RobotOutlined, SendOutlined,
    DeleteOutlined, FieldTimeOutlined,
    ArrowUpOutlined, ArrowDownOutlined, SwapOutlined
} from '@ant-design/icons';
import cytoscape from 'cytoscape';
import type { Core, EventObject } from 'cytoscape';
import {
    listProjects, getGraph, getSchema, exportGraph, getChunksByIds, chatWithGraphStream,
    getChatHistory, clearChatHistory, searchEntities, getProjectSubgraph
} from '../api';
import type {
    Project, GraphData, SchemaConfig, ChunkContent,
} from '../api';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface RecallInfo {
    mode?: string;
    summary?: {
        total_nodes: number;
        total_edges: number;
        total_chunks: number;
    };
    nodes: any[];
    edges: string[];
    chunks: any[];
}

interface ChatMessage {
    role: 'user' | 'assistant' | 'system';
    content: string;
    recallInfo?: RecallInfo;
}

const { Text } = Typography;
const GRAPH_RAG_MODE_LABEL: Record<string, string> = {
    graph_flow: '独立流式',
    graph_full: '全向扩散',
    graph_path: '路径查找',
    text_only: '仅文本块',
    direct: '模型直答',
};

export default function GraphPage() {
    const { projectId: routeProjectId } = useParams<{ projectId: string }>();
    const navigate = useNavigate();
    const containerRef = useRef<HTMLDivElement>(null);
    const cyRef = useRef<Core | null>(null);

    const [projects, setProjects] = useState<Project[]>([]);
    const [selectedProject, setSelectedProject] = useState<string>(routeProjectId || '');
    const [graph, setGraph] = useState<GraphData | null>(null);
    const [schema, setSchema] = useState<SchemaConfig | null>(null);
    const [selectedElement, setSelectedElement] = useState<any>(null);
    const [entityTypeFilter, setEntityTypeFilter] = useState<string[]>([]);
    const [fullscreen, setFullscreen] = useState(false);
    const [chunks, setChunks] = useState<ChunkContent[]>([]);
    const [chunksLoading, setChunksLoading] = useState(false);
    const [hasColdStart, setHasColdStart] = useState(false);
    const [useSubgraphContext, setUseSubgraphContext] = useState(false);

    // Chat Drawer State
    const [chatVisible, setChatVisible] = useState(false);
    const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
    const [chatInput, setChatInput] = useState('');
    const [chatLoading, setChatLoading] = useState(false);
    const [graphRagMaxDegree, setGraphRagMaxDegree] = useState<number>(2);
    const [graphRagMaxStartEntities, setGraphRagMaxStartEntities] = useState<number>(5);
    const [graphRagMode, setGraphRagMode] = useState<string>('graph_flow');
    const messagesEndRef = useRef<HTMLDivElement>(null);

    // Exploration State
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<{ id: string; name: string; type: string }[]>([]);
    const [contextMenu, setContextMenu] = useState<{ x: number, y: number, nodeId: string } | null>(null);
    const [explorationMode, setExplorationMode] = useState(true); // 默认开启按需探索模式
    const [exploring, setExploring] = useState(false);

    // Load projects
    useEffect(() => {
        listProjects().then((res) => {
            setProjects(res.data);
            if (!selectedProject && res.data.length > 0) {
                setSelectedProject(res.data[0].id);
            }
        });
    }, []);

    // Load Chat History
    const fetchChatHistory = async () => {
        if (!selectedProject) return;
        try {
            const res = await getChatHistory(selectedProject);
            setChatMessages(res.data.history as ChatMessage[]);
        } catch (error) {
            console.error("Failed to load chat history", error);
        }
    };

    const handleClearChat = async () => {
        if (!selectedProject) return;
        try {
            await clearChatHistory(selectedProject);
            setChatMessages([]);
            message.success('对话记录已清空');
        } catch (error) {
            message.error('清空失败');
        }
    };

    useEffect(() => {
        if (chatVisible && selectedProject) {
            fetchChatHistory();
        }
    }, [chatVisible, selectedProject]);

    // Handle Search
    const handleSearch = async (val: string) => {
        setSearchQuery(val);
        if (val.length < 1) {
            setSearchResults([]);
            return;
        }
        try {
            const res = await searchEntities(selectedProject, val);
            setSearchResults(res.data);
        } catch (e) {
            console.error(e);
        }
    };

    const onSearchSelect = async (nodeId: string) => {
        setExploring(true);
        try {
            const res = await getProjectSubgraph(selectedProject, nodeId, 1, 'published', 'both');
            mergeGraphData(res.data);
            setSearchQuery('');
            message.success('已添加节点到图谱');
        } catch (e) {
            message.error('加载节点失败');
        } finally {
            setExploring(false);
        }
    };

    const mergeGraphData = (newData: GraphData) => {
        setGraph(prev => {
            if (!prev) return newData;
            const existingNodeIds = new Set(prev.nodes.map(n => n.id));
            const existingEdgeIds = new Set(prev.edges.map(e => e.id));
            
            const newNodes = newData.nodes.filter(n => !existingNodeIds.has(n.id));
            const newEdges = newData.edges.filter(e => !existingEdgeIds.has(e.id));
            
            return {
                ...prev,
                nodes: [...prev.nodes, ...newNodes],
                edges: [...prev.edges, ...newEdges]
            };
        });
    };

    const handleExpand = async (nodeId: string, direction: 'up' | 'down' | 'both') => {
        setExploring(true);
        setContextMenu(null);
        try {
            const res = await getProjectSubgraph(selectedProject, nodeId, 1, 'published', direction);
            mergeGraphData(res.data);
            message.success('扩展成功');
        } catch (e) {
            message.error('扩展失败');
        } finally {
            setExploring(false);
        }
    };

    // Load graph data
    useEffect(() => {
        if (!selectedProject) return;
        setGraph(null); // 重置图谱
        if (cyRef.current) {
            cyRef.current.destroy();
            cyRef.current = null;
        }

        // 如果不是探索模式，则加载全量
        if (!explorationMode) {
            getGraph(selectedProject, 'published').then((res) => {
                const data = res.data.nodes?.length > 0 ? res.data : null;
                if (data) {
                    setGraph(data);
                } else {
                    getGraph(selectedProject, 'draft').then((r) => setGraph(r.data));
                }
            });
        }
        getSchema(selectedProject).then((res) => setSchema(res.data)).catch(() => null);
    }, [selectedProject, explorationMode]);

    // Build Cytoscape graph
    useEffect(() => {
        if (!graph || !containerRef.current) return;

        // Entity type → color mapping
        const colorMap: Record<string, string> = {};
        if (schema) {
            schema.entity_types.forEach((et) => {
                colorMap[et.name] = et.color;
            });
        }
        const defaultColors = ['#4A90D9', '#50C878', '#FF6B6B', '#FFD93D', '#9B59B6', '#1ABC9C', '#E67E22'];
        let colorIdx = 0;

        // Filter nodes
        const visibleNodes = graph.nodes.filter(
            (n) => entityTypeFilter.length === 0 || entityTypeFilter.includes(n.entity_type)
        );
        const visibleNodeIds = new Set(visibleNodes.map((n) => n.id));
        const visibleEdges = graph.edges.filter(
            (e) => visibleNodeIds.has(e.source_id) && visibleNodeIds.has(e.target_id)
        );

        const elements: cytoscape.ElementDefinition[] = [
            ...visibleNodes.map((node) => {
                if (!colorMap[node.entity_type]) {
                    colorMap[node.entity_type] = defaultColors[colorIdx % defaultColors.length];
                    colorIdx++;
                }
                return {
                    data: {
                        ...node,
                        id: node.id,
                        label: node.name,
                        entityType: node.entity_type,
                        color: colorMap[node.entity_type],
                    },
                };
            }),
            ...visibleEdges.map((edge) => ({
                data: {
                    ...edge,
                    id: edge.id,
                    source: edge.source_id,
                    target: edge.target_id,
                    label: edge.relation_type,
                },
            })),
        ];

        let cy = cyRef.current;

        // 如果 cy 尚未初始化，则进行全量初始化
        if (!cy) {
            cy = cytoscape({
                container: containerRef.current,
                elements,
                style: [
                    {
                        selector: 'node',
                        style: {
                            'background-color': 'data(color)',
                            label: 'data(label)',
                            color: '#fff',
                            'text-outline-color': 'data(color)',
                            'text-outline-width': 2,
                            'font-size': 12,
                            'text-valign': 'center',
                            'text-halign': 'center',
                            width: 40,
                            height: 40,
                            'border-width': 2,
                            'border-color': '#fff',
                            'overlay-opacity': 0,
                        },
                    },
                    {
                        selector: 'node:selected',
                        style: {
                            'border-width': 4,
                            'border-color': '#FFD700',
                            width: 50,
                            height: 50,
                        },
                    },
                    {
                        selector: 'edge',
                        style: {
                            width: 2,
                            'line-color': '#94A3B8',
                            'target-arrow-color': '#94A3B8',
                            'target-arrow-shape': 'triangle',
                            'curve-style': 'bezier',
                            label: 'data(label)',
                            'font-size': 10,
                            color: '#64748B',
                            'text-background-color': '#fff',
                            'text-background-opacity': 0.8,
                            'text-background-padding': '2px',
                        },
                    },
                    {
                        selector: 'edge:selected',
                        style: {
                            'line-color': '#FFD700',
                            'target-arrow-color': '#FFD700',
                            width: 3,
                        },
                    },
                ],
                layout: {
                    name: 'cose',
                    animate: true,
                    animationDuration: 800,
                    nodeOverlap: 20,
                    idealEdgeLength: () => 120,
                    nodeRepulsion: () => 8000,
                    gravity: 0.25,
                } as any,
                minZoom: 0.1,
                maxZoom: 5,
                wheelSensitivity: 0.2, // 降低鼠标滚轮缩放灵敏度
            });

            // 右键/长按显示扩展菜单
            cy.on('cxttap', 'node', (e: EventObject) => {
                const node = e.target;
                const pos = e.renderedPosition;
                setContextMenu({
                    x: pos.x,
                    y: pos.y,
                    nodeId: node.id()
                });
            });

            cy.on('tap', () => {
                setContextMenu(null);
            });

            // LOD (Level of Detail) 优化：根据缩放级别调整样式
            cy.on('zoom', () => {
                const zoom = cy!.zoom();
                if (zoom < 0.5) {
                    cy!.batch(() => {
                        cy!.$('node').style({ 'text-opacity': 0, 'width': 30, 'height': 30 });
                        cy!.$('edge').style({ 'text-opacity': 0, 'width': 1 });
                    });
                } else if (zoom < 0.8) {
                    cy!.batch(() => {
                        cy!.$('node').style({ 'text-opacity': 0.5, 'width': 40, 'height': 40 });
                        cy!.$('edge').style({ 'text-opacity': 0, 'width': 2 });
                    });
                } else {
                    cy!.batch(() => {
                        cy!.$('node').style({ 'text-opacity': 1, 'width': 40, 'height': 40 });
                        cy!.$('edge').style({ 'text-opacity': 1, 'width': 2 });
                    });
                }
            });

            cy.on('tap', 'node', (e: EventObject) => {
                setSelectedElement({ type: 'node', data: e.target.data() });
            });
            cy.on('tap', 'edge', (e: EventObject) => {
                const d = e.target.data();
                // We don't have direct access to react state here reliably over time, 
                // so we fetch names from the clicked element or graph fallback
                setSelectedElement({
                    type: 'edge',
                    data: { ...d },
                });
            });
            cy.on('tap', (e: EventObject) => {
                if (e.target === cy) setSelectedElement(null);
            });

            cyRef.current = cy;
        } else {
            // cy 已经存在，使用增量更新机制 (Incremental update)
            cy.batch(() => {
                // Remove elements that are no longer in the new set
                const newIds = new Set(elements.map(el => el.data.id));
                const toRemove = cy!.elements().filter(ele => !newIds.has(ele.id()));
                cy!.remove(toRemove);

                // Add or update elements
                elements.forEach(ele => {
                    const existingEle = cy!.$id(ele.data.id as string);
                    if (existingEle.length > 0) {
                        // Update existing data
                        existingEle.data(ele.data);
                    } else {
                        // Add new element
                        cy!.add(ele);
                    }
                });

                // 如果节点数量增加，重新运行布局
                if (cy!.nodes().length !== visibleNodes.length || elements.length > cy!.elements().length) {
                    cy!.layout({
                        name: 'cose',
                        animate: true,
                        animationDuration: 500,
                        nodeOverlap: 20,
                        idealEdgeLength: () => 100,
                    } as any).run();
                }
            });
        }

        return () => {
            // 不要在这里销毁实例，让组件卸载时再去销毁（需要一个全局 useEffect）
            // 如果这里销毁了，下次 useEffect 就会触发全量重绘
        };
    }, [graph, entityTypeFilter, schema]);

    // Cleanup cytoscape on full unmount
    useEffect(() => {
        return () => {
            if (cyRef.current) {
                cyRef.current.destroy();
                cyRef.current = null;
            }
        };
    }, []);

    // Toolbar actions
    const zoomIn = () => cyRef.current?.zoom(cyRef.current.zoom() * 1.3);
    const zoomOut = () => cyRef.current?.zoom(cyRef.current.zoom() / 1.3);
    const fitAll = () => cyRef.current?.fit(undefined, 50);
    const resetLayout = () => {
        cyRef.current?.layout({
            name: 'cose',
            animate: true,
            animationDuration: 800,
            nodeOverlap: 20,
            idealEdgeLength: () => 120,
            nodeRepulsion: () => 8000,
        } as any).run();
    };

    const handleExport = async () => {
        if (!selectedProject) return;
        try {
            const res = await exportGraph(selectedProject);
            const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `graph_${selectedProject}.json`;
            a.click();
            URL.revokeObjectURL(url);
            message.success('导出成功');
        } catch {
            message.error('导出失败');
        }
    };

    // Fetch chunks when element is selected
    useEffect(() => {
        if (!selectedElement || !selectedProject) {
            setChunks([]);
            setHasColdStart(false);
            return;
        }
        
        // 如果开启了“仅在当前选中实体上下文中提问”，则自动尝试获取更大范围的子图以丰富内容
        if (useSubgraphContext && selectedElement.type === 'node') {
            // 这里可以按需调用新的 subgraph API 来局部更新图谱结构
            // 但目前的逻辑主要是在 Chat 时处理上下文，这里保持显示片段详情
        }

        const chunkIds: string[] = selectedElement.data?.source_chunk_ids || [];
        setHasColdStart(chunkIds.includes('cold_start'));
        const realIds = chunkIds.filter((id: string) => id !== 'cold_start');
        if (realIds.length === 0) {
            setChunks([]);
            return;
        }
        setChunksLoading(true);
        getChunksByIds(selectedProject, realIds)
            .then((res) => setChunks(res.data))
            .catch(() => setChunks([]))
            .finally(() => setChunksLoading(false));
    }, [selectedElement, selectedProject, useSubgraphContext]);

    // Get unique entity types
    const entityTypes = graph ? [...new Set(graph.nodes.map((n) => n.entity_type))] : [];

    // Auto-scroll chat to bottom
    useEffect(() => {
        if (chatVisible && messagesEndRef.current) {
            messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
        }
    }, [chatMessages, chatVisible]);

    const handleSendMessage = () => {
        if (!chatInput.trim() || !selectedProject || chatLoading) return;

        const newMessage: ChatMessage = { role: 'user', content: chatInput };
        setChatMessages((prev) => [...prev, newMessage, { role: 'assistant', content: '' }]);
        setChatInput('');
        setChatLoading(true);

        let currentAssistantMessage = '';
        let recallBuffer = '';
        let isParsingRecall = false;

        // Handle Scope
        let contextPrefix = '';
        if (useSubgraphContext && cyRef.current) {
            // 获取当前选中的节点及其一度相连的邻居节点构成上下文
            const selected = cyRef.current.$(':selected');
            if (selected.length > 0) {
                const subGraph = selected.union(selected.neighborhood());
                const subGraphNames = subGraph.nodes().map(n => n.data('label')).filter(Boolean);
                if (subGraphNames.length > 0) {
                    contextPrefix = `【用户框选关注的实体上下文：${subGraphNames.join('、')}】\n`;
                }
            }
        }

        chatWithGraphStream(
            selectedProject,
            contextPrefix + newMessage.content,
            (chunk: string) => {
                // Metadata parsing
                if (chunk.includes('__RECALL_START__')) {
                    isParsingRecall = true;
                    const startIdx = chunk.indexOf('__RECALL_START__');
                    const leadingText = chunk.substring(0, startIdx);
                    currentAssistantMessage += leadingText;
                    
                    const remaining = chunk.substring(startIdx + '__RECALL_START__'.length);
                    if (remaining.includes('__RECALL_END__')) {
                        const endIdx = remaining.indexOf('__RECALL_END__');
                        const jsonStr = remaining.substring(0, endIdx);
                        try {
                            const info = JSON.parse(jsonStr);
                            setChatMessages(prev => {
                                const newMsgs = [...prev];
                                const last = newMsgs[newMsgs.length - 1];
                                if (last && last.role === 'assistant') last.recallInfo = info;
                                return newMsgs;
                            });
                        } catch (e) {}
                        isParsingRecall = false;
                        currentAssistantMessage += remaining.substring(endIdx + '__RECALL_END__'.length);
                    } else {
                        recallBuffer = remaining;
                    }
                } else if (isParsingRecall) {
                    if (chunk.includes('__RECALL_END__')) {
                        const endIdx = chunk.indexOf('__RECALL_END__');
                        recallBuffer += chunk.substring(0, endIdx);
                        try {
                            const info = JSON.parse(recallBuffer);
                            setChatMessages(prev => {
                                const newMsgs = [...prev];
                                const last = newMsgs[newMsgs.length - 1];
                                if (last && last.role === 'assistant') last.recallInfo = info;
                                return newMsgs;
                            });
                        } catch (e) {}
                        isParsingRecall = false;
                        currentAssistantMessage += chunk.substring(endIdx + '__RECALL_END__'.length);
                    } else {
                        recallBuffer += chunk;
                    }
                } else {
                    currentAssistantMessage += chunk;
                }

                if (!isParsingRecall) {
                    setChatMessages((prev) => {
                        const newMsgs = [...prev];
                        const lastMsg = newMsgs[newMsgs.length - 1];
                        if (lastMsg && lastMsg.role === 'assistant') {
                            lastMsg.content = currentAssistantMessage;
                        }
                        return newMsgs;
                    });
                }
            },
            (error: any) => {
                message.error(`问图失败: ${error.message}`);
                setChatLoading(false);
                // 移除空的助手消息占位
                setChatMessages(prev => prev.filter(m => m.content !== '' || m.role !== 'assistant'));
            },
            () => {
                setChatLoading(false);
            },
            { 
                max_degree: graphRagMaxDegree, 
                max_start_entities: graphRagMaxStartEntities,
                retrieval_mode: graphRagMode
            }
        );
    };

    const renderMarkdownContent = (content: string, isAssistant: boolean) => (
        <div className={`chat-markdown ${isAssistant ? 'assistant' : 'user'}`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {content}
            </ReactMarkdown>
        </div>
    );

    return (
        <div className="graph-page" style={fullscreen ? { position: 'fixed', inset: 0, zIndex: 1000, height: '100vh' } : {}}>
            {/* Canvas */}
            <div className="graph-canvas-container">
                {/* Toolbar */}
                <div style={{
                    position: 'absolute', top: 12, left: 12, zIndex: 10,
                    display: 'flex', gap: 8, alignItems: 'center',
                }}>
                    <Select
                        value={selectedProject || undefined}
                        onChange={(v) => { setSelectedProject(v); navigate(`/graph/${v}`); }}
                        placeholder="选择项目"
                        style={{ width: 200 }}
                        options={projects.map((p) => ({ label: p.name, value: p.id }))}
                    />
                </div>

                <div style={{
                    position: 'absolute', top: 12, right: 12, zIndex: 10,
                    display: 'flex', gap: 4,
                }}>
                    <Button
                        type="primary"
                        icon={<MessageOutlined />}
                        onClick={() => setChatVisible(true)}
                        style={{ marginRight: 8, boxShadow: '0 2px 8px rgba(0,0,0,0.15)' }}
                    >
                        问图
                    </Button>
                    <Button size="small" icon={<ZoomInOutlined />} onClick={zoomIn} />
                    <Button size="small" icon={<ZoomOutOutlined />} onClick={zoomOut} />
                    <Button size="small" icon={<AimOutlined />} onClick={fitAll} />
                    <Button size="small" icon={<ReloadOutlined />} onClick={resetLayout} />
                    {explorationMode && (
                        <AutoComplete
                            style={{ width: 250 }}
                            onSearch={handleSearch}
                            onSelect={onSearchSelect}
                            value={searchQuery}
                            placeholder="搜索并探索实体..."
                        >
                            {searchResults.map((item) => (
                                <AutoComplete.Option key={item.id} value={item.id}>
                                    <Space>
                                        <Tag color="blue">{item.type}</Tag>
                                        {item.name}
                                    </Space>
                                </AutoComplete.Option>
                            ))}
                        </AutoComplete>
                    )}
                    <Checkbox 
                        checked={explorationMode} 
                        onChange={e => setExplorationMode(e.target.checked)}
                    >
                        探索模式
                    </Checkbox>

                    <Button size="small" icon={<ExportOutlined />} onClick={handleExport} />
                    <Button
                        size="small"
                        icon={fullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
                        onClick={() => setFullscreen(!fullscreen)}
                    />
                </div>

                {/* Stats badge */}
                <div style={{
                    position: 'absolute', bottom: 12, left: 12, zIndex: 10,
                    display: 'flex', gap: 8,
                }}>
                    <Badge count={graph?.nodes.length || 0} showZero overflowCount={9999}>
                        <Tag>节点</Tag>
                    </Badge>
                    <Badge count={graph?.edges.length || 0} showZero overflowCount={9999}>
                        <Tag>关系</Tag>
                    </Badge>
                </div>

                <div style={{ width: '100%', height: '100%', position: 'relative', overflow: 'hidden', border: '1px solid #d9d9d9', borderRadius: 8, background: '#fcfcfc' }}>
                    <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
                    
                    {contextMenu && (
                        <div style={{ 
                            position: 'absolute', 
                            left: contextMenu.x, 
                            top: contextMenu.y, 
                            zIndex: 1000,
                            background: '#fff',
                            boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
                            borderRadius: 4,
                            minWidth: 120
                        }}>
                            <Menu onClick={({ key }) => {
                                if (key === 'close') {
                                    setContextMenu(null);
                                } else {
                                    handleExpand(contextMenu.nodeId, key as any);
                                }
                            }}>
                                <Menu.Item key="up" icon={<ArrowUpOutlined />}>向上展开</Menu.Item>
                                <Menu.Item key="down" icon={<ArrowDownOutlined />}>向下展开</Menu.Item>
                                <Menu.Item key="both" icon={<SwapOutlined />}>双向展开</Menu.Item>
                                <Menu.Divider />
                                <Menu.Item key="close" danger>关闭菜单</Menu.Item>
                            </Menu>
                        </div>
                    )}

                    {(!graph || graph.nodes.length === 0) && (
                        <div style={{
                            position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
                            pointerEvents: 'none', background: 'rgba(255,255,255,0.6)'
                        }}>
                            <Empty description={explorationMode ? "探索模式：请在左上角搜索并添加实体" : "暂无图谱数据，请先完成抽取并发布"} />
                        </div>
                    )}

                    {exploring && (
                        <div style={{
                            position: 'absolute',
                            bottom: 20,
                            right: 20,
                            zIndex: 10
                        }}>
                            <Spin tip="正在扩展预览..." />
                        </div>
                    )}
                </div>
            </div>

            {/* Sidebar */}
            <div className="graph-sidebar">
                {/* Filter */}
                <div className="sidebar-section">
                    <div className="sidebar-title">过滤器</div>
                    <Checkbox.Group
                        value={entityTypeFilter}
                        onChange={(vals) => setEntityTypeFilter(vals as string[])}
                    >
                        <Space direction="vertical" size="small">
                            {entityTypes.map((type) => (
                                <Checkbox key={type} value={type}>
                                    {type}
                                    <Tag style={{ marginLeft: 8, fontSize: 11 }}>
                                        {graph?.nodes.filter((n) => n.entity_type === type).length}
                                    </Tag>
                                </Checkbox>
                            ))}
                        </Space>
                    </Checkbox.Group>
                    {entityTypeFilter.length > 0 && (
                        <Button
                            size="small"
                            type="link"
                            onClick={() => setEntityTypeFilter([])}
                            style={{ marginTop: 8 }}
                        >
                            清除过滤
                        </Button>
                    )}
                </div>

                {/* Inspector */}
                <div className="sidebar-section" style={{ flex: 1, overflowY: 'auto' }}>
                    <div className="sidebar-title">详情面板</div>
                    {selectedElement ? (
                        <>
                            {selectedElement.type === 'node' ? (
                                <Descriptions column={1} size="small" bordered>
                                    <Descriptions.Item label="名称">{selectedElement.data.name}</Descriptions.Item>
                                    <Descriptions.Item label="类型">
                                        <Tag color="blue">{selectedElement.data.entityType || selectedElement.data.entity_type}</Tag>
                                    </Descriptions.Item>
                                    <Descriptions.Item label="置信度">
                                        {((selectedElement.data.confidence || 1) * 100).toFixed(0)}%
                                    </Descriptions.Item>
                                    <Descriptions.Item label="来源片段">
                                        {selectedElement.data.source_chunk_ids?.length || 0} 个
                                        {hasColdStart && <Tag color="orange" style={{ marginLeft: 4 }}>冷启动</Tag>}
                                    </Descriptions.Item>
                                    {selectedElement.data.properties && Object.keys(selectedElement.data.properties).length > 0 && (
                                        <Descriptions.Item label="属性">
                                            <pre style={{ fontSize: 11, margin: 0 }}>
                                                {JSON.stringify(selectedElement.data.properties, null, 2)}
                                            </pre>
                                        </Descriptions.Item>
                                    )}
                                </Descriptions>
                            ) : (
                                <Descriptions column={1} size="small" bordered>
                                    <Descriptions.Item label="关系">{selectedElement.data.relation_type}</Descriptions.Item>
                                    <Descriptions.Item label="源">
                                        {selectedElement.data.source_name || selectedElement.data.source_id}
                                    </Descriptions.Item>
                                    <Descriptions.Item label="目标">
                                        {selectedElement.data.target_name || selectedElement.data.target_id}
                                    </Descriptions.Item>
                                    <Descriptions.Item label="置信度">
                                        {((selectedElement.data.confidence || 1) * 100).toFixed(0)}%
                                    </Descriptions.Item>
                                    <Descriptions.Item label="来源片段">
                                        {selectedElement.data.source_chunk_ids?.length || 0} 个
                                        {hasColdStart && <Tag color="orange" style={{ marginLeft: 4 }}>冷启动</Tag>}
                                    </Descriptions.Item>
                                </Descriptions>
                            )}

                            {/* 来源片段内容 */}
                            <div style={{ marginTop: 12 }}>
                                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8, color: 'var(--gray-700)' }}>
                                    <FileTextOutlined style={{ marginRight: 4 }} />
                                    来源片段内容
                                </div>
                                {chunksLoading ? (
                                    <div style={{ textAlign: 'center', padding: 16 }}>
                                        <Spin size="small" />
                                        <Text type="secondary" style={{ marginLeft: 8 }}>加载中...</Text>
                                    </div>
                                ) : chunks.length > 0 ? (
                                    <Collapse
                                        size="small"
                                        items={chunks.map((chunk) => ({
                                            key: chunk.id,
                                            label: (
                                                <Space size="small">
                                                    <Text type="secondary" style={{ fontSize: 11 }}>片段 #{chunk.index + 1}</Text>
                                                    <Text type="secondary" style={{ fontSize: 11 }}>({chunk.text.length} 字)</Text>
                                                </Space>
                                            ),
                                            children: (
                                                <pre style={{
                                                    fontSize: 12,
                                                    lineHeight: 1.6,
                                                    whiteSpace: 'pre-wrap',
                                                    wordBreak: 'break-all',
                                                    margin: 0,
                                                    padding: 8,
                                                    background: 'var(--gray-50, #f9fafb)',
                                                    borderRadius: 4,
                                                    maxHeight: 200,
                                                    overflow: 'auto',
                                                }}>
                                                    {chunk.text}
                                                </pre>
                                            ),
                                        }))}
                                        defaultActiveKey={chunks.length === 1 ? [chunks[0].id] : []}
                                    />
                                ) : hasColdStart && !chunksLoading ? (
                                    <Tag color="orange">数据来源：冷启动导入</Tag>
                                ) : (
                                    <Text type="secondary" style={{ fontSize: 12 }}>无来源片段</Text>
                                )}
                            </div>
                        </>
                    ) : (
                        <Empty description="点击节点或连线查看详情" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                    )}
                </div>
            </div>

            {/* Chat Drawer */}
            <Drawer
                title={
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
                        <Space>
                            <MessageOutlined style={{ color: 'var(--primary-color)' }} />
                            <span>问图 (GraphRAG)</span>
                        </Space>
                        <Popconfirm
                            title="清空对话记录"
                            description="确定要清空与当前图谱的所有历史对话记录吗？"
                            onConfirm={handleClearChat}
                            okText="清空"
                            cancelText="取消"
                            placement="bottomRight"
                        >
                            <Button type="text" danger icon={<DeleteOutlined />} size="small">清空</Button>
                        </Popconfirm>
                    </div>
                }
                placement="left"
                width={420}
                onClose={() => setChatVisible(false)}
                open={chatVisible}
                mask={false}
                style={{ position: 'absolute' }}
                bodyStyle={{ display: 'flex', flexDirection: 'column', padding: 0 }}
            >
                <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
                    {chatMessages.length === 0 ? (
                        <Empty description="开始基于图谱提问吧！" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                    ) : (
                        <List
                            dataSource={chatMessages}
                            renderItem={(item) => (
                                <List.Item style={{ borderBottom: 'none', padding: '12px 0' }}>
                                    {item.role === 'system' ? (
                                        <div style={{ width: '100%', textAlign: 'center', margin: '8px 0' }}>
                                            <Tooltip title={item.content}>
                                                <Tag icon={<FieldTimeOutlined />} color="default" style={{ cursor: 'pointer', maxWidth: '80%', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                                    早期对话记忆摘要 (悬浮查看详情)
                                                </Tag>
                                            </Tooltip>
                                        </div>
                                    ) : (
                                        <List.Item.Meta
                                            avatar={
                                                <Avatar
                                                    icon={item.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
                                                    style={{ backgroundColor: item.role === 'user' ? '#1677FF' : '#52C41A' }}
                                                />
                                            }
                                            title={<span style={{ fontWeight: 600 }}>{item.role === 'user' ? '你' : '助手'}</span>}
                                            description={
                                                <div style={{ width: '100%' }}>
                                                    <div style={{
                                                        marginTop: 4,
                                                        color: 'var(--text-color)',
                                                        lineHeight: 1.6,
                                                        background: item.role === 'user' ? 'transparent' : '#f5f5f5',
                                                        padding: item.role === 'user' ? 0 : '12px',
                                                        borderRadius: item.role === 'user' ? 0 : '8px',
                                                    }}>
                                                        {item.content
                                                            ? renderMarkdownContent(item.content, item.role === 'assistant')
                                                            : (item.role === 'assistant' && chatLoading ? <Spin size="small" /> : '')
                                                        }
                                                    </div>
                                                    {item.recallInfo && (
                                                        <Collapse ghost size="small" style={{ marginTop: 8 }}>
                                                            <Collapse.Panel 
                                                                header={
                                                                    <Text type="secondary" style={{ fontSize: 12 }}>
                                                                        <AimOutlined /> 召回详情（模式: {GRAPH_RAG_MODE_LABEL[item.recallInfo.mode || ''] || item.recallInfo.mode || '未知'}，实体: {item.recallInfo.summary?.total_nodes ?? item.recallInfo.nodes.length}，关系: {item.recallInfo.summary?.total_edges ?? item.recallInfo.edges.length}）
                                                                    </Text>
                                                                } 
                                                                key="recall"
                                                            >
                                                                <div style={{ fontSize: 11, color: 'var(--gray-600)', background: '#fafafa', padding: '8px', borderRadius: '4px' }}>
                                                                    {item.recallInfo.nodes.length > 0 && (
                                                                        <div style={{ marginBottom: 4 }}>
                                                                            <b style={{ color: '#1677ff' }}>匹配实体：</b>
                                                                            {item.recallInfo.nodes.map(n => n.name).join('、')}
                                                                        </div>
                                                                    )}
                                                                    {item.recallInfo.edges.length > 0 && (
                                                                        <div style={{ marginBottom: 4 }}>
                                                                            <b style={{ color: '#1677ff' }}>关联关系（展示前 {item.recallInfo.edges.length} 条）：</b>
                                                                            <ul style={{ paddingLeft: 16, margin: '4px 0' }}>
                                                                                {item.recallInfo.edges.map((e, i) => <li key={i}>{e}</li>)}
                                                                            </ul>
                                                                        </div>
                                                                    )}
                                                                    {item.recallInfo.chunks.length > 0 && (
                                                                        <div>
                                                                            <b style={{ color: '#1677ff' }}>溯源片段（展示前 {item.recallInfo.chunks.length} 条）：</b>
                                                                            <ul style={{ paddingLeft: 16, margin: '4px 0' }}>
                                                                                {item.recallInfo.chunks.map((c, i) => (
                                                                                    <li key={c.id || i}>
                                                                                        #{(c.index ?? 0) + 1} {c.text ? `- ${c.text}` : ''}
                                                                                    </li>
                                                                                ))}
                                                                            </ul>
                                                                        </div>
                                                                    )}
                                                                </div>
                                                            </Collapse.Panel>
                                                        </Collapse>
                                                    )}
                                                </div>
                                            }
                                        />
                                    )}
                                </List.Item>
                            )}
                        />
                    )}
                    <div ref={messagesEndRef} />
                </div>
                <div style={{ padding: '0 16px 8px' }}>
                    <Space direction="vertical" size="small" style={{ width: '100%' }}>
                        <Checkbox
                            checked={useSubgraphContext}
                            onChange={(e) => setUseSubgraphContext(e.target.checked)}
                            style={{ fontSize: 13, color: 'var(--gray-600)' }}
                        >
                            仅在当前选中实体上下文中提问
                        </Checkbox>
                        <Space wrap style={{ fontSize: 12, color: 'var(--gray-600)' }}>
                            <span>模式：</span>
                            <Select
                                size="small"
                                value={graphRagMode}
                                onChange={(mode) => {
                                    setGraphRagMode(mode);
                                    if (mode === 'graph_flow') {
                                        setGraphRagMaxDegree(2);
                                        setGraphRagMaxStartEntities(5);
                                    } else if (mode === 'graph_full') {
                                        setGraphRagMaxDegree(1);
                                        setGraphRagMaxStartEntities(4);
                                    } else if (mode === 'graph_path') {
                                        setGraphRagMaxDegree(2);
                                        setGraphRagMaxStartEntities(6);
                                    } else if (mode === 'text_only') {
                                        setGraphRagMaxDegree(1);
                                        setGraphRagMaxStartEntities(4);
                                    }
                                }}
                                style={{ width: 120 }}
                                options={[
                                    { label: '独立流式', value: 'graph_flow' },
                                    { label: '全向扩散', value: 'graph_full' },
                                    { label: '路径查找', value: 'graph_path' },
                                    { label: '仅文本块', value: 'text_only' },
                                    { label: '模型直答', value: 'direct' },
                                ]}
                            />
                            <Tooltip title={
                                <div>
                                    <b>独立流式</b>: 平衡模式，优先推荐。<br/>
                                    <b>全向扩散</b>: 覆盖广但噪点多，建议降低检索深度。<br/>
                                    <b>路径查找</b>: 关注实体之间路径，适合关系解释类问题。<br/>
                                    <b>仅文本块</b>: 不看图结构，只基于文本召回。<br/>
                                    <b>模型直答</b>: 不做召回，速度快但不可溯源。
                                </div>
                            }>
                                <Text type="secondary" style={{ cursor: 'help' }}>(?)</Text>
                            </Tooltip>
                        </Space>
                        <Space wrap style={{ fontSize: 12, color: 'var(--gray-600)' }}>
                            <span>检索深度：</span>
                            <Select
                                size="small"
                                disabled={['text_only', 'direct'].includes(graphRagMode)}
                                value={graphRagMaxDegree}
                                onChange={setGraphRagMaxDegree}
                                options={[
                                    { label: '1 度', value: 1 },
                                    { label: '2 度', value: 2 },
                                    { label: '3 度', value: 3 },
                                ]}
                                style={{ width: 80 }}
                            />
                            <span style={{ marginLeft: 8 }}>起点实体数：</span>
                            <InputNumber
                                size="small"
                                disabled={['direct'].includes(graphRagMode)}
                                min={1}
                                max={100}
                                value={graphRagMaxStartEntities}
                                onChange={(v) => v != null && setGraphRagMaxStartEntities(v)}
                                style={{ width: 72 }}
                            />
                        </Space>
                    </Space>
                </div>
                <div style={{ padding: 16, borderTop: '1px solid var(--border-color)', background: '#fff' }}>
                    <Input.TextArea
                        value={chatInput}
                        onChange={(e) => setChatInput(e.target.value)}
                        placeholder="想问点什么关于这个图谱的内容？"
                        autoSize={{ minRows: 2, maxRows: 6 }}
                        onPressEnter={(e) => {
                            if (!e.shiftKey) {
                                e.preventDefault();
                                handleSendMessage();
                            }
                        }}
                        disabled={chatLoading}
                    />
                    <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 12 }}>
                        <Button
                            type="primary"
                            icon={<SendOutlined />}
                            loading={chatLoading}
                            onClick={handleSendMessage}
                        >
                            发送
                        </Button>
                    </div>
                </div>
            </Drawer>
        </div>
    );
}
