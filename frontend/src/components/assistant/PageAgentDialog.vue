<template>
  <transition name="page-agent-dialog">
    <section
      v-if="visible"
      class="page-agent-dialog"
      @click.stop
    >
      <div class="dialog-panel">
        <div class="dialog-header">
          <div class="dialog-header-main">
            <div class="dialog-eyebrow">
              页面助手
            </div>
            <h3 class="dialog-title">
              {{ contextTitle }}
            </h3>
            <p class="dialog-subtitle">
              {{ contextSubtitle }}
            </p>
          </div>
          <div class="dialog-header-actions">
            <div
              v-if="models.length"
              class="model-switcher"
            >
              <button
                class="dialog-icon-action"
                type="button"
                :disabled="streaming"
                :title="currentModelLabel"
                @click="toggleModelMenu"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  xmlns="http://www.w3.org/2000/svg"
                  aria-hidden="true"
                >
                  <path
                    d="M12 4L19 8L12 12L5 8L12 4Z"
                    stroke="currentColor"
                    stroke-width="1.5"
                    stroke-linejoin="round"
                  />
                  <path
                    d="M5 12L12 16L19 12"
                    stroke="currentColor"
                    stroke-width="1.5"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                  <path
                    d="M5 16L12 20L19 16"
                    stroke="currentColor"
                    stroke-width="1.5"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                </svg>
              </button>
              <div
                v-if="showModelMenu"
                class="model-menu"
              >
                <button
                  v-for="item in models"
                  :key="item.id"
                  :class="['model-menu-item', `status-${item.runtime_status || 'unknown'}`, { 'is-active': item.id === selectedModelProviderId, 'is-disabled': !item.runtime_available }]"
                  type="button"
                  :disabled="!item.runtime_available"
                  :title="getModelTooltip(item)"
                  @click="handleSelectModel(item.id)"
                >
                  <span class="model-menu-title">{{ item.name }} / {{ item.model_name }}</span>
                  <small
                    v-if="getModelStatusLabel(item)"
                    class="model-menu-status"
                  >{{ getModelStatusLabel(item) }}</small>
                </button>
              </div>
            </div>
            <button
              v-if="showClearSession"
              class="dialog-ghost-action"
              type="button"
              :disabled="streaming"
              @click="$emit('clear-session')"
            >
              清空会话
            </button>
            <button
              class="dialog-close"
              type="button"
              @click="$emit('close')"
            >
              ×
            </button>
          </div>
        </div>

        <div
          v-if="showQuickActions"
          class="quick-actions"
        >
          <button
            v-for="item in quickActions"
            :key="item"
            class="quick-action-btn"
            type="button"
            :disabled="streaming"
            @click="$emit('quick-action', item)"
          >
            {{ item }}
          </button>
        </div>

        <div
          ref="messageList"
          class="message-list"
        >
          <div
            v-if="messages.length === 0"
            class="empty-chat"
          >
            <div class="empty-hero">
              助手已准备就绪
            </div>
            <p class="empty-hint">
              你可以让我总结当前页面、给下一步建议，或执行已开放的页面内操作。
            </p>
          </div>

          <article
            v-for="message in displayedMessages"
            :key="message.id"
            :class="['message-card', `role-${message.role}`]"
          >
            <div class="message-meta">
              <span
                v-if="message.role !== 'system'"
                class="message-role"
              >
                {{ message.role === 'user' ? '你' : '页面助手' }}
              </span>
              <span
                v-if="message.pending"
                class="message-status"
              >
                处理中
              </span>
            </div>
            <div class="message-content">
              {{ message.content }}
            </div>

            <div
              v-if="message.suggestions && message.suggestions.length"
              class="suggestion-list"
            >
              <button
                v-for="suggestion in message.suggestions"
                :key="suggestion.id"
                class="suggestion-btn"
                type="button"
                :disabled="streaming"
                @click="$emit('execute-suggestion', suggestion)"
              >
                <span>{{ suggestion.label }}</span>
                <small v-if="suggestion.description">{{ suggestion.description }}</small>
              </button>
            </div>
          </article>
        </div>

        <div
          v-if="floatingStatus"
          class="floating-status"
        >
          <span class="floating-status-dot" />
          <span class="floating-status-text">{{ floatingStatus.content }}</span>
        </div>

        <div class="composer-card">
          <textarea
            ref="composerInput"
            :value="value"
            class="composer-input"
            rows="3"
            placeholder="例如：总结当前项目进度，或者帮我定位到分镜阶段。"
            @input="$emit('input', $event.target.value)"
            @keydown.meta.enter.prevent="$emit('submit')"
            @keydown.ctrl.enter.prevent="$emit('submit')"
          />
          <div class="composer-footer">
            <span class="composer-tip">⌘/Ctrl + Enter 发送</span>
            <div class="composer-actions">
              <button
                v-if="streaming"
                class="secondary-action"
                type="button"
                @click="$emit('stop')"
              >
                停止
              </button>
              <button
                class="primary-action"
                type="button"
                :disabled="streaming || !value.trim()"
                @click="$emit('submit')"
              >
                {{ streaming ? '处理中...' : '发送' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  </transition>
</template>

<script>
export default {
  name: 'PageAgentDialog',
  props: {
    visible: {
      type: Boolean,
      default: false,
    },
    contextTitle: {
      type: String,
      default: '当前页面',
    },
    contextSubtitle: {
      type: String,
      default: '页面助手',
    },
    summary: {
      type: String,
      default: '',
    },
    quickActions: {
      type: Array,
      default: () => [],
    },
    messages: {
      type: Array,
      default: () => [],
    },
    models: {
      type: Array,
      default: () => [],
    },
    selectedModelProviderId: {
      type: String,
      default: '',
    },
    value: {
      type: String,
      default: '',
    },
    streaming: {
      type: Boolean,
      default: false,
    },
  },
  data() {
    return {
      showModelMenu: false,
    };
  },
  computed: {
    displayedMessages() {
      return this.messages.filter((message) => message.role !== 'system');
    },
    showQuickActions() {
      return !this.streaming && this.quickActions.length > 0 && this.displayedMessages.length <= 1;
    },
    showClearSession() {
      return this.displayedMessages.length > 1;
    },
    currentModelLabel() {
      const currentModel = this.models.find((item) => item.id === this.selectedModelProviderId);
      if (!currentModel) {
        return '切换助手模型';
      }
      const statusLabel = this.getModelStatusLabel(currentModel);
      return statusLabel
        ? `当前模型：${currentModel.name} / ${currentModel.model_name}（${statusLabel}）`
        : `当前模型：${currentModel.name} / ${currentModel.model_name}`;
    },
    floatingStatus() {
      for (let index = this.messages.length - 1; index >= 0; index -= 1) {
        const message = this.messages[index];
        if (message.role === 'system' && message.pending) {
          return message;
        }
      }
      return null;
    },
  },
  watch: {
    messages: {
      deep: true,
      handler() {
        this.scrollToBottom();
      },
    },
    visible(nextVisible) {
      if (nextVisible) {
        this.$nextTick(() => {
          this.scrollToBottom();
          this.$refs.composerInput?.focus();
        });
      } else {
        this.showModelMenu = false;
      }
    },
  },
  methods: {
    getModelStatusLabel(model) {
      if (model.runtime_status === 'restart_required') {
        return '需重启';
      }
      return '';
    },
    getModelTooltip(model) {
      const baseLabel = `${model.name} / ${model.model_name}`;
      const statusLabel = this.getModelStatusLabel(model);
      return statusLabel ? `${baseLabel}（${statusLabel}）` : baseLabel;
    },
    toggleModelMenu() {
      this.showModelMenu = !this.showModelMenu;
    },
    handleSelectModel(providerId) {
      this.showModelMenu = false;
      this.$emit('select-model', providerId);
    },
    scrollToBottom() {
      this.$nextTick(() => {
        const list = this.$refs.messageList;
        if (list) {
          list.scrollTop = list.scrollHeight;
        }
      });
    },
  },
};
</script>

<style scoped>
.page-agent-dialog {
  position: fixed;
  right: 24px;
  bottom: 24px;
  width: min(440px, calc(100vw - 32px));
  height: min(760px, calc(100vh - 48px));
  z-index: 430;
}

.dialog-panel {
  position: relative;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding: 0.9rem;
  border-radius: 24px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(18px);
  box-shadow: 0 28px 56px rgba(15, 23, 42, 0.18);
  overflow: hidden;
}

.dialog-panel::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, rgba(20, 184, 166, 0.95), rgba(14, 165, 233, 0.85));
}

