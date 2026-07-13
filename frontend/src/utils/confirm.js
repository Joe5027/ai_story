import { nextTick, reactive } from 'vue';

const defaultState = {
  visible: false,
  title: '确认操作',
  message: '',
  confirmText: '确认',
  cancelText: '取消',
  tone: 'primary',
  closeOnBackdrop: true,
  showCancel: true,
};

const state = reactive({ ...defaultState });

const queue = [];
let activeRequest = null;

function showNext() {
  if (activeRequest || queue.length === 0) {
    return;
  }

  activeRequest = queue.shift();
  Object.assign(state, activeRequest.config, { visible: true });
}

function finish(result) {
  if (!activeRequest) {
    return;
  }

  const { resolve, confirmValue, cancelValue } = activeRequest;
  activeRequest = null;
  Object.assign(state, { ...defaultState });
  resolve(result ? confirmValue : cancelValue);

  nextTick(() => {
    showNext();
  });
}

function normalizeConfig(message, title = '确认', options = {}) {
  if (typeof message === 'object' && message !== null) {
    return {
      ...defaultState,
      ...message,
      visible: false,
    };
  }

  if (typeof title === 'object' && title !== null) {
    options = title;
    title = options.title || '确认';
  }

  return {
    ...defaultState,
    ...options,
    title,
    message,
    visible: false,
  };
}

function open(message, title = '确认', options = {}) {
  const config = normalizeConfig(message, title, options);

  return new Promise((resolve) => {
    queue.push({ config, resolve, confirmValue: true, cancelValue: false });
    showNext();
  });
}

function alert(message, title = '提示', options = {}) {
  const config = normalizeConfig(message, title, {
    confirmText: '知道了',
    showCancel: false,
    tone: 'info',
    ...options,
  });

  return new Promise((resolve) => {
    queue.push({ config, resolve, confirmValue: true, cancelValue: true });
    showNext();
  });
}

export default {
  state,
  open,
  alert,
  confirm() {
    finish(true);
  },
  cancel() {
    finish(false);
  },
};
