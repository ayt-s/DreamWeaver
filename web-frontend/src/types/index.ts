import { TaskResponse } from './task';

declare global {
  interface Window {
    hermes?: {
      send: (prompt: string) => void;
    };
  }
}

export interface CreatePageProps {
  task?: TaskResponse;
  onTaskComplete?: (task: TaskResponse) => void;
}
