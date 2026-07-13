<template>
  <div class="project-stage-sse">
    <!-- 连接控制 -->
    <div class="card bg-base-100 shadow-xl mb-4">
      <div class="card-body">
        <h2 class="card-title">
          SSE 流式连接控制
        </h2>

        <div class="form-control">
          <label class="label">
            <span class="label-text">项目 ID</span>
          </label>
          <input
            v-model="projectId"
            type="text"
            placeholder="输入项目ID"
            class="input input-bordered"
            :disabled="isConnected"
          >
        </div>

        <div class="form-control">
          <label class="label">
            <span class="label-text">阶段名称</span>
          </label>
          <select
            v-model="stageName"
            class="select select-bordered"
            :disabled="isConnected"
          >
            <option value="">
              所有阶段
            </option>
            <option value="rewrite">
              剧本精修
            </option>
            <option value="storyboard">
              分镜生成
            </option>
            <option value="image_generation">
              文生图
            </option>
            <option value="camera_movement">
              运镜生成
            </option>
            <option value="video_generation">
              图生视频
            </option>
          </select>
        </div>

        <div class="form-control">
          <label class="label cursor-pointer">
            <span class="label-text">自动重连</span>
            <input
              v-model="autoReconnect"
              type="checkbox"
              class="checkbox"
              :disabled="isConnected"
            >
          </label>
        </div>

        <div class="card-actions justify-end mt-4">
          <button
            v-if="!isConnected"
            class="btn btn-primary"
            :disabled="!projectId"
            @click="connect"
          >
            连接
          </button>
          <button
            v-else
            class="btn btn-error"
            @click="disconnect"
          >
            断开连接
          </button>
          <button
            class="btn btn-ghost"
            @click="clearMessages"
          >
            清空消息
          </button>
        </div>
      </div>
    </div>

    <!-- 连接状态 -->
    <div
      class="alert mb-4"
      :class="statusAlertClass"
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        class="stroke-current shrink-0 h-6 w-6"
        fill="none"
        viewBox="0 0 24 24"
      >
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          stroke-width="2"
          d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
        />
      </svg>
      <span>{{ statusText }}</span>
    </div>

    <!-- 错误信息 -->
    <div
      v-if="error"
      class="alert alert-error mb-4"
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        class="stroke-current shrink-0 h-6 w-6"
        fill="none"
        viewBox="0 0 24 24"
      >
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          stroke-width="2"
          d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z"
        />
      </svg>
      <span>{{ error }}</span>
    </div>

    <!-- 消息列表 -->
    <div class="card bg-base-100 shadow-xl">
      <div class="card-body">
        <h2 class="card-title">
          接收到的消息
          <div class="badge badge-secondary">
            {{ messages.length }}
          </div>
        </h2>

        <div class="overflow-x-auto max-h-96 overflow-y-auto">
          <table class="table table-zebra table-pin-rows">
            <thead>
              <tr>
                <th>时间</th>
                <th>类型</th>
                <th>内容</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(msg, index) in messages"
                :key="index"
              >
                <td class="text-xs">
                  {{ msg.timestamp }}
                </td>
                <td>
                  <div
                    class="badge"
                    :class="getMessageTypeBadgeClass(msg.type)"
                  >
                    {{ msg.type }}
                  </div>
                </td>
                <td>
                  <pre class="text-xs whitespace-pre-wrap">{{ formatMessageContent(msg.data) }}</pre>
                </td>
              </tr>
            </tbody>
          </table>

          <div
            v-if="messages.length === 0"
            class="text-center py-8 text-base-content/50"
          >
            暂无消息
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { createProjectStageSSE, createProjectAllStagesSSE, SSE_EVENT_TYPES } from '@/services/sseService';

