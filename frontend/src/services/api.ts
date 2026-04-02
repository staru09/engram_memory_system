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

  async streamMessage(
    message: string,
    threadId: string,
    onToken: (text: string) => void,
    onDone: () => void,
    onError: (error: Error) => void,
  ): Promise<void> {
    try {
      const response = await fetch(`${API_BASE_URL}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, thread_id: threadId }),
      });
      if (!response.ok) throw new Error('Stream failed');
      if (!response.body) throw new Error('No response body');

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6);
          try {
            const parsed = JSON.parse(data);
            if (parsed.done) {
              onDone();
              return;
            }
            if (parsed.text) {
              onToken(parsed.text);
            }
          } catch {
            // skip malformed JSON
          }
        }
      }
      onDone();
    } catch (error) {
      onError(error instanceof Error ? error : new Error(String(error)));
    }
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
