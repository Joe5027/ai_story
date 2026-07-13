<template>
  <transition name="confirm-dialog-fade">
    <div
      v-if="state.visible"
      class="confirm-dialog-overlay"
      @click="handleBackdrop"
    >
      <div
        class="confirm-dialog"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        :aria-describedby="contentId"
        @click.stop
      >
        <div
          class="confirm-dialog__accent"
          :class="`confirm-dialog__accent--${state.tone}`"
        />
        <div class="confirm-dialog__body">
          <div class="confirm-dialog__header">
            <div
              class="confirm-dialog__icon"
              :class="`confirm-dialog__icon--${state.tone}`"
              aria-hidden="true"
            >
              <svg
                v-if="state.tone === 'success'"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  d="M9 12.75l2.25 2.25L15 9.75m6 2.25a9 9 0 11-18 0 9 9 0 0118 0z"
                />
              </svg>
              <svg
                v-else-if="state.tone === 'info' || state.tone === 'primary'"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  d="M11.25 11.25l.041-.02a.75.75 0 011.09.664v4.856a.75.75 0 001.5 0v-4.856a2.25 2.25 0 00-3.27-1.992l-.041.02a.75.75 0 00.68 1.336zM12 7.5h.008v.008H12V7.5z"
                />
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                />
              </svg>
              <svg
                v-else
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  d="M12 9v4m0 4h.01M10.29 3.86l-7.5 13A1 1 0 003.65 18h16.7a1 1 0 00.86-1.5l-7.5-13a1 1 0 00-1.72 0z"
                />
              </svg>
            </div>
            <div class="confirm-dialog__copy">
              <h3
                :id="titleId"
                class="confirm-dialog__title"
              >
                {{ state.title }}
              </h3>
              <p
                :id="contentId"
                class="confirm-dialog__message"
              >
                {{ state.message }}
              </p>
            </div>
          </div>

          <div class="confirm-dialog__actions">
            <button
              v-if="state.showCancel"
              type="button"
              class="confirm-dialog__button confirm-dialog__button--secondary"
              @click="handleCancel"
            >
              {{ state.cancelText }}
            </button>
            <button
              ref="confirmButton"
              type="button"
              class="confirm-dialog__button"
              :class="`confirm-dialog__button--${state.tone}`"
              @click="handleConfirm"
            >
              {{ state.confirmText }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </transition>
</template>

<script>
import confirmService from '@/utils/confirm';

export default {
  name: 'ConfirmDialog',
  data() {
    return {
      state: confirmService.state,
      titleId: 'global-confirm-dialog-title',
      contentId: 'global-confirm-dialog-content',
    };
  },
  watch: {
    'state.visible'(visible) {
      if (visible) {
        this.$nextTick(() => {
          this.$refs.confirmButton?.focus();
        });
      }
    },
  },
  mounted() {
    document.addEventListener('keydown', this.handleKeydown);
  },
  beforeUnmount() {
    document.removeEventListener('keydown', this.handleKeydown);
  },
  methods: {
    handleConfirm() {
      confirmService.confirm();
    },
    handleCancel() {
      confirmService.cancel();
    },
    handleBackdrop(event) {
      if (event.target === event.currentTarget && this.state.closeOnBackdrop) {
        this.handleCancel();
      }
    },
    handleKeydown(event) {
      if (!this.state.visible) {
        return;
      }

      if (event.key === 'Escape') {
        event.preventDefault();
        this.handleCancel();
      }
    },
  },
};
</script>

<style scoped>
.confirm-dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.5rem;
  background: rgba(15, 23, 42, 0.38);
  backdrop-filter: blur(10px);
}

.confirm-dialog {
  width: min(100%, 30rem);
  border-radius: 20px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(148, 163, 184, 0.18);
  box-shadow: 0 24px 70px rgba(15, 23, 42, 0.2);
}

