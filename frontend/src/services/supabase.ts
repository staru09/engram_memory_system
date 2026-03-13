const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL;
const SUPABASE_KEY = import.meta.env.VITE_SUPABASE_KEY;

const headers = {
  'apikey': SUPABASE_KEY,
  'Authorization': `Bearer ${SUPABASE_KEY}`,
  'Content-Type': 'application/json',
};

export interface Thread {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: number;
  thread_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
}

export const supabaseApi = {
  async getThreads(): Promise<Thread[]> {
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/chat_threads?order=updated_at.desc&limit=20`,
      { headers }
    );
    if (!res.ok) throw new Error('Failed to fetch threads');
    return res.json();
  },

  async getMessages(threadId: string, beforeId?: number): Promise<{ messages: ChatMessage[]; has_more: boolean }> {
    let url = `${SUPABASE_URL}/rest/v1/chat_messages?thread_id=eq.${threadId}&order=id.desc&limit=50`;
    if (beforeId) url += `&id=lt.${beforeId}`;
    const res = await fetch(url, { headers });
    if (!res.ok) throw new Error('Failed to fetch messages');
    const messages: ChatMessage[] = await res.json();
    return {
      messages: messages.reverse(),
      has_more: messages.length === 50,
    };
  },
};
