import React, { useState, useEffect, useRef } from 'react';
import { api } from './services/api';
import { Message } from './types';
import Header from './components/Header';
import ChatArea from './components/ChatArea';
import MessageInput from './components/MessageInput';

const THREAD_ID_KEY = 'engram_thread_id';

function formatTimestamp(dateStr?: string): string {
  if (!dateStr) return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }).toLowerCase();
  // DB stores UTC but without 'Z' suffix — append it so browser converts to local time
  const utcStr = dateStr.endsWith('Z') || dateStr.includes('+') ? dateStr : dateStr + 'Z';
  const d = new Date(utcStr);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }).toLowerCase();
}

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputText, setInputText] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const threadIdRef = useRef<string | null>(null);

  const formatMessages = (history: { id?: number; role: string; content: string; created_at?: string }[]) =>
    history.map((msg, index) => ({
      id: (msg.id || index).toString(),
      text: msg.content,
      sender: (msg.role === 'user' ? 'user' : 'bot') as 'user' | 'bot',
      timestamp: formatTimestamp(msg.created_at),
      status: msg.role === 'user' ? 'read' as const : undefined,
      dbId: msg.id,
    }));

  // Initialize: restore or create a thread, then load history
  useEffect(() => {
    const init = async () => {
      try {
        // Try to restore existing thread
        let threadId = localStorage.getItem(THREAD_ID_KEY);

        if (!threadId) {
          // Create a new thread
          const { thread_id } = await api.createThread();
          threadId = thread_id;
          localStorage.setItem(THREAD_ID_KEY, threadId);
        }

        threadIdRef.current = threadId;

        // Load latest messages for this thread
        const { messages: history, has_more } = await api.getHistory(threadId);
        if (history && history.length > 0) {
          setMessages(formatMessages(history));
        }
        setHasMore(has_more);
      } catch (error) {
        console.log('Backend not reachable, starting with empty chat.');
        // Still try to set a thread ID for when backend comes back
        if (!threadIdRef.current) {
          threadIdRef.current = crypto.randomUUID();
          localStorage.setItem(THREAD_ID_KEY, threadIdRef.current);
        }
      }
    };

    init();
  }, []);

  const loadOlderMessages = async () => {
    if (!threadIdRef.current || !hasMore || isLoadingMore) return;
    setIsLoadingMore(true);
    try {
      const oldestMsg = messages[0];
      const beforeId = oldestMsg?.dbId;
      if (!beforeId) return;
      const { messages: older, has_more } = await api.getHistory(threadIdRef.current, beforeId);
      if (older && older.length > 0) {
        setMessages(prev => [...formatMessages(older), ...prev]);
      }
      setHasMore(has_more);
    } catch (error) {
      console.error('Failed to load older messages:', error);
    } finally {
      setIsLoadingMore(false);
    }
  };

  const handleNewChat = async () => {
    try {
      const { thread_id } = await api.createThread();
      threadIdRef.current = thread_id;
      localStorage.setItem(THREAD_ID_KEY, thread_id);
      setMessages([]);
      setHasMore(false);
    } catch (error) {
      console.error('Failed to create new thread:', error);
    }
  };

  const handleSendMessage = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!inputText.trim() || !threadIdRef.current) return;

    const newUserMessage: Message = {
      id: Date.now().toString(),
      text: inputText.trim(),
      sender: 'user',
      timestamp: formatTimestamp(),
      status: 'sent'
    };

    setMessages(prev => [...prev, newUserMessage]);
    setInputText('');
    setIsLoading(true);

    try {
      const data = await api.sendMessage(newUserMessage.text, threadIdRef.current!);

      const newBotMessage: Message = {
        id: (Date.now() + 1).toString(),
        text: data.response || 'Message received.',
        sender: 'bot',
        timestamp: formatTimestamp(),
      };

      setMessages(prev => {
        const updated = prev.map(msg =>
          msg.id === newUserMessage.id ? { ...msg, status: 'read' as const } : msg
        );
        return [...updated, newBotMessage];
      });
    } catch (error) {
      console.error('Failed to send message:', error);
      const fallbackMessage: Message = {
        id: (Date.now() + 1).toString(),
        text: 'Could not reach the backend. Make sure the FastAPI server is running.',
        sender: 'bot',
        timestamp: formatTimestamp(),
      };
      setMessages(prev => {
        const updated = prev.map(msg =>
          msg.id === newUserMessage.id ? { ...msg, status: 'read' as const } : msg
        );
        return [...updated, fallbackMessage];
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="font-sans flex flex-col w-full h-[100dvh] overflow-hidden bg-[#efeae2] relative">
      <Header />
      <ChatArea messages={messages} isLoading={isLoading} hasMore={hasMore} isLoadingMore={isLoadingMore} onLoadMore={loadOlderMessages} />
      <MessageInput 
          inputText={inputText} 
          setInputText={setInputText} 
          handleSendMessage={handleSendMessage} 
        />
    </div>
  );
}
