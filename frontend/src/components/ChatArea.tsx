import React, { useEffect, useRef } from 'react';
import { Message } from '../types';
import MessageBubble from './MessageBubble';

interface ChatAreaProps {
  messages: Message[];
  isLoading: boolean;
}

export default function ChatArea({ messages, isLoading }: ChatAreaProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  return (
    <div 
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
