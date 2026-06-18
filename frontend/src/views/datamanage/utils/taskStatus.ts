export type TaskStatus =
  | 'pending'
  | 'running'
  | 'stopping'
  | 'stopped'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'skipped'
  | 'interrupted'
  | 'cooling_down'

type StatusTheme = 'default' | 'primary' | 'warning' | 'success' | 'danger' | 'info'

const TASK_STATUS_META: Record<TaskStatus, { text: string; theme: StatusTheme }> = {
  pending: { text: '等待中', theme: 'default' },
  running: { text: '执行中', theme: 'primary' },
  stopping: { text: '停止中', theme: 'warning' },
  stopped: { text: '已停止', theme: 'default' },
  completed: { text: '已完成', theme: 'success' },
  failed: { text: '失败', theme: 'danger' },
  cancelled: { text: '已取消', theme: 'default' },
  skipped: { text: '已跳过', theme: 'default' },
  interrupted: { text: '已中断', theme: 'danger' },
  cooling_down: { text: '冷却中', theme: 'info' },
}

export const getTaskStatusText = (status: string): string =>
  TASK_STATUS_META[status as TaskStatus]?.text ?? status

export const getTaskStatusTheme = (status: string): StatusTheme =>
  TASK_STATUS_META[status as TaskStatus]?.theme ?? 'default'