export default {
  name: 'ProjectStageSSE',
  props: {
    // 可以通过props传入项目ID和阶段名称
    initialProjectId: {
      type: String,
      default: '',
    },
    initialStageName: {
      type: String,
      default: '',
    },
  },
  data() {
    return {
      // SSE客户端
      sseClient: null,

      // 连接参数
      projectId: this.initialProjectId,
      stageName: this.initialStageName,
      autoReconnect: false,

      // 连接状态
      isConnected: false,
      error: null,

      // 消息列表
      messages: [],
    };
  },
  computed: {
    statusText() {
      if (this.isConnected) {
        return `已连接到项目 ${this.projectId}${this.stageName ? ` - ${this.stageName}` : ' (所有阶段)'}`;
      }
      return '未连接';
    },
    statusAlertClass() {
      return this.isConnected ? 'alert-success' : 'alert-info';
    },
  },
  beforeUnmount() {
    // 组件销毁时断开连接
    this.disconnect();
  },
  methods: {
    /**
     * 连接SSE
     */
    connect() {
      if (!this.projectId) {
        this.error = '请输入项目ID';
        return;
      }

      // 清空之前的消息和错误
      this.error = null;
      this.messages = [];

      // 创建SSE客户端
      if (this.stageName) {
        this.sseClient = createProjectStageSSE(this.projectId, this.stageName, {
          autoReconnect: this.autoReconnect,
        });
      } else {
        this.sseClient = createProjectAllStagesSSE(this.projectId, {
          autoReconnect: this.autoReconnect,
        });
      }

      // 监听所有事件
      this.setupEventListeners();
    },

    /**
     * 断开连接
     */
    disconnect() {
      if (this.sseClient) {
        this.sseClient.disconnect();
        this.sseClient = null;
      }
      this.isConnected = false;
    },

    /**
     * 清空消息
     */
    clearMessages() {
      this.messages = [];
    },

    /**
     * 设置事件监听器
     */
    setupEventListeners() {
      if (!this.sseClient) return;

      // 连接打开
      this.sseClient.on(SSE_EVENT_TYPES.OPEN, () => {
        this.isConnected = true;
        this.error = null;
        this.addMessage('open', { message: 'SSE连接已建立' });
      });

      // 连接关闭
      this.sseClient.on(SSE_EVENT_TYPES.CLOSE, () => {
        this.isConnected = false;
        this.addMessage('close', { message: 'SSE连接已关闭' });
      });

      // 连接错误
      this.sseClient.on(SSE_EVENT_TYPES.ERROR, (data) => {
        this.error = data.error || 'SSE连接错误';
        this.addMessage('error', data);
      });

      // 解析错误
      this.sseClient.on(SSE_EVENT_TYPES.PARSE_ERROR, (data) => {
        this.error = '消息解析失败';
        this.addMessage('parse_error', data);
      });

      // 通用消息
      this.sseClient.on(SSE_EVENT_TYPES.MESSAGE, () => {
        // 已经在特定事件中处理，这里不重复添加
      });

      // 业务事件
      this.sseClient.on(SSE_EVENT_TYPES.CONNECTED, (data) => {
        this.addMessage('connected', data);
      });

      this.sseClient.on(SSE_EVENT_TYPES.TOKEN, (data) => {
        this.addMessage('token', data);
      });

      this.sseClient.on(SSE_EVENT_TYPES.STAGE_UPDATE, (data) => {
        this.addMessage('stage_update', data);
      });

      this.sseClient.on(SSE_EVENT_TYPES.PROGRESS, (data) => {
        this.addMessage('progress', data);
      });

      this.sseClient.on(SSE_EVENT_TYPES.DONE, (data) => {
        this.addMessage('done', data);
      });

      this.sseClient.on(SSE_EVENT_TYPES.STREAM_END, (data) => {
        this.addMessage('stream_end', data);
      });
    },

    /**
     * 添加消息到列表
     */
    addMessage(type, data) {
      this.messages.push({
        type,
        data,
        timestamp: new Date().toLocaleTimeString(),
      });

      // 自动滚动到底部
      this.$nextTick(() => {
        const container = this.$el.querySelector('.overflow-y-auto');
        if (container) {
          container.scrollTop = container.scrollHeight;
        }
      });
    },

    /**
     * 格式化消息内容
     */
    formatMessageContent(data) {
      return JSON.stringify(data, null, 2);
    },

    /**
     * 获取消息类型的badge样式
     */
    getMessageTypeBadgeClass(type) {
      const classMap = {
        open: 'badge-success',
        close: 'badge-warning',
        error: 'badge-error',
        parse_error: 'badge-error',
        connected: 'badge-info',
        token: 'badge-primary',
        stage_update: 'badge-accent',
        progress: 'badge-secondary',
        done: 'badge-success',
        stream_end: 'badge-neutral',
      };
      return classMap[type] || 'badge-ghost';
    },
  },
};
</script>

<style scoped>
.project-stage-sse {
  padding: 1rem;
}

pre {
  max-width: 600px;
  overflow-x: auto;
}
</style>
