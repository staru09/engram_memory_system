import { useState, useEffect, useRef } from 'react';
import { supabaseApi, Thread } from './services/supabase';
import { Message } from './types';
import ChatArea from './components/ChatArea';
import { Eye, ChevronDown } from 'lucide-react';

function formatTimestamp(dateStr?: string): string {
  if (!dateStr) return '';
  const utcStr = dateStr.endsWith('Z') || dateStr.includes('+') ? dateStr : dateStr + 'Z';
  const d = new Date(utcStr);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }).toLowerCase();
}

export default function ViewApp() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [selectedThread, setSelectedThread] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [showThreadPicker, setShowThreadPicker] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const formatMessages = (history: { id: number; role: string; content: string; created_at?: string }[]) =>
    history.map((msg) => ({
      id: msg.id.toString(),
      text: msg.content,
      sender: (msg.role === 'user' ? 'user' : 'bot') as 'user' | 'bot',
      timestamp: formatTimestamp(msg.created_at),
      status: msg.role === 'user' ? 'read' as const : undefined,
      dbId: msg.id,
    }));

  // Load threads on mount
  useEffect(() => {
    const loadThreads = async () => {
      try {
        const threadList = await supabaseApi.getThreads();
        setThreads(threadList);
        if (threadList.length > 0) {
          setSelectedThread(threadList[0].id);
        }
      } catch (error) {
        console.error('Failed to load threads:', error);
      }
    };
    loadThreads();
  }, []);

  // Load messages when thread changes
  useEffect(() => {
    if (!selectedThread) return;
    setIsLoading(true);
    const loadMessages = async () => {
      try {
        const { messages: history, has_more } = await supabaseApi.getMessages(selectedThread);
        setMessages(formatMessages(history));
        setHasMore(has_more);
      } catch (error) {
        console.error('Failed to load messages:', error);
      } finally {
        setIsLoading(false);
      }
    };
    loadMessages();
  }, [selectedThread]);

  // Poll for new messages every 5 seconds
  useEffect(() => {
    if (!selectedThread) return;
    if (pollRef.current) clearInterval(pollRef.current);

    pollRef.current = setInterval(async () => {
      try {
        const { messages: latest } = await supabaseApi.getMessages(selectedThread);
        const formatted = formatMessages(latest);
        setMessages(prev => {
          if (formatted.length !== prev.length || (formatted.length > 0 && formatted[formatted.length - 1].id !== prev[prev.length - 1]?.id)) {
            return formatted;
          }
          return prev;
        });
      } catch {
        // silently ignore poll errors
      }
    }, 5000);

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [selectedThread]);

  const loadOlderMessages = async () => {
    if (!selectedThread || !hasMore || isLoadingMore) return;
    setIsLoadingMore(true);
    try {
      const oldestMsg = messages[0];
      const beforeId = oldestMsg?.dbId;
      if (!beforeId) return;
      const { messages: older, has_more } = await supabaseApi.getMessages(selectedThread, beforeId);
      if (older.length > 0) {
        setMessages(prev => [...formatMessages(older), ...prev]);
      }
      setHasMore(has_more);
    } catch (error) {
      console.error('Failed to load older messages:', error);
    } finally {
      setIsLoadingMore(false);
    }
  };

  const selectedThreadData = threads.find(t => t.id === selectedThread);

  return (
    <div className="font-sans flex flex-col w-full h-[100dvh] overflow-hidden bg-[#efeae2] relative">
      {/* Header */}
      <header className="flex items-center justify-between px-4 py-2 bg-[#f0f2f5] border-b border-gray-300 z-10">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-gray-300 overflow-hidden">
            <img src="https://api.dicebear.com/7.x/avataaars/svg?seed=Ira" alt="Ira" className="w-full h-full object-cover" />
          </div>
          <div>
            <h1 className="text-[#111b21] font-medium text-base">Ira</h1>
            <span className="text-[#667781] text-xs flex items-center gap-1">
              <Eye size={12} /> View Only
            </span>
          </div>
        </div>

        {/* Thread picker */}
        {threads.length > 1 && (
          <div className="relative">
            <button
              onClick={() => setShowThreadPicker(!showThreadPicker)}
              className="flex items-center gap-1 px-3 py-1.5 text-sm text-[#54656f] hover:bg-gray-200 rounded-lg transition-colors"
            >
              Thread {selectedThreadData ? threads.indexOf(selectedThreadData) + 1 : ''}
              <ChevronDown size={16} />
            </button>
            {showThreadPicker && (
              <div className="absolute right-0 top-full mt-1 bg-white rounded-lg shadow-lg border border-gray-200 min-w-[200px] max-h-[300px] overflow-y-auto z-20">
                {threads.map((thread, i) => (
                  <button
                    key={thread.id}
                    onClick={() => {
                      setSelectedThread(thread.id);
                      setShowThreadPicker(false);
                    }}
                    className={`w-full text-left px-4 py-2.5 text-sm hover:bg-gray-100 transition-colors ${
                      thread.id === selectedThread ? 'bg-[#e7fce3] text-[#111b21]' : 'text-[#54656f]'
                    }`}
                  >
                    <div className="font-medium">Thread {i + 1}</div>
                    <div className="text-xs text-[#667781]">
                      {new Date(thread.updated_at.endsWith('Z') ? thread.updated_at : thread.updated_at + 'Z').toLocaleDateString()}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </header>

      {/* Chat area */}
      <ChatArea
        messages={messages}
        isLoading={isLoading}
        hasMore={hasMore}
        isLoadingMore={isLoadingMore}
        onLoadMore={loadOlderMessages}
      />

      {/* Footer — read-only notice instead of input */}
      <footer className="bg-[#f0f2f5] px-4 py-3 flex items-center justify-center z-10 border-t border-gray-200">
        <div className="flex items-center gap-2 text-[#667781] text-sm">
          <Eye size={16} />
          <span>View-only mode — you're watching a live conversation</span>
        </div>
      </footer>
    </div>
  );
}
