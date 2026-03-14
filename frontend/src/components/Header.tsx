import React from 'react';
import { Search } from 'lucide-react';

export default function Header() {
  return (
    <header className="flex items-center justify-between px-4 py-2 bg-[#f0f2f5] border-b border-gray-300 z-10">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-full bg-gray-300 overflow-hidden">
          <img src="/icon.jpg" alt="yo-bot" className="w-full h-full object-cover" />
        </div>
        <h1 className="text-[#111b21] font-medium text-base">yo-bot</h1>
      </div>
      <div className="flex items-center gap-4 text-[#54656f]">
        <button className="p-2 hover:bg-gray-200 rounded-full transition-colors">
          <Search size={20} />
        </button>
      </div>
    </header>
  );
}
