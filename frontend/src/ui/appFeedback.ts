import { Modal as staticModal, message as staticMessage } from 'antd';
import type { MessageInstance } from 'antd/es/message/interface';
import type { ModalStaticFunctions } from 'antd/es/modal/confirm';

type AppModalApi = Omit<ModalStaticFunctions, 'warn'>;

interface FeedbackInstances {
  message: MessageInstance;
  modal: AppModalApi;
}

let current: FeedbackInstances | null = null;

export function registerAppFeedback(next: FeedbackInstances): () => void {
  current = next;
  return () => {
    if (current === next) current = null;
  };
}

export const appMessage: Pick<
  MessageInstance,
  'info' | 'success' | 'error' | 'warning' | 'loading' | 'open' | 'destroy'
> = {
  info: (...args) => (current ? current.message.info(...args) : staticMessage.info(...args)),
  success: (...args) => (current ? current.message.success(...args) : staticMessage.success(...args)),
  error: (...args) => (current ? current.message.error(...args) : staticMessage.error(...args)),
  warning: (...args) => (current ? current.message.warning(...args) : staticMessage.warning(...args)),
  loading: (...args) => (current ? current.message.loading(...args) : staticMessage.loading(...args)),
  open: (...args) => (current ? current.message.open(...args) : staticMessage.open(...args)),
  destroy: (...args) => (current ? current.message.destroy(...args) : staticMessage.destroy(...args)),
};

export const appModal: Pick<AppModalApi, 'info' | 'success' | 'error' | 'warning' | 'confirm'> = {
  info: (...args) => (current ? current.modal.info(...args) : staticModal.info(...args)),
  success: (...args) => (current ? current.modal.success(...args) : staticModal.success(...args)),
  error: (...args) => (current ? current.modal.error(...args) : staticModal.error(...args)),
  warning: (...args) => (current ? current.modal.warning(...args) : staticModal.warning(...args)),
  confirm: (...args) => (current ? current.modal.confirm(...args) : staticModal.confirm(...args)),
};
