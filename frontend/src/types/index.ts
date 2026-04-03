export interface Message {
  id: string;
  text: string;
  sender: 'user' | 'bot';
  timestamp: string;
  status?: 'sent' | 'delivered' | 'read';
  dbId?: number;
  timings?: {
    ctx_ms: number;
    prompt_ms: number;
    first_token_ms: number;
    llm_ms: number;
    total_ms: number;
  };
}
