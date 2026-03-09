import React, { useState, useEffect, useRef } from 'react';
import { api } from './services/api';
import { Message } from './types';
import Header from './components/Header';
import ChatArea from './components/ChatArea';
import MessageInput from './components/MessageInput';

const THREAD_ID_KEY = 'engram_thread_id';

function formatTimestamp(dateStr?: string): string {
  if (!dateStr) return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }).toLowerCase();
  const d = new Date(dateStr);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }).toLowerCase();
}

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputText, setInputText] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const threadIdRef = useRef<string | null>(null);

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

        // Load chat history for this thread
        const { messages: history } = await api.getHistory(threadId);
        if (history && history.length > 0) {
          const formatted = history.map((msg, index) => ({
            id: (msg.id || index).toString(),
            text: msg.content,
            sender: (msg.role === 'user' ? 'user' : 'bot') as 'user' | 'bot',
            timestamp: formatTimestamp(msg.created_at),
            status: msg.role === 'user' ? 'read' as const : undefined,
          }));
          setMessages(formatted);
        }
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

  const handleNewChat = async () => {
    try {
      const { thread_id } = await api.createThread();
      threadIdRef.current = thread_id;
      localStorage.setItem(THREAD_ID_KEY, thread_id);
      setMessages([]);
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
      <Header onNewChat={handleNewChat} />
      <ChatArea messages={messages} isLoading={isLoading} />
      <MessageInput 
          inputText={inputText} 
          setInputText={setInputText} 
          handleSendMessage={handleSendMessage} 
        />
    </div>
  );
}
