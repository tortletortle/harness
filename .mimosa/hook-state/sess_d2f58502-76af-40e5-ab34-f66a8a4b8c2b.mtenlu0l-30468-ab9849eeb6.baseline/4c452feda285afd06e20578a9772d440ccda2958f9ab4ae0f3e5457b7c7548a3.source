/**
 * AI 助手面板（右侧）
 */

import { useState } from 'react';
import { X, Send, Bot } from 'lucide-react';
import { useAppStore } from '../stores/appStore';
import { apiClient } from '../api/client';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export function AiPanel() {
  const { toggleAiPanel } = useAppStore();
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: 'assistant', content: '你好！我是 AI 助手，可以帮你分析任务、解答问题。有什么需要帮助的吗？' }
  ]);
  const [input, setInput] = useState('');

  const handleSend = async () => {
    if (!input.trim()) return;
    
    const userMessage = input;
    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: userMessage }]);
    
    try {
      // 调用真实 API
      const history = messages.map(m => ({ role: m.role, content: m.content }));
      const response = await apiClient.chat(userMessage, history);
      
      setMessages(prev => [...prev, { 
        role: 'assistant', 
        content: response 
      }]);
    } catch (error) {
      setMessages(prev => [...prev, { 
        role: 'assistant', 
        content: `抱歉，出错了: ${error}` 
      }]);
    }
  };

  return (
    <div className="w-80 bg-zinc-900 border-l border-zinc-800 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
        <div className="flex items-center gap-2">
          <Bot className="w-4 h-4 text-blue-400" />
          <span className="text-sm font-medium text-white">AI 助手</span>
        </div>
        <button
          onClick={toggleAiPanel}
          className="text-zinc-400 hover:text-white transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                msg.role === 'user'
                  ? 'bg-blue-600 text-white'
                  : 'bg-zinc-800 text-zinc-300'
              }`}
            >
              {msg.content}
            </div>
          </div>
        ))}
      </div>

      {/* Input */}
      <div className="p-3 border-t border-zinc-800">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
            placeholder="输入问题..."
            className="flex-1 bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-white placeholder-zinc-600 focus:outline-none focus:border-blue-600"
          />
          <button
            onClick={handleSend}
            disabled={!input.trim()}
            className="px-3 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-zinc-800 disabled:text-zinc-600 text-white rounded-lg transition-colors"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
