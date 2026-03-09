import React from 'react';
import { CheckCheck } from 'lucide-react';
import { Message } from '../types';

interface MessageBubbleProps {
  msg: Message;
  showTail: boolean;
  key?: React.Key;
}

export default function MessageBubble({ msg, showTail }: MessageBubbleProps) {
  const isUser = msg.sender === 'user';

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} ${showTail ? 'mt-2' : ''}`}>
      <div 
        className={`relative max-w-[85%] sm:max-w-[75%] md:max-w-[65%] px-2 sm:px-2.5 py-1 sm:py-1.5 rounded-lg shadow-[0_1px_1px_rgba(11,20,26,0.1)] text-[14.5px] sm:text-[15px] leading-relaxed ${
          isUser 
            ? 'bg-[#d9fdd3] text-[#111b21] rounded-tr-none' 
            : 'bg-white text-[#111b21] rounded-tl-none'
        }`}
      >
        {/* Tail SVG */}
        {showTail && (
          <div className={`absolute top-0 w-4 h-4 ${isUser ? '-right-2 text-[#d9fdd3]' : '-left-2 text-white'}`}>
            <svg viewBox="0 0 8 13" width="8" height="13" className="fill-current">
              {isUser ? (
                <path d="M5.188 1H0v11.193l6.467-8.625C7.526 2.156 6.958 1 5.188 1z" />
              ) : (
                <path d="M1.533 3.568L8 12.193V1H2.812C1.042 1 .474 2.156 1.533 3.568z" />
              )}
            </svg>
          </div>
        )}
        
        <div className="relative">
          <span className="whitespace-pre-wrap break-words">
            {msg.text}
            <span className="inline-block w-[70px]"></span>
          </span>
          <div className="absolute bottom-[-2px] right-0 flex items-center gap-1">
            <span className="text-[11px] text-[#667781] leading-none">{msg.timestamp}</span>
            {isUser && (
              <span className={msg.status === 'read' ? 'text-[#53bdeb]' : 'text-[#667781]'}>
                <CheckCheck size={14} strokeWidth={2.5} />
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
