const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export interface ChatMessage {
  id?: number;
  role: 'user' | 'assistant';
  content: string;
  created_at?: string;
}

export interface Thread {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export const api = {
  async createThread(title?: string): Promise<{ thread_id: string }> {
    const response = await fetch(`${API_BASE_URL}/threads`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: title || null }),
    });
    if (!response.ok) throw new Error('Failed to create thread');
    return response.json();
  },

  async getThreads(): Promise<{ threads: Thread[] }> {
    const response = await fetch(`${API_BASE_URL}/threads`);
    if (!response.ok) throw new Error('Failed to fetch threads');
    return response.json();
  },

  async getHistory(threadId: string, beforeId?: number): Promise<{ messages: ChatMessage[]; has_more: boolean }> {
    const params = new URLSearchParams({ limit: '50' });
    if (beforeId) params.append('before_id', beforeId.toString());
    const response = await fetch(`${API_BASE_URL}/threads/${threadId}/messages?${params}`);
    if (!response.ok) throw new Error('Failed to fetch history');
    return response.json();
  },

  async sendMessage(message: string, threadId: string): Promise<{ response: string; thread_id: string }> {
    const response = await fetch(`${API_BASE_URL}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, thread_id: threadId }),
    });
    if (!response.ok) throw new Error('Failed to send message');
    return response.json();
  },

  async queryMemory(query: string, threadId?: string): Promise<QueryResponse> {
    const response = await fetch(`${API_BASE_URL}/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, thread_id: threadId || null }),
    });
    if (!response.ok) throw new Error('Query failed');
    return response.json();
  }
};

export interface QueryMetadata {
  facts: Array<{
    fact_id: number;
    fact_text: string;
    rrf_score: number;
    conversation_date: string | null;
    memcell_id: number;
  }>;
  episodes: Array<{
    memcell_id: number;
    episode_text: string;
    relevance_score: number;
    semantic_sim: number | null;
    staleness: number | null;
    conversation_date: string | null;
    scene_id: number;
  }>;
  foresight: Array<{
    id: number;
    description: string;
    valid_from: string | null;
    valid_until: string | null;
    source_date: string | null;
    query_sim: number | null;
  }>;
  scenes: Array<{
    scene_id: number;
    best_score: number;
    fact_ids: number[];
  }>;
  profile_included: boolean;
  sufficiency: {
    is_sufficient: boolean;
    rounds: number;
    reasoning: string;
    missing_information: string[];
  };
  timing: {
    total_retrieval_s: number;
    llm_response_s: number;
  };
}

export interface QueryResponse {
  response: string;
  metadata: QueryMetadata;
  query_time: string;
}
