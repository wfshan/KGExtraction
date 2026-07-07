/**
 * 后端 API 接口封装
 */
import axios from 'axios';
import { message } from 'antd';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

// ===== 访问令牌与操作人（最小认证；见后端 KG_ACCESS_TOKEN） =====
export const getAccessToken = () => localStorage.getItem('kg_token') || '';
export const setAccessToken = (token: string) => localStorage.setItem('kg_token', token);
export const getOperatorName = () => localStorage.getItem('kg_user') || '';
export const setOperatorName = (name: string) => localStorage.setItem('kg_user', name);

export const authHeaders = (): Record<string, string> => {
  const headers: Record<string, string> = {};
  const token = getAccessToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const user = getOperatorName();
  if (user) headers['X-User'] = user;
  return headers;
};

const api = axios.create({
  baseURL: API_BASE,
  timeout: 60000,
});

api.interceptors.request.use((config) => {
  const headers = authHeaders();
  Object.entries(headers).forEach(([k, v]) => config.headers.set(k, v));
  return config;
});

// 401 统一处理：各页面的"加载失败"无法让用户定位到鉴权问题，
// 在这里给出唯一、明确的指引（key 去重避免并发请求连环弹窗）
api.interceptors.response.use(
  (res) => res,
  (error) => {
    if (error.response?.status === 401) {
      message.error({
        content: '访问未授权：请在右上角「系统配置 → 身份与访问」中填写正确的访问令牌',
        key: 'auth-401',
        duration: 5,
      });
    }
    return Promise.reject(error);
  }
);

// 供原生 fetch 流式调用复用的 401 检查
const throwIfUnauthorized = (response: Response) => {
  if (response.status === 401) {
    message.error({
      content: '访问未授权：请在「系统配置 → 身份与访问」中填写正确的访问令牌',
      key: 'auth-401',
      duration: 5,
    });
    throw new Error('访问未授权（401）');
  }
};

// ===== 系统配置 =====
export interface SystemConfig {
  api_key: string;
  base_url: string;
  model_simple: string;
  model_normal: string;
  model_complex: string;
  similarity_backend: string;
  vector_top_k: number;
  score_threshold: number;
  fast_score_threshold: number;
  chunk_size: number;
  chunk_overlap: number;
  parallel_processes: number;
  extraction_mode: string;
  enable_self_correction: boolean;
  enable_cross_chunk_inference: boolean;
  enable_disambiguation: boolean;
  disambiguation_fast_path_score: number;
  disambiguation_candidate_limit_per_entity: number;
  disambiguation_low_confidence_only: boolean;
  disambiguation_entity_confidence_threshold: number;
  llm_stream_log: boolean;
  database_batch_size: number;
  // 成本控制
  run_token_budget?: number;
  price_per_1k_input_tokens?: number;
  price_per_1k_output_tokens?: number;
  // 证据与门控
  enable_evidence_anchor?: boolean;
  enable_publish_gate?: boolean;
  publish_gate_block?: boolean;
  publish_gate_require_evidence?: boolean;
  [key: string]: any;
}

export const getSystemConfig = () => api.get<SystemConfig>('/system/config');
export const updateSystemConfig = (config: SystemConfig) => api.put<SystemConfig>('/system/config', config);

// ===== 项目管理 =====
export interface Project {
  id: string;
  name: string;
  description: string;
  created_at: string;
  status: string;
  document_count: number;
  run_count: number;
}

export const listProjects = () => api.get<Project[]>('/projects');
export const createProject = (data: { name: string; description: string }) => api.post<Project>('/projects', data);
export const getProject = (id: string) => api.get<Project>(`/projects/${id}`);
export const deleteProject = (id: string) => api.delete(`/projects/${id}`);

// ===== 文档管理 =====
export interface Document {
  id: string;
  project_id: string;
  filename: string;
  original_filename: string;
  file_size: number;
  file_type: string;
  upload_time: string;
  status: string;
  chunk_count: number;
  text_length: number;
  error_message: string | null;
  target_entities?: string[];
  target_relations?: string[];
  chunk_method?: string;
  chunk_size?: number;
  chunk_overlap?: number;
  hierarchical_level?: number;
  max_chunk_length?: number;
}