.layout-shell.theme-dark .dialog-panel {
  background: rgba(15, 23, 42, 0.94);
  border-color: rgba(148, 163, 184, 0.2);
  box-shadow: 0 28px 56px rgba(2, 6, 23, 0.62);
}

.dialog-header,
.dialog-header-actions,
.composer-footer,
.message-meta,
.composer-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.dialog-header,
.composer-footer,
.message-meta {
  justify-content: space-between;
}

.dialog-header-main {
  min-width: 0;
}

.model-switcher {
  position: relative;
}

.dialog-icon-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid rgba(15, 23, 42, 0.12);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  color: #0f172a;
}

.dialog-icon-action svg {
  width: 16px;
  height: 16px;
}

.model-menu {
  position: absolute;
  top: calc(100% + 0.45rem);
  right: 0;
  z-index: 4;
  width: min(280px, calc(100vw - 48px));
  max-height: min(320px, calc(100vh - 180px));
  overflow-y: auto;
  padding: 0.28rem;
  border-radius: 14px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 18px 36px rgba(15, 23, 42, 0.12);
}

.model-menu::-webkit-scrollbar {
  width: 6px;
}

.model-menu::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.42);
}

.model-menu-item {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.08rem;
  padding: 0.48rem 0.56rem;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: #0f172a;
  font-size: 0.72rem;
  text-align: left;
  transition: background 0.2s ease, color 0.2s ease;
}

