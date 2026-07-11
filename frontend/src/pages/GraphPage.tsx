/**
 * 图谱可视化页面 - Cytoscape.js 力导向图 + 侧边栏
 */
import { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
    Select, Tag, Descriptions, Empty, Space, Button, Checkbox, message, Badge,
    Collapse, Spin, Typography, Drawer, Input, List, Avatar, Popconfirm, Tooltip, InputNumber,
    AutoComplete, Menu, Alert
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
    getChatHistory, clearChatHistory, resetChatSession, searchEntities, getProjectSubgraph,
    buildCommunities, getCommunities
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
    auto: '智能路由',
    graph_flow: '独立流式',
    graph_full: '全向扩散',
    graph_path: '路径查找',
    hippo: '关联检索',
    global: '全局摘要',
    text_only: '仅文本块',
    direct: '模型直答',
};

// 大图阈值：超过后 cose 力导向布局抖动且开销大，降级为确定性的 concentric（按度数同心圆）布局
const LARGE_GRAPH_NODE_THRESHOLD = 500;

function pickLayout(nodeCount: number, idealEdgeLength = 120, duration = 800): any {
    if (nodeCount > LARGE_GRAPH_NODE_THRESHOLD) {
        return {
            name: 'concentric',
            animate: false,
            concentric: (node: any) => node.degree(),
            levelWidth: () => 2,
            minNodeSpacing: 24,
        };
    }
    return {
        name: 'cose',
        animate: true,
        animationDuration: duration,
        nodeOverlap: 20,
        idealEdgeLength: () => idealEdgeLength,
        nodeRepulsion: () => 8000,
        gravity: 0.25,
    };
}

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
    const [graphRagMode, setGraphRagMode] = useState<string>('auto');
    const [communityCount, setCommunityCount] = useState<number>(0);
    const [buildingCommunity, setBuildingCommunity] = useState(false);
    const messagesEndRef = useRef<HTMLDivElement>(null);

    // Exploration State
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<{ id: string; name: string; type: string }[]>([]);
    const [contextMenu, setContextMenu] = useState<{ x: number, y: number, nodeId: string } | null>(null);
    const [explorationMode, setExplorationMode] = useState(true); // 默认开启按需探索模式
    const [exploring, setExploring] = useState(false);
    const [showDocLayer, setShowDocLayer] = useState(false); // 文档结构层（片段锚点/「下一段」边）默认隐藏
    // 图数据源：已发布 / 草稿。抽取完成但尚未发布时，已发布图为空——
    // 不切换数据源的话，探索搜索永远空结果且无解释
    const [graphSource, setGraphSource] = useState<'published' | 'draft'>('published');

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

    // 开启新会话：换会话 ID（旧会话历史保留在服务端），清空当前窗口消息
    const handleNewSession = () => {
        if (!selectedProject) return;
        resetChatSession(selectedProject);
        setChatMessages([]);
        message.success('已开启新会话');
    };

    // 社区摘要：global 问答模式的前置依赖。加载已构建数量，供 UI 判断是否需要提示构建
    const loadCommunityCount = async () => {
        if (!selectedProject) { setCommunityCount(0); return; }
        try {
            const res = await getCommunities(selectedProject);
            setCommunityCount(res.data.communities?.length || 0);
        } catch { setCommunityCount(0); }
    };
    useEffect(() => { loadCommunityCount(); }, [selectedProject]);

    const handleBuildCommunities = async () => {
        if (!selectedProject) return;
        setBuildingCommunity(true);
        try {
            const res = await buildCommunities(selectedProject, 3);
            if (res.data.error) {
                message.warning(res.data.error);
            } else {
                message.success(`已构建 ${res.data.communities} 个社区摘要（检测到 ${res.data.total_detected} 个）`);
                await loadCommunityCount();
            }
        } catch (err: any) {
            message.error(err.response?.data?.detail || '社区摘要构建失败（需已发布图谱）');
        } finally {
            setBuildingCommunity(false);
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
            const res = await searchEntities(selectedProject, val, graphSource);
            setSearchResults(res.data);
        } catch (e) {
            console.error(e);
        }
    };

    const onSearchSelect = async (nodeId: string) => {
        setExploring(true);
        try {
            const res = await getProjectSubgraph(selectedProject, nodeId, 1, graphSource, 'both');
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
            const res = await getProjectSubgraph(selectedProject, nodeId, 1, graphSource, direction);
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

        // 如果不是探索模式，则加载全量（按所选数据源；文档结构层按开关过滤）
        if (!explorationMode) {
            getGraph(selectedProject, graphSource, showDocLayer).then((res) => {
                if (res.data.nodes?.length > 0) {
                    setGraph(res.data);
                } else if (graphSource === 'published') {
                    // 已发布图为空（尚未发布）：透明回退到草稿并明确告知，而非静默偷换数据源
                    getGraph(selectedProject, 'draft', showDocLayer).then((r) => {
                        if (r.data.nodes?.length > 0) {
                            message.info('该项目尚未发布图谱，已切换为草稿图数据');
                            setGraphSource('draft');
                        }
                        setGraph(r.data);
                    });
                } else {
                    setGraph(res.data);
                }
            });
        }
        getSchema(selectedProject).then((res) => setSchema(res.data)).catch(() => null);
    }, [selectedProject, explorationMode, showDocLayer, graphSource]);

    // Build Cytoscape graph
    useEffect(() => {
        if (!graph || !containerRef.current) return;

        // Entity type → color / abstractness mapping
        const colorMap: Record<string, string> = {};
        const abstractnessMap: Record<string, string> = {};
        if (schema) {
            schema.entity_types.forEach((et) => {
                colorMap[et.name] = et.color;
                abstractnessMap[et.name] = et.abstractness || 'surface';
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
                        abstractness: abstractnessMap[node.entity_type] || 'surface',
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
                        // 归纳知识（概念/规则）用菱形 + 虚线边框区分于表面实体，一眼看出"这是概括出来的抽象知识"
                        selector: 'node[abstractness = "inductive"]',
                        style: {
                            shape: 'diamond',
                            'border-width': 3,
                            'border-color': '#9B59B6',
                            'border-style': 'dashed',
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
                layout: pickLayout(elements.filter(e => !e.data.source).length) as any,
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
                    cy!.layout(pickLayout(cy!.nodes().length, 100, 500)).run();
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
        cyRef.current?.layout(pickLayout(cyRef.current.nodes().length)).run();
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

        // 缓冲式流解析：网络分块的边界是任意的，__RECALL_START__/__RECALL_END__
        // 标记可能被截断在两个 chunk 之间。把原始流累积进缓冲区、每次从缓冲区整体
        // 解析，避免半截标记漏进消息文本或召回信息丢失。
        const RECALL_START = '__RECALL_START__';
        const RECALL_END = '__RECALL_END__';
        let streamBuffer = '';
        let recallParsed = false;

        const setRecallInfo = (info: any) => {
            setChatMessages(prev => {
                const newMsgs = [...prev];
                const last = newMsgs[newMsgs.length - 1];
                if (last && last.role === 'assistant') last.recallInfo = info;
                return newMsgs;
            });
        };

        const renderFromBuffer = () => {
            let display = streamBuffer;
            if (!recallParsed) {
                const startIdx = streamBuffer.indexOf(RECALL_START);
                if (startIdx !== -1) {
                    const endIdx = streamBuffer.indexOf(RECALL_END, startIdx);
                    if (endIdx !== -1) {
                        // 完整标记：截出 JSON，拼接前后正文
                        const jsonStr = streamBuffer.substring(startIdx + RECALL_START.length, endIdx);
                        try {
                            setRecallInfo(JSON.parse(jsonStr));
                        } catch { /* 召回信息损坏时不阻塞正文 */ }
                        streamBuffer = streamBuffer.substring(0, startIdx) + streamBuffer.substring(endIdx + RECALL_END.length);
                        recallParsed = true;
                        display = streamBuffer;
                    } else {
                        // 标记未闭合：只展示标记前的正文，等待后续 chunk
                        display = streamBuffer.substring(0, startIdx);
                    }
                } else {
                    // 缓冲区尾部可能是被截断的标记前缀（如 "__RECALL_ST"）：暂不展示这部分
                    for (let keep = Math.min(RECALL_START.length - 1, streamBuffer.length); keep > 0; keep--) {
                        if (streamBuffer.endsWith(RECALL_START.substring(0, keep))) {
                            display = streamBuffer.substring(0, streamBuffer.length - keep);
                            break;
                        }
                    }
                }
            }
            setChatMessages((prev) => {
                const newMsgs = [...prev];
                const lastMsg = newMsgs[newMsgs.length - 1];
                if (lastMsg && lastMsg.role === 'assistant') {
                    lastMsg.content = display;
                }
                return newMsgs;
            });
        };

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
                streamBuffer += chunk;
                renderFromBuffer();
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
                    <Select
                        value={graphSource}
                        onChange={(v) => setGraphSource(v)}
                        style={{ width: 110 }}
                        options={[
                            { label: '📗 已发布图', value: 'published' },
                            { label: '📝 草稿图', value: 'draft' },
                        ]}
                        title="切换查看已发布图谱或复核中的草稿图谱"
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
                    <Tooltip title="开启：从空画布开始，搜索并逐个添加实体、按需展开邻居（适合大图）。关闭：一次性加载全部节点。">
                        <Checkbox
                            checked={explorationMode}
                            onChange={e => setExplorationMode(e.target.checked)}
                        >
                            探索模式
                        </Checkbox>
                    </Tooltip>
                    {!explorationMode && (
                        <Checkbox
                            checked={showDocLayer}
                            onChange={e => setShowDocLayer(e.target.checked)}
                        >
                            文档结构层
                        </Checkbox>
                    )}

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
                            <Empty description={
                                explorationMode
                                    ? `探索模式（${graphSource === 'published' ? '已发布图' : '草稿图'}）：请在左上角搜索框输入实体名并选择，逐个添加到画布`
                                    : (graphSource === 'published'
                                        ? "当前已发布图为空。若已完成抽取，请到人工复核页发布，或在左上角切换为「草稿图」查看"
                                        : "草稿图为空，请先完成文档抽取")
                            } />
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
                                        {selectedElement.data.abstractness === 'inductive' && (
                                            <Tooltip title="归纳知识：从案例概括的抽象知识，可信度由支撑案例数决定">
                                                <Tag color="purple">归纳</Tag>
                                            </Tooltip>
                                        )}
                                    </Descriptions.Item>
                                    <Descriptions.Item label={selectedElement.data.abstractness === 'inductive' ? '可信度' : '置信度'}>
                                        {((selectedElement.data.confidence || 1) * 100).toFixed(0)}%
                                        {selectedElement.data.abstractness === 'inductive' && (
                                            <Text type="secondary" style={{ fontSize: 11, marginLeft: 6 }}>
                                                （由 {(selectedElement.data.source_chunk_ids || []).filter((c: string) => c !== 'cold_start').length} 个支撑案例得出）
                                            </Text>
                                        )}
                                    </Descriptions.Item>
                                    <Descriptions.Item label={selectedElement.data.abstractness === 'inductive' ? '支撑案例' : '来源片段'}>
                                        {(selectedElement.data.source_chunk_ids || []).filter((c: string) => c !== 'cold_start').length} 个
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
                        <Space size={0}>
                            <Tooltip title="开启新会话：当前会话历史将保留在服务端，但不再作为上下文">
                                <Button type="text" size="small" onClick={handleNewSession}>新会话</Button>
                            </Tooltip>
                            <Popconfirm
                                title="清空对话记录"
                                description="确定要清空当前会话的历史对话记录吗？"
                                onConfirm={handleClearChat}
                                okText="清空"
                                cancelText="取消"
                                placement="bottomRight"
                            >
                                <Button type="text" danger icon={<DeleteOutlined />} size="small">清空</Button>
                            </Popconfirm>
                        </Space>
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
                                style={{ width: 130 }}
                                options={[
                                    // 默认只推「智能路由」，其余检索方式收进「高级」，降低终端用户的概念门槛
                                    { label: '智能路由（推荐）', value: 'auto' },
                                    {
                                        label: '高级（手动指定检索方式）',
                                        options: [
                                            { label: '独立流式', value: 'graph_flow' },
                                            { label: '全向扩散', value: 'graph_full' },
                                            { label: '路径查找', value: 'graph_path' },
                                            { label: '关联检索', value: 'hippo' },
                                            { label: '全局摘要', value: 'global' },
                                            { label: '仅文本块', value: 'text_only' },
                                            { label: '模型直答', value: 'direct' },
                                        ],
                                    },
                                ]}
                            />
                            <Tooltip title={
                                <div>
                                    <b>智能路由</b>: 按问题类型自动选择检索方式（推荐）。<br/>
                                    <b>独立流式</b>: 子图扩展，实体邻域类问题。<br/>
                                    <b>全向扩散</b>: 覆盖广但噪点多，建议降低检索深度。<br/>
                                    <b>路径查找</b>: 关注实体之间路径，适合关系解释类问题。<br/>
                                    <b>关联检索</b>: PPR 激活扩散，适合多跳关联问题。<br/>
                                    <b>全局摘要</b>: 基于社区摘要回答宏观主题类问题。<br/>
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
                        {/* 全局摘要模式依赖社区摘要，未构建时给出构建入口，避免"选了没反应" */}
                        {(graphRagMode === 'global' || graphRagMode === 'auto') && (
                            communityCount > 0 ? (
                                <Text type="secondary" style={{ fontSize: 12 }}>
                                    ✅ 已构建 {communityCount} 个社区摘要，全局主题问答可用
                                    <Button type="link" size="small" loading={buildingCommunity} onClick={handleBuildCommunities}>重建</Button>
                                </Text>
                            ) : (
                                <Alert
                                    type="warning"
                                    showIcon
                                    style={{ fontSize: 12 }}
                                    message={
                                        <Space size={4} wrap>
                                            <span>{graphRagMode === 'global' ? '全局摘要模式需先构建社区摘要' : '如需全局主题问答，建议先构建社区摘要'}（需已发布图谱）</span>
                                            <Button type="primary" size="small" loading={buildingCommunity} onClick={handleBuildCommunities}>
                                                构建社区摘要
                                            </Button>
                                        </Space>
                                    }
                                />
                            )
                        )}
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