export const listDocuments = (projectId: string) => api.get<Document[]>(`/projects/${projectId}/documents`);

export const uploadDocument = (projectId: string, file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return api.post<Document>(`/projects/${projectId}/documents`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  });
};

export const deleteDocument = (projectId: string, docId: string) => api.delete(`/projects/${projectId}/documents/${docId}`);

export const updateDocument = (projectId: string, docId: string, data: Partial<Document>) => 
  api.patch<Document>(`/projects/${projectId}/documents/${docId}`, data);

export const rechunkDocument = (projectId: string, docId: string, data: { chunk_method: string, chunk_size: number, chunk_overlap: number, hierarchical_level?: number }) =>
  api.post<Document>(`/projects/${projectId}/documents/${docId}/rechunk`, data);

export const listDocumentChunks = (projectId: string, docId: string) =>
  api.get<any[]>(`/projects/${projectId}/documents/${docId}/chunks`);

// ===== Schema =====
export interface EntityType {
  name: string;
  definition: string;
  examples: string[];
  color: string;
}

export interface RelationType {
  name: string;
  definition: string;
  // 支持单类型（string，兼容旧数据）或多类型（string[]）约束；空 = 不约束
  source_entity_type: string | string[];
  target_entity_type: string | string[];
  examples: string[];
}

export interface SchemaConfig {
  entity_types: EntityType[];
  relation_types: RelationType[];
}

export const getSchema = (projectId: string) => api.get<SchemaConfig>(`/projects/${projectId}/schema`);
export const updateSchema = (projectId: string, schema: SchemaConfig) =>
  api.put<SchemaConfig>(`/projects/${projectId}/schema`, schema);

export interface SchemaSource {
  key: string;
  label: string;
}
export const getSchemaSources = (projectId: string) =>
  api.get<{ sources: SchemaSource[] }>(`/projects/${projectId}/schema/sources`);

export const suggestSchema = (projectId: string, source: string = 'auto') =>
  api.post<SchemaConfig>(`/projects/${projectId}/schema/suggest`, { sample_size: 15, source }, { timeout: 300000 });

// Schema Chat：对话配置 Drawer 打开时自动请求的文档开场白流
export const getProfileSummaryStream = async (
  projectId: string,
  onChunk: (text: string) => void,
  onError: (err: any) => void,
  onFinish: () => void,
  source: string = 'auto'
) => {
  try {
    const response = await fetch(`${API_BASE}/projects/${projectId}/schema/profile-summary?source=${source}`, {
      headers: authHeaders(),
    });
    throwIfUnauthorized(response);
    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
    if (!response.body) throw new Error('No response body');
    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      onChunk(decoder.decode(value, { stream: true }));
    }
    onFinish();
  } catch (err) {
    onError(err);
  }
};