.model-menu-title {
  display: block;
  font-weight: 600;
  width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-menu-status {
  display: block;
  font-size: 0.64rem;
  color: #b45309;
}

.model-menu-item:hover,
.model-menu-item.is-active {
  background: rgba(20, 184, 166, 0.1);
  color: #0f766e;
}

.model-menu-item.status-ready .model-menu-status {
  color: #0f766e;
}

.model-menu-item.status-restart_required .model-menu-status {
  color: #b45309;
}

.model-menu-item.is-disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.model-menu-item.is-disabled:hover {
  background: transparent;
  color: #0f172a;
  transform: none;
  box-shadow: none;
}

.dialog-header-actions {
  flex: 0 0 auto;
}

.dialog-eyebrow {
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #0f766e;
}

.dialog-title {
  margin: 0.18rem 0 0;
  font-size: 1.05rem;
  font-weight: 700;
  color: #0f172a;
}

.dialog-subtitle,
.summary-title,
.composer-tip,
.message-status,
.suggestion-btn small {
  font-size: 0.74rem;
  color: #64748b;
}

.dialog-subtitle,
.summary-text {
  margin: 0.18rem 0 0;
}

.layout-shell.theme-dark .dialog-title {
  color: #e2e8f0;
}

.dialog-close,
.dialog-ghost-action,
.quick-action-btn,
.suggestion-btn,
.primary-action,
.secondary-action {
  transition: all 0.2s ease;
}

.dialog-close {
  width: 36px;
  height: 36px;
  border: 1px solid rgba(15, 23, 42, 0.12);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  color: #0f172a;
  font-size: 1.2rem;
  line-height: 1;
}

.dialog-ghost-action {
  padding: 0.46rem 0.78rem;
  border-radius: 999px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(255, 255, 255, 0.78);
  color: #64748b;
  font-size: 0.74rem;
  font-weight: 600;
}

.summary-card,
.composer-card {
  border-radius: 18px;
  border: 1px solid rgba(148, 163, 184, 0.16);
  background: rgba(255, 255, 255, 0.72);
  box-shadow: 0 14px 30px rgba(15, 23, 42, 0.08);
}

.layout-shell.theme-dark .summary-card,
.layout-shell.theme-dark .composer-card {
  background: rgba(15, 23, 42, 0.84);
  border-color: rgba(148, 163, 184, 0.18);
  box-shadow: 0 14px 30px rgba(2, 6, 23, 0.45);
}

.summary-card {
  padding: 0.75rem 0.85rem;
}

.summary-title {
  font-weight: 700;
}

.summary-text {
  font-size: 0.84rem;
  line-height: 1.6;
  color: #334155;
}

.layout-shell.theme-dark .summary-text,
.layout-shell.theme-dark .message-content {
  color: #e2e8f0;
}

.quick-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.quick-action-btn,
.primary-action,
.secondary-action {
  padding: 0.6rem 1rem;
  border-radius: 999px;
  border: 1px solid rgba(15, 23, 42, 0.12);
  background: rgba(255, 255, 255, 0.96);
  color: #0f172a;
  font-size: 0.8rem;
  font-weight: 600;
}

.quick-action-btn:hover,
.dialog-close:hover,
.suggestion-btn:hover,
.primary-action:hover,
.secondary-action:hover {
  border-color: rgba(20, 184, 166, 0.5);
  box-shadow: 0 12px 24px rgba(20, 184, 166, 0.16);
  transform: translateY(-1px);
}

.layout-shell.theme-dark .dialog-close,
.layout-shell.theme-dark .dialog-ghost-action,
.layout-shell.theme-dark .dialog-icon-action,
.layout-shell.theme-dark .quick-action-btn,
.layout-shell.theme-dark .primary-action,
.layout-shell.theme-dark .secondary-action,
.layout-shell.theme-dark .suggestion-btn {
  background: rgba(15, 23, 42, 0.96);
  color: #e2e8f0;
  border-color: rgba(148, 163, 184, 0.2);
}

.layout-shell.theme-dark .model-menu {
  background: rgba(15, 23, 42, 0.96);
  border-color: rgba(148, 163, 184, 0.18);
  box-shadow: 0 18px 36px rgba(2, 6, 23, 0.5);
}

.layout-shell.theme-dark .model-menu-item {
  color: #e2e8f0;
}

.layout-shell.theme-dark .model-menu-item:hover,
.layout-shell.theme-dark .model-menu-item.is-active {
  background: rgba(20, 184, 166, 0.16);
  color: #5eead4;
}

.layout-shell.theme-dark .model-menu-status {
  color: #fbbf24;
}

.layout-shell.theme-dark .model-menu-item.status-restart_required .model-menu-status {
  color: #fbbf24;
}

.layout-shell.theme-dark .model-menu-item.is-disabled:hover {
  color: #e2e8f0;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  padding-right: 0.1rem;
}

.floating-status {
  align-self: center;
  max-width: calc(100% - 3rem);
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.16rem 0.55rem;
  margin: -0.1rem auto 0.05rem;
  border-radius: 999px;
  background: rgba(248, 250, 252, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.14);
  backdrop-filter: blur(12px);
  pointer-events: none;
}

.layout-shell.theme-dark .floating-status {
  background: rgba(15, 23, 42, 0.72);
  border-color: rgba(148, 163, 184, 0.14);
}

.floating-status-dot {
  width: 0.38rem;
  height: 0.38rem;
  flex: 0 0 auto;
  border-radius: 999px;
  background: linear-gradient(135deg, #14b8a6, #0ea5e9);
  opacity: 0.9;
  animation: floating-status-pulse 1.2s ease-in-out infinite;
}

.floating-status-text {
  display: block;
  font-size: 0.68rem;
  line-height: 1.2;
  color: #64748b;
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.layout-shell.theme-dark .floating-status-text {
  color: #94a3b8;
}

.empty-chat {
  padding: 1rem 0.85rem;
  border-radius: 18px;
  border: 1px dashed rgba(148, 163, 184, 0.28);
  background: rgba(255, 255, 255, 0.48);
  text-align: center;
}

.layout-shell.theme-dark .empty-chat {
  background: rgba(15, 23, 42, 0.58);
}

.empty-hero {
  font-size: 1rem;
  font-weight: 700;
  color: #0f172a;
}

.empty-hint {
  margin: 0.35rem 0 0;
  font-size: 0.82rem;
  line-height: 1.6;
  color: #64748b;
}

.layout-shell.theme-dark .empty-hero {
  color: #f8fafc;
}

.message-card {
  max-width: 92%;
  padding: 0.68rem 0.8rem;
  border-radius: 18px;
  border: 1px solid rgba(148, 163, 184, 0.12);
  box-shadow: 0 10px 20px rgba(15, 23, 42, 0.08);
}

.message-card.role-user {
  align-self: flex-end;
  background: linear-gradient(135deg, rgba(20, 184, 166, 0.16), rgba(14, 165, 233, 0.14));
}

.message-card.role-system {
  align-self: center;
  max-width: 78%;
  padding: 0.26rem 0.6rem;
  border-radius: 999px;
  border: 0;
  background: rgba(148, 163, 184, 0.1);
  box-shadow: none;
}

.message-card.role-assistant {
  align-self: flex-start;
  background: rgba(255, 255, 255, 0.84);
}

.layout-shell.theme-dark .message-card.role-user {
  background: linear-gradient(135deg, rgba(20, 184, 166, 0.2), rgba(14, 165, 233, 0.18));
}

.layout-shell.theme-dark .message-card.role-system {
  background: rgba(51, 65, 85, 0.72);
}

.layout-shell.theme-dark .message-card.role-assistant {
  background: rgba(15, 23, 42, 0.86);
}

.message-role {
  font-size: 0.74rem;
  font-weight: 700;
  color: #0f766e;
}

.message-content {
  margin-top: 0.32rem;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 0.86rem;
  line-height: 1.58;
  color: #1e293b;
}

.message-card.role-system .message-content {
  margin-top: 0;
  font-size: 0.72rem;
  line-height: 1.35;
  color: #64748b;
  text-align: center;
}

.message-card.role-system .message-meta {
  justify-content: center;
  gap: 0.35rem;
}

.message-card.role-system .message-status {
  font-size: 0.66rem;
  color: #94a3b8;
}

.suggestion-list {
  margin-top: 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}

.suggestion-btn {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.12rem;
  width: 100%;
  padding: 0.62rem 0.72rem;
  border-radius: 14px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  background: rgba(248, 250, 252, 0.92);
  color: #0f172a;
  text-align: left;
}

.composer-card {
  padding: 0.7rem 0.75rem;
}

.composer-input {
  width: 100%;
  resize: none;
  border: 0;
  background: transparent;
  color: #0f172a;
  font-size: 0.9rem;
  line-height: 1.6;
  outline: none;
}

.composer-input::placeholder {
  color: #94a3b8;
}

.layout-shell.theme-dark .composer-input {
  color: #e2e8f0;
}

.page-agent-dialog-enter-active,
.page-agent-dialog-leave-active {
  transition: all 0.22s ease;
}

.page-agent-dialog-enter,
.page-agent-dialog-leave-to {
  opacity: 0;
  transform: translateY(12px) scale(0.98);
}

@keyframes floating-status-pulse {
  0%,
  100% {
    opacity: 0.45;
    transform: scale(0.92);
  }

  50% {
    opacity: 1;
    transform: scale(1);
  }
}

@media (max-width: 768px) {
  .page-agent-dialog {
    right: 12px;
    left: 12px;
    bottom: 12px;
    width: auto;
    height: min(82vh, 760px);
  }

  .floating-status {
    max-width: calc(100% - 1.5rem);
  }
}
</style>
