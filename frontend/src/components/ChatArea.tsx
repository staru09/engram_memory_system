import React, { useEffect, useRef, useCallback } from 'react';
import { Message } from '../types';
import MessageBubble from './MessageBubble';

interface ChatAreaProps {
  messages: Message[];
  isLoading: boolean;
  hasMore: boolean;
  isLoadingMore: boolean;
  onLoadMore: () => void;
}

export default function ChatArea({ messages, isLoading, hasMore, isLoadingMore, onLoadMore }: ChatAreaProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const prevScrollHeightRef = useRef<number>(0);
  const isInitialLoad = useRef(true);

  // Scroll to bottom on new messages (but not when loading older ones)
  useEffect(() => {
    if (isInitialLoad.current) {
      // On initial load, scroll to bottom instantly
      messagesEndRef.current?.scrollIntoView();
      isInitialLoad.current = false;
      return;
    }
    if (!isLoadingMore) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, isLoadingMore]);

  // Preserve scroll position after prepending older messages
  useEffect(() => {
    if (isLoadingMore) {
      prevScrollHeightRef.current = containerRef.current?.scrollHeight || 0;
    }
  }, [isLoadingMore]);

  useEffect(() => {
    if (!isLoadingMore && prevScrollHeightRef.current > 0 && containerRef.current) {
      const newScrollHeight = containerRef.current.scrollHeight;
      containerRef.current.scrollTop = newScrollHeight - prevScrollHeightRef.current;
      prevScrollHeightRef.current = 0;
    }
  }, [messages, isLoadingMore]);

  // Detect scroll to top
  const handleScroll = useCallback(() => {
    if (!containerRef.current || !hasMore || isLoadingMore) return;
    if (containerRef.current.scrollTop < 100) {
      onLoadMore();
    }
  }, [hasMore, isLoadingMore, onLoadMore]);

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto p-3 sm:p-4 md:px-[8%] lg:px-[10%] relative"
      style={{
        backgroundImage: 'url("https://static.whatsapp.net/rsrc.php/v3/yl/r/gi_DckOUM5a.png")',
        backgroundRepeat: 'repeat',
        backgroundSize: '400px',
        backgroundColor: '#efeae2',
        backgroundBlendMode: 'overlay',
        opacity: 0.95
      }}
    >
      <div className="flex flex-col gap-[2px] sm:gap-1 w-full mx-auto">
        {isLoadingMore && (
          <div className="flex justify-center py-3">
            <div className="text-[#667781] text-sm flex gap-1.5 items-center">
              <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></div>
              <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></div>
              <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></div>
            </div>
          </div>
        )}
        {messages.map((msg, index) => {
          const showTail = index === 0 || messages[index - 1].sender !== msg.sender;
          return <MessageBubble key={msg.id} msg={msg} showTail={showTail} />;
        })}

        {isLoading && (
          <div className="flex justify-start mt-2">
            <div className="bg-white px-4 py-3 rounded-lg rounded-tl-none shadow-sm text-[#667781] text-sm flex gap-1.5 items-center">
              <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></div>
              <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></div>
              <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>
    </div>
  );
}
