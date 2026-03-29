import React, { useState, useRef, useEffect } from 'react';
import { X, Search, ChevronDown, ChevronUp, Clock, Zap } from 'lucide-react';
import { api, QueryMetadata } from '../services/api';

interface QueryModalProps {
  isOpen: boolean;
  onClose: () => void;
  threadId?: string;
}

export default function QueryModal({ isOpen, onClose, threadId }: QueryModalProps) {
  const [queryText, setQueryText] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [response, setResponse] = useState<string | null>(null);
  const [metadata, setMetadata] = useState<QueryMetadata | null>(null);
  const [showMetadata, setShowMetadata] = useState(false);
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set());
  const [fastMode, setFastMode] = useState(false);
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
      setMetadata(null);
      setShowMetadata(false);
      setExpandedSections(new Set());
    }
  }, [isOpen]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const toggleSection = (section: string) => {
    setExpandedSections(prev => {
      const next = new Set(prev);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });
  };

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!queryText.trim() || isLoading) return;

    setIsLoading(true);
    setResponse(null);
    setMetadata(null);
    setShowMetadata(false);

    try {
      const result = await api.queryMemory(queryText.trim(), threadId, fastMode);
      setResponse(result.response);
      setMetadata(result.metadata);
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
          <div className="flex items-center gap-2">
            <button
              onClick={() => setFastMode(!fastMode)}
              className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                fastMode
                  ? 'bg-[#00a884] text-white'
                  : 'bg-gray-200 text-[#667781]'
              }`}
              title={fastMode ? 'Fast mode: profile + summaries only' : 'Normal mode: full search pipeline'}
            >
              <Zap size={12} className="inline mr-1" />
              {fastMode ? 'Fast' : 'Normal'}
            </button>
            <button onClick={onClose} className="p-1.5 hover:bg-gray-200 rounded-full transition-colors">
              <X size={20} className="text-[#54656f]" />
            </button>
          </div>
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

              {/* Timing + Complexity Badge */}
              {metadata?.timing && (
                <div className="space-y-2">
                  <div className="flex items-center gap-3 text-xs text-[#667781]">
                    <span className={`px-2 py-0.5 rounded-full font-medium ${
                      metadata.mode === 'fast'
                        ? 'bg-green-100 text-green-700'
                        : 'bg-blue-100 text-blue-700'
                    }`}>
                      {metadata.mode === 'fast' ? 'Fast' : 'Normal'}
                    </span>
                    <span className="flex items-center gap-1">
                      <Clock size={12} />
                      Retrieval: {metadata.timing.total_retrieval_s}s
                    </span>
                    <span className="flex items-center gap-1">
                      <Zap size={12} />
                      LLM: {metadata.timing.llm_response_s}s
                    </span>
                  </div>

                  {/* Per-step timing bar */}
                  <TimingBar timing={metadata.timing} />
                </div>
              )}

              {/* Metadata Toggle */}
              {metadata && Object.keys(metadata).length > 0 && (
                <button
                  onClick={() => setShowMetadata(!showMetadata)}
                  className="flex items-center gap-1.5 text-xs text-[#00a884] hover:text-[#008f6f] font-medium transition-colors"
                >
                  {showMetadata ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  {showMetadata ? 'Hide' : 'Show'} Retrieval Details
                </button>
              )}

              {/* Metadata Sections */}
              {showMetadata && metadata && (
                <div className="space-y-2">
                  {/* Facts */}
                  {metadata.facts?.length > 0 && (
                    <MetadataSection
                      title={`Facts (${metadata.facts.length})`}
                      isExpanded={expandedSections.has('facts')}
                      onToggle={() => toggleSection('facts')}
                    >
                      {metadata.facts.map((f, i) => (
                        <div key={i} className="flex justify-between items-start gap-2 py-1.5 border-b border-gray-50 last:border-0">
                          <span className="text-xs text-[#111b21]">{f.fact_text}</span>
                          <div className="flex items-center gap-2 shrink-0">
                            {f.conversation_date && (
                              <span className="text-[10px] text-[#667781]">{f.conversation_date}</span>
                            )}
                            <span className="text-[10px] text-[#00a884] font-mono">{f.rrf_score.toFixed(4)}</span>
                          </div>
                        </div>
                      ))}
                    </MetadataSection>
                  )}

                  {/* Episodes */}
                  {metadata.episodes?.length > 0 && (
                    <MetadataSection
                      title={`Episodes (${metadata.episodes.length})`}
                      isExpanded={expandedSections.has('episodes')}
                      onToggle={() => toggleSection('episodes')}
                    >
                      {metadata.episodes.map((e, i) => (
                        <div key={i} className="py-2 border-b border-gray-50 last:border-0">
                          <p className="text-xs text-[#111b21] leading-relaxed">{e.episode_text}</p>
                          <div className="flex items-center gap-3 mt-1 text-[10px] text-[#667781]">
                            {e.conversation_date && <span>{e.conversation_date}</span>}
                          </div>
                        </div>
                      ))}
                    </MetadataSection>
                  )}

                  {/* Foresight */}
                  {metadata.foresight?.length > 0 && (
                    <MetadataSection
                      title={`Foresight (${metadata.foresight.length})`}
                      isExpanded={expandedSections.has('foresight')}
                      onToggle={() => toggleSection('foresight')}
                    >
                      {metadata.foresight.map((fs, i) => (
                        <div key={i} className="py-1.5 border-b border-gray-50 last:border-0">
                          <p className="text-xs text-[#111b21]">{fs.description}</p>
                          <div className="flex items-center gap-3 mt-0.5 text-[10px] text-[#667781]">
                            {fs.source_date && <span>from: {fs.source_date}</span>}
                            <span>until: {fs.valid_until || 'indefinite'}</span>
                            {fs.query_sim != null && <span>sim: {fs.query_sim.toFixed(3)}</span>}
                          </div>
                        </div>
                      ))}
                    </MetadataSection>
                  )}
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


function TimingBar({ timing }: { timing: QueryMetadata['timing'] }) {
  const steps = [
    { key: 'classifier_s', label: 'Classify', color: 'bg-purple-400', value: timing.classifier_s },
    { key: 'embedding_s', label: 'Embed', color: 'bg-blue-400', value: timing.embedding_s },
    { key: 'search_s', label: 'Search', color: 'bg-green-400', value: timing.search_s },
    { key: 'foresight_s', label: 'Foresight', color: 'bg-yellow-400', value: timing.foresight_s },
    { key: 'context_compose_s', label: 'Compose', color: 'bg-orange-400', value: timing.context_compose_s },
    { key: 'llm_response_s', label: 'LLM', color: 'bg-red-400', value: timing.llm_response_s },
  ].filter(s => s.value > 0);

  if (steps.length === 0) return null;

  return (
    <div className="space-y-1">
      <div className="flex gap-0.5 h-2 rounded-full overflow-hidden bg-gray-100">
        {steps.map(s => (
          <div
            key={s.key}
            className={`${s.color} transition-all`}
            style={{ flex: s.value }}
            title={`${s.label}: ${s.value}s`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-[#667781]">
        {steps.map(s => (
          <span key={s.key} className="flex items-center gap-1">
            <span className={`w-2 h-2 rounded-sm ${s.color}`} />
            {s.label}: {s.value}s
          </span>
        ))}
      </div>
    </div>
  );
}


function MetadataSection({
  title,
  isExpanded,
  onToggle,
  children,
}: {
  title: string;
  isExpanded: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <button
        onClick={onToggle}
        className="flex items-center justify-between w-full px-3 py-2 bg-gray-50 hover:bg-gray-100 transition-colors"
      >
        <span className="text-xs font-medium text-[#111b21]">{title}</span>
        {isExpanded ? <ChevronUp size={14} className="text-[#667781]" /> : <ChevronDown size={14} className="text-[#667781]" />}
      </button>
      {isExpanded && <div className="px-3 py-2">{children}</div>}
    </div>
  );
}
