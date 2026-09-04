import { useCallback, useEffect, useRef, useState } from 'react';
import { Bot, Loader2, Send, X, Trash2, Sparkles } from 'lucide-react';
import { agentChat, type ChatHistoryItem, type ChatToolCall } from '../api/agent';

interface ChatPanelProps {
  open: boolean;
  onClose: () => void;
  /** 当前画布项目 id，传给 agent 让它感知上下文；未保存项目时为 null */
  canvasId: number | null;
  /** 画布上是否已经保存过项目（用于提示用户） */
  hasProject: boolean;
  dark: boolean;
}

interface ChatMsg {
  role: 'user' | 'assistant';
  content: string;
  toolCalls?: ChatToolCall[];
  error?: boolean;
}

/** 工具调用轨迹的可读标签 */
function toolCallLabel(tc: ChatToolCall): string {
  if (tc.status === 'error') return `${tc.tool_name} ✗ 失败`;
  return `${tc.tool_name} ✓`;
}

/** 剥离 markdown 特殊符号（agent 回复不需要 markdown 渲染，纯文本展示更清爽） */
function stripMarkdown(text: string): string {
  if (!text) return text;
  return text
    // 代码块：保留内容，去掉围栏
    .replace(/```[\s\S]*?```/g, (m) => m.replace(/```[\s\n]*[\w]*[\s\n]*[\s\S]*?```/g, ''))
    // 行内代码
    .replace(/`([^`]+)`/g, '$1')
    // 图片 ![alt](url)
    .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '$1')
    // 链接 [text](url)
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1')
    // 标题 # ## ###
    .replace(/^#{1,6}\s+/gm, '')
    // 粗体/斜体
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/_([^_]+)_/g, '$1')
    // 引用
    .replace(/^>\s?/gm, '')
    // 列表符号
    .replace(/^\s*[-*+]\s+/gm, '• ')
    .replace(/^\s*\d+\.\s+/gm, (m) => m)
    // 分隔线
    .replace(/^[-*_]{3,}\s*$/gm, '')
    // 表格管道
    .replace(/\|/g, ' | ')
    // 多余空行
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

const QUICK_PROMPTS = [
  '描述当前画布的结构',
  '把第 3 个视频节点的提示词改得更动态',
  '查看最近失败的任务',
];

export default function ChatPanel({ open, onClose, canvasId, hasProject, dark }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  // 打开面板时如果空，给一条提示
  useEffect(() => {
    if (open && messages.length === 0) {
      setMessages([
        {
          role: 'assistant',
          content: hasProject && canvasId
            ? `你好，我看到你在画布「项目 #${canvasId}」。告诉我你想调整什么，比如「把第 3 个视频节点的提示词改得更动态」或「查看最近失败的任务」。`
            : '你好，我是画布助手。当前画布尚未保存到服务端——保存后我能直接读取和修改节点。你可以先问「我该怎么用这个功能」或直接描述需求。',
        },
      ]);
    }
  }, [open, canvasId, hasProject, messages.length]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  const history: ChatHistoryItem[] = messages
    .filter((m) => !m.error)
    .map((m) => ({ role: m.role, content: m.content }));

  const send = useCallback(async (text: string) => {
    const msg = text.trim();
    if (!msg || loading) return;
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: msg }]);
    setLoading(true);
    try {
      const res = await agentChat(canvasId, msg, history);
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: res.reply, toolCalls: res.tool_calls },
      ]);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `调用失败：${message}`, error: true },
      ]);
    } finally {
      setLoading(false);
    }
  }, [canvasId, loading, messages]);

  const clear = () => setMessages([]);

  if (!open) return null;

  const isDark = dark;
  return (
    <aside
      className={`absolute right-0 top-0 bottom-0 z-30 flex w-96 flex-col border-l shadow-2xl backdrop-blur ${
        isDark ? 'border-slate-700 bg-slate-900/95 text-slate-100' : 'border-slate-300 bg-white/95 text-slate-800'
      }`}
    >
      {/* 头部 */}
      <header
        className={`flex items-center gap-2 border-b px-3 py-2 ${
          isDark ? 'border-slate-700' : 'border-slate-200'
        }`}
      >
        <Bot className="h-4 w-4 text-indigo-400" />
        <div className="flex-1">
          <div className="text-xs font-semibold">画布助手</div>
          <div className={`text-[10px] ${isDark ? 'text-slate-400' : 'text-slate-500'}`}>
            {canvasId ? `项目 #${canvasId}` : '未保存项目'} · agnes-2.5-flash
          </div>
        </div>
        <button
          onClick={clear}
          title="清空对话"
          className={`rounded-lg border px-2 py-1 text-[11px] ${
            isDark ? 'border-slate-700 hover:bg-slate-800' : 'border-slate-300 hover:bg-slate-50'
          }`}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
        <button
          onClick={onClose}
          title="关闭"
          className="rounded-lg border border-slate-300 p-1 hover:bg-slate-50"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      {/* 消息区 */}
      <div className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {messages.map((m, i) => (
          <div key={i} className={m.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
            <div
              className={`max-w-[85%] whitespace-pre-wrap break-words rounded-xl px-3 py-2 text-xs leading-relaxed ${
                m.role === 'user'
                  ? 'bg-indigo-600 text-white'
                  : m.error
                  ? isDark
                    ? 'border border-red-500/50 bg-red-500/10 text-red-300'
                    : 'border border-red-300 bg-red-50 text-red-600'
                  : isDark
                  ? 'border border-slate-700 bg-slate-800'
                  : 'border border-slate-200 bg-slate-50'
              }`}
            >
              {m.role === 'assistant' ? stripMarkdown(m.content) : m.content}
              {m.toolCalls && m.toolCalls.length > 0 && (
                <div
                  className={`mt-2 border-t pt-1.5 text-[10px] ${
                    isDark ? 'border-slate-700 text-slate-400' : 'border-slate-200 text-slate-500'
                  }`}
                >
                  <div className="mb-0.5 flex items-center gap-1 font-medium">
                    <Sparkles className="h-3 w-3" /> 工具调用：
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {m.toolCalls.map((tc, j) => (
                      <span
                        key={j}
                        title={`${tc.tool_name}\n${JSON.stringify(tc.args, null, 2)}`}
                        className={`rounded px-1.5 py-0.5 ${
                          tc.status === 'error'
                            ? 'bg-red-500/20 text-red-400'
                            : isDark
                            ? 'bg-indigo-500/20 text-indigo-300'
                            : 'bg-indigo-100 text-indigo-600'
                        }`}
                      >
                        {toolCallLabel(tc)}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> agent 思考中...
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* 快捷提示 */}
      {messages.length <= 1 && (
        <div className="border-t border-slate-700/50 px-3 py-2">
          <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-slate-500">试试这些</div>
          <div className="flex flex-wrap gap-1.5">
            {QUICK_PROMPTS.map((q) => (
              <button
                key={q}
                onClick={() => send(q)}
                className={`rounded-full border px-2 py-1 text-[11px] ${
                  isDark ? 'border-slate-700 hover:bg-slate-800' : 'border-slate-300 hover:bg-slate-50'
                }`}
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 输入 */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className={`border-t px-3 py-2.5 ${
          isDark ? 'border-slate-700' : 'border-slate-200'
        }`}
      >
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            placeholder="描述你想让 agent 做什么…（Enter 发送，Shift+Enter 换行）"
            rows={2}
            className={`flex-1 resize-none rounded-lg border px-2.5 py-1.5 text-xs outline-none focus:ring-1 focus:ring-indigo-500 ${
              isDark
                ? 'border-slate-700 bg-slate-800 placeholder:text-slate-500'
                : 'border-slate-300 bg-white placeholder:text-slate-400'
            }`}
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="rounded-lg bg-indigo-600 p-2 text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
            title="发送"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </form>
    </aside>
  );
}
