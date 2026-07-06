/**
 * 后端 API 接口封装
 */
import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

const api = axios.create({
  baseURL: API_BASE,
  timeout: 60000,
});

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
  source_entity_type: string;
  target_entity_type: string;
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
    const response = await fetch(`${API_BASE}/projects/${projectId}/schema/profile-summary?source=${source}`);
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
      },
      body: JSON.stringify({ messages, source }),
    });

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

export const startRun = (projectId: string) => api.post<Run>(`/projects/${projectId}/runs`, {});
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

export const getGraph = (projectId: string, status = 'draft') =>
  api.get<GraphData>(`/projects/${projectId}/graph`, { params: { status } });
export const getProjectSubgraph = (projectId: string, nodeIds: string, depth = 1, status = 'published', direction = 'both') =>
  api.get<GraphData>(`/projects/${projectId}/graph/subgraph`, { params: { node_ids: nodeIds, depth, status, direction } });
export const searchEntities = (projectId: string, query: string, status = 'published') =>
  api.get<{ id: string; name: string; type: string }[]>(`/projects/${projectId}/graph/search`, { params: { query, status } });
export const publishGraph = (projectId: string) => api.post(`/projects/${projectId}/graph/publish`);
export const rejectGraph = (projectId: string) => api.post(`/projects/${projectId}/graph/reject`);
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
      },
      body: JSON.stringify(body),
    });

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
