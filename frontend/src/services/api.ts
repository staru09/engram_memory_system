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

  async getHistory(threadId: string): Promise<{ messages: ChatMessage[] }> {
    const response = await fetch(`${API_BASE_URL}/threads/${threadId}/messages`);
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
  }
};
