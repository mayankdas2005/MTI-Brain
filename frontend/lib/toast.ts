import { toast as sonnerToast } from 'sonner';
import type { CSSProperties } from 'react';

type SonnerOptions = Parameters<typeof sonnerToast>[1];

const successStyle: CSSProperties = {
  background: '#16a34a',
  color: '#ffffff',
  border: '1px solid #15803d',
};

const errorStyle: CSSProperties = {
  background: '#dc2626',
  color: '#ffffff',
  border: '1px solid #b91c1c',
};

const warningStyle: CSSProperties = {
  background: '#d97706',
  color: '#ffffff',
  border: '1px solid #b45309',
};

function mergeStyle(base: CSSProperties, opts?: SonnerOptions): SonnerOptions {
  if (!opts) return { style: base };
  return { ...opts, style: { ...base, ...(opts.style ?? {}) } };
}

export const toast = {
  success: (message: string, opts?: SonnerOptions) =>
    sonnerToast.success(message, mergeStyle(successStyle, opts)),

  error: (message: string, opts?: SonnerOptions) =>
    sonnerToast.error(message, mergeStyle(errorStyle, opts)),

  warning: (message: string, opts?: SonnerOptions) =>
    sonnerToast.warning(message, mergeStyle(warningStyle, opts)),

  info: (message: string, opts?: SonnerOptions) =>
    sonnerToast.info(message, opts),

  default: (message: string, opts?: SonnerOptions) =>
    sonnerToast(message, opts),
};
