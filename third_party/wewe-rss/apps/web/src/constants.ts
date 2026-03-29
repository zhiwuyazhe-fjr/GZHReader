export const statusMap = {
  0: { label: '需要重新登录', color: 'danger' },
  1: { label: '启用', color: 'success' },
  2: { label: '禁用', color: 'warning' },
} as const;

export const healthToneClassMap = {
  danger: 'is-danger',
  success: 'is-success',
  warning: 'is-warning',
} as const;
