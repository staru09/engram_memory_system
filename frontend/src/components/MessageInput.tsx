import React, { useEffect, useRef } from 'react';
import { Plus, Smile, Mic, Send } from 'lucide-react';

interface MessageInputProps {
  inputText: string;
  setInputText: (text: string) => void;
  handleSendMessage: (e?: React.FormEvent) => void;
}

export default function MessageInput({ inputText, setInputText, handleSendMessage }: MessageInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 120)}px`;
    }
  }, [inputText]);

  return (
    <footer className="bg-[#f0f2f5] px-2 sm:px-4 py-2.5 flex items-end gap-1 sm:gap-2 z-10">
      <div className="flex items-center gap-1 sm:gap-2 text-[#54656f] pb-1.5">
        <button className="p-2 hover:bg-gray-200 rounded-full transition-colors hidden sm:block">
          <Plus size={24} />
        </button>
      </div>
      
      <form 
        onSubmit={handleSendMessage} 
        className="flex-1 flex items-end bg-white rounded-xl overflow-hidden border border-transparent focus-within:border-gray-300 shadow-sm"
      >
        <button type="button" className="p-2 sm:p-3 text-[#54656f] hover:text-gray-700 transition-colors">
          <Smile size={24} />
        </button>
        <textarea
          ref={textareaRef}
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              handleSendMessage();
            }
          }}
          placeholder="Type a message"
          className="flex-1 max-h-32 py-3 px-2 outline-none resize-none text-[#111b21] bg-transparent text-[15px]"
          rows={1}
          style={{ minHeight: '44px' }}
        />
      </form>

      <div className="flex items-center text-[#54656f] pb-1">
        {inputText.trim() ? (
          <button 
            onClick={handleSendMessage}
            className="p-2.5 bg-[#00a884] text-white rounded-full hover:bg-[#008f6f] transition-colors shadow-sm"
          >
            <Send size={20} className="ml-0.5" />
          </button>
        ) : (
          <button className="p-2.5 hover:bg-gray-200 rounded-full transition-colors">
            <Mic size={24} />
          </button>
        )}
      </div>
    </footer>
  );
}
