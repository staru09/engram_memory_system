import React, { useState, useRef, useEffect } from 'react';
import { X, Search, Clock, Zap } from 'lucide-react';
import { api } from '../services/api';

interface QueryModalProps {
  isOpen: boolean;
  onClose: () => void;
  threadId?: string;
}

export default function QueryModal({ isOpen, onClose, threadId }: QueryModalProps) {
  const [queryText, setQueryText] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [response, setResponse] = useState<string | null>(null);
  const [timing, setTiming] = useState<{ total_retrieval_s: number; llm_response_s: number; mode?: string; prompt_tokens?: number } | null>(null);
  const [mode, setMode] = useState<'search' | 'summary' | 'date'>('search');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) {
      setQueryText('');
      setResponse(null);
      setTiming(null);
    }
  }, [isOpen]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!queryText.trim() || isLoading) return;

    setIsLoading(true);
    setResponse(null);
    setTiming(null);

    try {
      const result = await api.queryMemory(queryText.trim(), threadId, mode);
      setResponse(result.response);
      setTiming({
        ...(result.metadata?.timing || {}),
        prompt_tokens: result.metadata?.prompt_tokens,
      } as any);
    } catch {
      setResponse('Backend se connect nahi ho paya. Server check karo.');
    } finally {
      setIsLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="absolute inset-0 bg-black/50" />
      <div
        className="relative bg-white rounded-2xl w-full max-w-2xl max-h-[85vh] flex flex-col shadow-2xl overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 bg-[#f0f2f5]">
          <div className="flex items-center gap-2">
            <Search size={20} className="text-[#00a884]" />
            <h2 className="text-[#111b21] font-semibold text-lg">Query Memory</h2>
          </div>
          <button onClick={onClose} className="p-1.5 hover:bg-gray-200 rounded-full transition-colors">
            <X size={20} className="text-[#54656f]" />
          </button>
        </div>

        {/* Mode Toggle */}
        <div className="flex items-center gap-2 px-5 py-2 border-b border-gray-100 bg-gray-50">
          <span className="text-[11px] text-[#667781] font-medium mr-1">Mode:</span>
          {(['search', 'summary', 'date'] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`px-3 py-1 rounded-full text-[11px] font-medium transition-colors ${
                mode === m
                  ? 'bg-[#00a884] text-white'
                  : 'bg-white text-[#667781] border border-gray-200 hover:border-[#00a884] hover:text-[#00a884]'
              }`}
            >
              {m === 'search' ? 'Search' : m === 'summary' ? 'Summary' : 'Date'}
            </button>
          ))}
          <span className="text-[10px] text-[#667781] ml-auto">
            {mode === 'search' && 'Top-5 hybrid search'}
            {mode === 'summary' && 'Search + rolling summary'}
            {mode === 'date' && 'All facts for detected date'}
          </span>
        </div>

        {/* Search Input */}
        <form onSubmit={handleSubmit} className="flex items-center gap-3 px-5 py-3 border-b border-gray-100">
          <input
            ref={inputRef}
            type="text"
            value={queryText}
            onChange={e => setQueryText(e.target.value)}
            placeholder="Kuch bhi poocho memory se..."
            className="flex-1 px-4 py-2.5 bg-gray-100 rounded-full text-sm text-[#111b21] placeholder-[#667781] outline-none focus:ring-2 focus:ring-[#00a884]/30"
          />
          <button
            type="submit"
            disabled={!queryText.trim() || isLoading}
            className="px-5 py-2.5 bg-[#00a884] text-white rounded-full text-sm font-medium hover:bg-[#008f6f] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {isLoading ? '...' : 'Ask'}
          </button>
        </form>

        {/* Results Area */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {isLoading && (
            <div className="flex items-center gap-3 text-[#667781] py-8 justify-center">
              <div className="flex gap-1">
                <span className="w-2 h-2 bg-[#00a884] rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-2 h-2 bg-[#00a884] rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-2 h-2 bg-[#00a884] rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
              <span className="text-sm">Searching memory...</span>
            </div>
          )}

          {response && !isLoading && (
            <div className="space-y-4">
              {/* Answer */}
              <div className="bg-white border border-gray-200 rounded-xl p-4">
                <p className="text-[#111b21] text-sm leading-relaxed whitespace-pre-wrap">{response}</p>
              </div>

              {/* Timing */}
              {timing && (
                <div className="space-y-2">
                  <div className="flex items-center gap-3 text-xs text-[#667781]">
                    <span className="flex items-center gap-1">
                      <Clock size={12} />
                      Retrieval: {timing.total_retrieval_s}s
                    </span>
                    <span className="flex items-center gap-1">
                      <Zap size={12} />
                      LLM: {timing.llm_response_s}s
                    </span>
                    {(timing as any).prompt_tokens && (
                      <span className="text-[10px]">
                        {(timing as any).prompt_tokens} tokens
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-[#667781]">
                    {(timing as any).embed_s > 0 && (
                      <span className="flex items-center gap-1">
                        <span className="w-2 h-2 rounded-sm bg-blue-400" />
                        Embed: {(timing as any).embed_s}s
                      </span>
                    )}
                    {(timing as any).hybrid_search_s > 0 && (
                      <span className="flex items-center gap-1">
                        <span className="w-2 h-2 rounded-sm bg-green-400" />
                        Search: {(timing as any).hybrid_search_s}s
                      </span>
                    )}
                    {(timing as any).date_fetch_s != null && (
                      <span className="flex items-center gap-1">
                        <span className="w-2 h-2 rounded-sm bg-purple-400" />
                        Date fetch: {(timing as any).date_fetch_s}s
                      </span>
                    )}
                    {(timing as any).context_s != null && (
                      <span className="flex items-center gap-1">
                        <span className="w-2 h-2 rounded-sm bg-yellow-400" />
                        Context: {(timing as any).context_s}s
                      </span>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Empty state */}
          {!response && !isLoading && (
            <div className="flex flex-col items-center justify-center py-12 text-[#667781]">
              <Search size={40} strokeWidth={1.5} className="mb-3 opacity-30" />
              <p className="text-sm">Ask anything about your past conversations</p>
              <p className="text-xs mt-1 opacity-70">This won't be stored in chat or ingested</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