.confirm-dialog__accent {
  height: 3px;
  background: linear-gradient(90deg, #38bdf8, #14b8a6);
}

.confirm-dialog__accent--primary,
.confirm-dialog__accent--info {
  background: linear-gradient(90deg, #38bdf8, #14b8a6);
}

.confirm-dialog__accent--success {
  background: linear-gradient(90deg, #34d399, #10b981);
}

.confirm-dialog__accent--warning {
  background: linear-gradient(90deg, #f59e0b, #f97316);
}

.confirm-dialog__accent--error,
.confirm-dialog__accent--danger {
  background: linear-gradient(90deg, #fb7185, #ef4444);
}

.confirm-dialog__body {
  padding: 1.5rem;
}

.confirm-dialog__header {
  display: flex;
  align-items: flex-start;
  gap: 1rem;
}

.confirm-dialog__icon {
  flex-shrink: 0;
  width: 3rem;
  height: 3rem;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 16px;
}

.confirm-dialog__icon svg {
  width: 1.35rem;
  height: 1.35rem;
}

.confirm-dialog__icon--primary,
.confirm-dialog__icon--info {
  background: rgba(20, 184, 166, 0.12);
  color: #0f766e;
}

.confirm-dialog__icon--success {
  background: rgba(16, 185, 129, 0.14);
  color: #059669;
}

.confirm-dialog__icon--warning {
  background: rgba(245, 158, 11, 0.16);
  color: #d97706;
}

.confirm-dialog__icon--error,
.confirm-dialog__icon--danger {
  background: rgba(248, 113, 113, 0.14);
  color: #dc2626;
}

.confirm-dialog__copy {
  flex: 1;
}

.confirm-dialog__title {
  font-size: 1.1rem;
  font-weight: 600;
  color: #0f172a;
}

.confirm-dialog__message {
  margin-top: 0.4rem;
  color: #475569;
  line-height: 1.65;
  white-space: pre-line;
}

.confirm-dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
  margin-top: 1.5rem;
}

.confirm-dialog__button {
  min-width: 5.75rem;
  padding: 0.72rem 1.1rem;
  border: 1px solid transparent;
  border-radius: 999px;
  font-size: 0.95rem;
  font-weight: 600;
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease, background-color 0.2s ease, color 0.2s ease;
}

.confirm-dialog__button:hover {
  transform: translateY(-1px);
}

.confirm-dialog__button:focus-visible {
  outline: none;
  box-shadow: 0 0 0 4px rgba(56, 189, 248, 0.18);
}

.confirm-dialog__button--secondary {
  border-color: rgba(148, 163, 184, 0.26);
  background: rgba(255, 255, 255, 0.86);
  color: #334155;
}

.confirm-dialog__button--secondary:hover {
  border-color: rgba(20, 184, 166, 0.35);
  box-shadow: 0 10px 24px rgba(20, 184, 166, 0.08);
}

.confirm-dialog__button--primary,
.confirm-dialog__button--info {
  background: linear-gradient(135deg, #22d3ee, #14b8a6);
  color: #f8fafc;
  box-shadow: 0 14px 30px rgba(20, 184, 166, 0.22);
}

.confirm-dialog__button--success {
  background: linear-gradient(135deg, #34d399, #10b981);
  color: #ecfdf5;
  box-shadow: 0 14px 30px rgba(16, 185, 129, 0.22);
}

.confirm-dialog__button--warning {
  background: linear-gradient(135deg, #f59e0b, #f97316);
  color: #fff7ed;
  box-shadow: 0 14px 30px rgba(249, 115, 22, 0.22);
}

.confirm-dialog__button--error,
.confirm-dialog__button--danger {
  background: linear-gradient(135deg, #fb7185, #ef4444);
  color: #fff1f2;
  box-shadow: 0 14px 30px rgba(239, 68, 68, 0.22);
}

.confirm-dialog-fade-enter-active,
.confirm-dialog-fade-leave-active {
  transition: opacity 0.2s ease;
}

.confirm-dialog-fade-enter,
.confirm-dialog-fade-leave-to {
  opacity: 0;
}

[data-theme='dark'] .confirm-dialog {
  background: rgba(15, 23, 42, 0.95);
  border-color: rgba(148, 163, 184, 0.16);
  box-shadow: 0 28px 70px rgba(2, 6, 23, 0.5);
}

[data-theme='dark'] .confirm-dialog__title {
  color: #e2e8f0;
}

[data-theme='dark'] .confirm-dialog__message {
  color: #94a3b8;
}

[data-theme='dark'] .confirm-dialog__button--secondary {
  background: rgba(15, 23, 42, 0.85);
  color: #e2e8f0;
  border-color: rgba(148, 163, 184, 0.24);
}

[data-theme='dark'] .confirm-dialog__button--secondary:hover {
  border-color: rgba(56, 189, 248, 0.35);
  box-shadow: 0 12px 26px rgba(15, 23, 42, 0.3);
}

@media (max-width: 640px) {
  .confirm-dialog-overlay {
    padding: 1rem;
    align-items: flex-end;
  }

  .confirm-dialog {
    width: 100%;
    border-bottom-left-radius: 16px;
    border-bottom-right-radius: 16px;
  }

  .confirm-dialog__header {
    gap: 0.75rem;
  }

  .confirm-dialog__actions {
    flex-direction: column-reverse;
  }

  .confirm-dialog__button {
    width: 100%;
  }
}
</style>
