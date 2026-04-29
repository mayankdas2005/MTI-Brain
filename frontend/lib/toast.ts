import { toast as sonnerToast } from 'sonner';

const successStyle = {
  background: '#16a34a',
  color: '#ffffff',
  border: '1px solid #15803d',
};

const errorStyle = {
  background: '#dc2626',
  color: '#ffffff',
  border: '1px solid #b91c1c',
};

const warningStyle = {
  background: '#d97706',
  color: '#ffffff',
  border: '1px solid #b45309',
};

export const toast = {
  success: (message: string) =>
    sonnerToast.success(message, { style: successStyle }),

  error: (message: string) =>
    sonnerToast.error(message, { style: errorStyle }),

  warning: (message: string) =>
    sonnerToast.warning(message, { style: warningStyle }),

  info: (message: string) =>
    sonnerToast.info(message),

  default: (message: string) =>
    sonnerToast(message),
};
