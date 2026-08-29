/**
 * 输入需求页面
 */

import { useState } from 'react';
import { Send, Loader2 } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';

export function InputView() {
  const [isLoading, setIsLoading] = useState(false);
  const { inputText, setInputText, setCurrentDesign, setCurrentView, addLog } = useAppStore();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!inputText.trim()) return;

    setIsLoading(true);
    addLog({ type: 'log', level: 'info', message: '开始生成设计书...' });

    try {
      const design = await apiClient.createDesign(inputText);
      setCurrentDesign(design);
      setCurrentView('design');
      addLog({ 
        type: 'log', 
        level: 'success', 
        message: `设计书生成完成: ${design.design_id}` 
      });
    } catch (error) {
      addLog({ 
        type: 'log', 
        level: 'error', 
        message: `生成失败: ${error}` 
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex-1 flex items-center justify-center p-4">
      <div className="w-full max-w-4xl">
        <div className="text-center mb-8">
          <h2 className="text-3xl font-bold text-white mb-2">
            描述你的项目需求
          </h2>
          <p className="text-zinc-400">
            AI 将分析需求并生成详细的设计书和执行计划
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <textarea
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            placeholder="描述你想要实现的功能和技术要求..."
            className="w-full h-96 bg-zinc-900 border border-zinc-800 rounded-lg p-4 text-white placeholder-zinc-600 focus:outline-none focus:border-blue-600 resize-none"
            disabled={isLoading}
          />

          <button
            type="submit"
            disabled={isLoading || !inputText.trim()}
            className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:bg-zinc-800 disabled:text-zinc-600 text-white font-medium py-3 rounded-lg transition-colors"
          >
            {isLoading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>生成中...</span>
              </>
            ) : (
              <>
                <Send className="w-4 h-4" />
                <span>生成设计书</span>
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
