import { create } from 'zustand';

interface TaskStoreState {
  activeTaskId: number | null;
  setActiveTask: (id: number | null) => void;
}

// 全局会话状态：当前正在跟踪的任务
export const useTaskStore = create<TaskStoreState>((set) => ({
  activeTaskId: null,
  setActiveTask: (id) => set({ activeTaskId: id }),
}));