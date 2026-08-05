import { useContext } from 'react';

import { ToastContext } from '../contexts/ToastContext';
import type { ToastContextValue } from '../contexts/ToastContext';

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) throw new Error('useToast must be used inside <ToastProvider>');
  return context;
}