// Schema Chat
export const chatWithSchemaStream = async (
  projectId: string,
  messages: { role: string; content: string }[],
  onMessage: (text: string) => void,
  onError: (err: any) => void,
  onFinish: () => void,
  source: string = 'auto'
) => {
  try {
    const response = await fetch(`${API_BASE}/projects/${projectId}/schema/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(),
      },
      body: JSON.stringify({ messages, source }),
    });

    throwIfUnauthorized(response);
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    if (!response.body) {
      throw new Error('No response body');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const text = decoder.decode(value, { stream: true });
      onMessage(text);
    }
    onFinish();
  } catch (err) {
    onError(err);
  }
};

export const generateSchemaFromChat = (projectId: string, messages: { role: string; content: string }[], source: string = 'auto') =>
  api.post<SchemaConfig>(`/projects/${projectId}/schema/generate-from-chat`, { messages, source });

// ===== 抽取任务 =====
export interface Run {
  id: string;
  project_id: string;
  status: string;
  progress: number;
  current_step: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  description: string;
  stats: {
    total_chunks: number;
    processed_chunks: number;
    entities_extracted: number;
    relations_extracted: number;
    entities_deduplicated: number;
    tokens_used: number;
  };
  error_message: string | null;
}

export interface RunEstimate {
  total_chunks: number;
  calls_per_chunk: number;
  estimated_calls: number;
  estimated_input_tokens: number;
  estimated_output_tokens: number;
  estimated_total_tokens: number;
  estimated_cost: number | null;
  token_budget: number;
  budget_sufficient: boolean;
}

export const estimateRun = (projectId: string) => api.get<RunEstimate>(`/projects/${projectId}/runs/estimate`);
export const startRun = (projectId: string) => api.post<Run>(`/projects/${projectId}/runs`, {});
// 增量抽取：仅处理上次抽取后新增的文档，与既有草稿图谱合并
export const startIncrementalRun = (projectId: string) => api.post<Run>(`/projects/${projectId}/runs/incremental`, {});
export const listRuns = (projectId: string) => api.get<Run[]>(`/projects/${projectId}/runs`);
export const getRun = (projectId: string, runId: string) =>
  api.get<Run>(`/projects/${projectId}/runs/${runId}`);
export const resumeRun = (projectId: string, runId: string) =>
  api.post<Run>(`/projects/${projectId}/runs/${runId}/resume`);
export const restartRun = (projectId: string, runId: string) =>
  api.post<Run>(`/projects/${projectId}/runs/${runId}/restart`);
export const getRunLogs = (projectId: string, runId: string, limit = 500) =>
  api.get<{ logs: string[] }>(`/projects/${projectId}/runs/${runId}/logs`, { params: { limit } });

// ===== 图谱 =====
export interface GraphNode {
  id: string;
  name: string;
  entity_type: string;
  properties: Record<string, any>;
  source_chunk_ids: string[];
  confidence: number;
}

export interface GraphEdge {
  id: string;
  source_id: string;
  target_id: string;
  relation_type: string;
  properties: Record<string, any>;
  source_chunk_ids: string[];
  confidence: number;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  version: number;
  status: string;
  updated_at: string;
}

export const getGraph = (projectId: string, status = 'draft', includeDocLayer = true) =>
  api.get<GraphData>(`/projects/${projectId}/graph`, { params: { status, include_doc_layer: includeDocLayer } });
export const getProjectSubgraph = (projectId: string, nodeIds: string, depth = 1, status = 'published', direction = 'both') =>
  api.get<GraphData>(`/projects/${projectId}/graph/subgraph`, { params: { node_ids: nodeIds, depth, status, direction } });
export const searchEntities = (projectId: string, query: string, status = 'published') =>
  api.get<{ id: string; name: string; type: string }[]>(`/projects/${projectId}/graph/search`, { params: { query, status } });
export const publishGraph = (projectId: string) => api.post(`/projects/${projectId}/graph/publish`);
export const rejectGraph = (projectId: string) => api.post(`/projects/${projectId}/graph/reject`);

// 发布门控预演：不修改数据，返回将被过滤的节点/边与原因
export interface ValidationReport {
  passed: boolean;
  valid_node_count: number;
  valid_edge_count: number;
  violation_count: number;
  violations: { kind: string; target_id: string; rule: string; message: string }[];
  stats: {
    total_nodes: number;
    total_edges: number;
    valid_nodes: number;
    valid_edges: number;
    rejected_nodes: number;
    rejected_edges: number;
  };
}

export const validateGraph = (projectId: string) =>
  api.get<ValidationReport>(`/projects/${projectId}/graph/validate`);
export const exportGraph = (projectId: string) =>
  api.get(`/projects/${projectId}/graph/export`, { responseType: 'blob' });

// 节点/边 CRUD
export const updateNode = (projectId: string, nodeId: string, data: Partial<GraphNode>) =>
  api.patch(`/projects/${projectId}/graph/nodes/${nodeId}`, data);
export const deleteNode = (projectId: string, nodeId: string) =>
  api.delete(`/projects/${projectId}/graph/nodes/${nodeId}`);
export const updateEdge = (projectId: string, edgeId: string, data: Partial<GraphEdge>) =>
  api.patch(`/projects/${projectId}/graph/edges/${edgeId}`, data);
export const deleteEdge = (projectId: string, edgeId: string) =>
  api.delete(`/projects/${projectId}/graph/edges/${edgeId}`);

// ===== 人工复核队列 / 审计 / 被拒项 =====
export interface ReviewItem {
  kind: 'node' | 'edge';
  id: string;
  title: string;
  entity_type?: string;
  relation_type?: string;
  confidence: number;
  run_id: string;
  change: 'new' | 'changed';
  violations: string[];
  evidence_verified: boolean;
  evidence_quotes: { chunk_id: string; quote: string; verified?: boolean | null }[];
  source_chunk_count: number;
  review_status: 'pending' | 'approved';
}

export interface ReviewQueue {
  total: number;
  pending: number;
  with_violations: number;
  unverified_evidence: number;
  items: ReviewItem[];
}

export const getReviewQueue = (projectId: string, runId?: string) =>
  api.get<ReviewQueue>(`/projects/${projectId}/graph/review/queue`, { params: runId ? { run_id: runId } : {} });

export const postReviewDecision = (projectId: string, data: { kind: string; target_id: string; decision: 'approve' | 'reject'; reason?: string }) =>
  api.post(`/projects/${projectId}/graph/review/decision`, data);

export interface AuditLogEntry {
  id: string;
  ts: string;
  actor: string;
  action: string;
  target_kind: string;
  target_id: string;
  detail: Record<string, any>;
}

export const getAuditLog = (projectId: string, limit = 100) =>
  api.get<{ logs: AuditLogEntry[] }>(`/projects/${projectId}/graph/audit`, { params: { limit } });

export interface RejectedItemsResponse {
  stats: {
    total: number;
    by_reason: Record<string, number>;
    entity_types: { name: string; count: number; examples: string[] }[];
    relation_types: { name: string; count: number; examples: string[] }[];
  };
  items: any[];
}

export const getRejectedItems = (projectId: string, runId?: string) =>
  api.get<RejectedItemsResponse>(`/projects/${projectId}/graph/rejected`, { params: runId ? { run_id: runId } : {} });

// 图谱数据导入（冷启动）
export const downloadGraphTemplate = (projectId: string) =>
  api.get(`/projects/${projectId}/graph/template`, { responseType: 'blob' });
export const importGraphData = (projectId: string, file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return api.post(`/projects/${projectId}/graph/import`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  });
};

// ===== 片段内容查询 =====
export interface ChunkContent {
  id: string;
  doc_id: string;
  text: string;
  index: number;
}

export const getChunksByIds = (projectId: string, chunkIds: string[]) =>
  api.post<ChunkContent[]>(`/projects/${projectId}/chunks`, { chunk_ids: chunkIds });

// ===== 健康检查 =====
export const healthCheck = () => api.get('/health');

// ===== GraphRAG (问图) =====
export interface GraphRAGOptions {
  max_degree?: number;
  max_start_entities?: number;
  retrieval_mode?: string;
}

export const chatWithGraphStream = async (
  projectId: string,
  query: string,
  onMessage: (text: string) => void,
  onError: (err: any) => void,
  onFinish: () => void,
  options?: GraphRAGOptions
) => {
  try {
    const body: { query: string; options?: GraphRAGOptions } = { query };
    if (options && (options.max_degree != null || options.max_start_entities != null || options.retrieval_mode != null)) {
      body.options = {};
      if (options.max_degree != null) body.options.max_degree = options.max_degree;
      if (options.max_start_entities != null) body.options.max_start_entities = options.max_start_entities;
      if (options.retrieval_mode != null) body.options.retrieval_mode = options.retrieval_mode;
    }
    const response = await fetch(`${API_BASE}/projects/${projectId}/graph-rag/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(),
      },
      body: JSON.stringify(body),
    });

    throwIfUnauthorized(response);
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    if (!response.body) {
      throw new Error('No response body');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const text = decoder.decode(value, { stream: true });
      onMessage(text);
    }
    onFinish();
  } catch (err) {
    onError(err);
  }
};

export const getChatHistory = (projectId: string) =>
  api.get<{ history: { role: string; content: string }[] }>(`/projects/${projectId}/graph-rag/chat`);

export const clearChatHistory = (projectId: string) =>
  api.delete(`/projects/${projectId}/graph-rag/chat`);

export default api;
