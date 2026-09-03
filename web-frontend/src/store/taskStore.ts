import { create } from 'zustand';
import type { TaskResponse } from '../types/task';

interface TaskStoreState {
  activeTaskId: number | null;
  setActiveTask: (id: number | null) => void;
  completedTasks: TaskResponse[];
  addCompletedTask: (task: TaskResponse) => void;
  clearActiveTask: () => void;
  clearCompletedTasks: () => void;
}

export const useTaskStore = create<TaskStoreState>((set) => ({
  activeTaskId: null,
  setActiveTask: (id) => set({ activeTaskId: id }),
  completedTasks: [],
  addCompletedTask: (task) => set((state) => ({
    completedTasks: [task, ...state.completedTasks].slice(0, 20),
  })),
  clearActiveTask: () => set({ activeTaskId: null }),
  clearCompletedTasks: () => set({ completedTasks: [] }),
}));
