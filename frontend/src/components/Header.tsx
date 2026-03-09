import React from 'react';
import { Search, SquarePen } from 'lucide-react';

interface HeaderProps {
  onNewChat?: () => void;
}

export default function Header({ onNewChat }: HeaderProps) {
  return (
    <header className="flex items-center justify-between px-4 py-2 bg-[#f0f2f5] border-b border-gray-300 z-10">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-full bg-gray-300 overflow-hidden">
          <img src="https://api.dicebear.com/7.x/avataaars/svg?seed=Ira" alt="Ira" className="w-full h-full object-cover" />
        </div>
        <h1 className="text-[#111b21] font-medium text-base">Ira</h1>
      </div>
      <div className="flex items-center gap-4 text-[#54656f]">
        {onNewChat && (
          <button onClick={onNewChat} className="p-2 hover:bg-gray-200 rounded-full transition-colors" title="New Chat">
            <SquarePen size={20} />
          </button>
        )}
        <button className="p-2 hover:bg-gray-200 rounded-full transition-colors">
          <Search size={20} />
        </button>
      </div>
    </header>
  );
}
