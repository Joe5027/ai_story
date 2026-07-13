<template>
  <section class="local-ai-panel">
    <div class="local-ai-section-heading">
      <div>
        <h2>本地 AI 运行节点</h2>
        <p>节点只承载本地推理，不连接业务数据库，也不持有付费 Provider Key。</p>
      </div>
      <button
        class="local-ai-button primary"
        type="button"
        @click="openCreateForm"
      >
        注册节点
      </button>
    </div>

    <form
      v-if="formVisible"
      class="local-ai-form-panel"
      @submit.prevent="saveNode"
    >
      <h3>{{ editingId ? '编辑运行节点' : '注册运行节点' }}</h3>
      <p>访问令牌只写不读；编辑时留空会保留服务器中已有值。</p>
      <div class="local-ai-form-grid">
        <label class="local-ai-field">
          <span>节点名称</span>
          <input
            v-model.trim="form.name"
            class="local-ai-input"
            required
            maxlength="120"
            placeholder="local-gpu-0"
          >
        </label>
        <label class="local-ai-field">
          <span>节点类型</span>
          <select
            v-model="form.node_type"
            class="local-ai-select"
            required
          >
            <option value="local_gpu">本地 GPU</option>
            <option value="cpu">CPU 节点</option>
            <option value="rented_gpu">租用 GPU</option>
            <option value="edge">边缘节点</option>
          </select>
        </label>
        <label class="local-ai-field wide">
          <span>Runtime Agent 地址</span>
          <input
            v-model.trim="form.agent_url"
            class="local-ai-input"
            required
            type="url"
            placeholder="http://127.0.0.1:9100"
          >
        </label>
        <label class="local-ai-field">
          <span>访问令牌</span>
          <input
            v-model="form.access_token"
            class="local-ai-input"
            type="password"
            autocomplete="new-password"
            :placeholder="editingId && form.has_access_token ? '已配置，留空保持不变' : 'Bearer Token'"
          >
        </label>
        <label class="local-ai-field">
          <span>总槽位</span>
          <input
            v-model.number="form.slot_count"
            class="local-ai-input"
            type="number"
            min="1"
            max="64"
            required
          >
        </label>
        <label class="local-ai-field">
          <span>并发上限</span>
          <input
            v-model.number="form.concurrency_limit"
            class="local-ai-input"
            type="number"
            min="1"
            max="64"
            required
          >
        </label>
        <label class="local-ai-field">
          <span>优先级</span>
          <input
            v-model.number="form.priority"
            class="local-ai-input"
            type="number"
          >
        </label>
        <label class="local-ai-checkbox">
          <input
            v-model="form.is_local"
            type="checkbox"
          >
          本机节点
        </label>
        <label class="local-ai-checkbox">
          <input
            v-model="form.is_active"
            type="checkbox"
          >
          启用调度
        </label>
      </div>
      <div class="local-ai-form-actions">
        <button
          class="local-ai-button primary"
          type="submit"
          :disabled="saving"
        >
          {{ saving ? '保存中' : '保存节点' }}
        </button>
        <button
          class="local-ai-button secondary"
          type="button"
          :disabled="saving"
          @click="closeForm"
        >
          取消
        </button>
      </div>
    </form>

    <div class="local-ai-toolbar">
      <div class="local-ai-note">
        健康检查不会生成内容；远程节点应使用 HTTPS，并在防火墙中仅放行 Django 主机。
      </div>
      <button
        class="local-ai-button secondary"
        type="button"
        :disabled="loading"
        @click="loadNodes"
      >
        刷新列表
      </button>
    </div>

    <div
      v-if="loading"
      class="local-ai-loading"
      aria-live="polite"
    >
      <span class="local-ai-spinner" />
      <span>正在读取节点状态</span>
    </div>
    <div
      v-else-if="errorMessage"
      class="local-ai-error"
    >
      <div>
        <h3>节点列表加载失败</h3>
        <p>{{ errorMessage }}</p>
        <button
          class="local-ai-button secondary"
          type="button"
          @click="loadNodes"
        >
          重新加载
        </button>
      </div>
    </div>
    <div
      v-else-if="!nodes.length"
      class="local-ai-empty"
    >
      <div>
        <h3>尚未注册运行节点</h3>
        <p>先启动 Runtime Agent，再注册地址和令牌。没有节点时，本地 Provider 不会被调度。</p>
        <button
          class="local-ai-button primary"
          type="button"
          @click="openCreateForm"
        >
          注册第一个节点
        </button>
      </div>
    </div>
    <div
      v-else
      class="local-ai-table-wrap"
    >
      <table class="local-ai-table">
        <thead>
          <tr>
            <th>节点</th>
            <th>健康</th>
            <th>能力 / 模型</th>
            <th>槽位</th>
            <th>硬件快照</th>
            <th>最后在线</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="node in nodes"
            :key="node.id"
          >
            <td>
              <strong>{{ node.name }}</strong>
              <small>{{ node.agent_url || node.endpoint || '未配置地址' }}</small>
            </td>
            <td>
              <span :class="['local-ai-status', node.health_status || 'unknown']">
                {{ healthLabel(node.health_status) }}
              </span>
              <small>{{ node.is_active ? '调度已启用' : '调度已停用' }}</small>
            </td>
            <td>
              <strong>{{ capabilitySummary(node) }}</strong>
              <small>{{ modelSummary(node) }}</small>
            </td>
            <td>
              <strong>{{ availableSlots(node) }} / {{ node.slot_count || node.concurrency_limit || 1 }}</strong>
              <small>可用 / 总数</small>
            </td>
            <td>
              <strong>{{ gpuSummary(node) }}</strong>
              <small>{{ memorySummary(node) }}</small>
            </td>
            <td>{{ formatDateTime(node.last_seen_at) }}</td>
            <td>
              <div class="local-ai-row-actions">
                <button
                  class="local-ai-button secondary"
                  type="button"
                  :disabled="healthCheckingId === node.id"
                  @click="checkHealth(node)"
                >
                  {{ healthCheckingId === node.id ? '检查中' : '检查' }}
                </button>
                <button
                  class="local-ai-button secondary"
                  type="button"
                  @click="openEditForm(node)"
                >
                  编辑
                </button>
                <button
                  class="local-ai-button danger"
                  type="button"
                  @click="removeNode(node)"
                >
                  删除
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script>
import { runtimeNodeApi } from '@/api/inference'

const emptyForm = () => ({
  name: '',
  node_type: 'local_gpu',
  agent_url: 'http://127.0.0.1:9100',
  access_token: '',
  has_access_token: false,
  slot_count: 1,
  concurrency_limit: 1,
  priority: 0,
  is_local: true,
  is_active: true
})

export default {
  name: 'RuntimeNodesPanel',
  data() {
    return {
      nodes: [],
      loading: false,
      saving: false,
      formVisible: false,
      editingId: null,
      healthCheckingId: null,
      errorMessage: '',
      form: emptyForm()
    }
  },
  created() {
    this.loadNodes()
  },
  methods: {
    normalizeList(response) {
      if (Array.isArray(response)) {
        return response
      }
      return Array.isArray(response?.results) ? response.results : []
    },
    async loadNodes() {
      this.loading = true
      this.errorMessage = ''
      try {
        this.nodes = this.normalizeList(await runtimeNodeApi.list())
      } catch (error) {
        this.errorMessage = this.getErrorMessage(error, '无法读取运行节点，请检查后端服务。')
      } finally {
        this.loading = false
      }
    },
    openCreateForm() {
      this.editingId = null
      this.form = emptyForm()
      this.formVisible = true
    },
    openEditForm(node) {
      this.editingId = node.id
      this.form = {
        ...emptyForm(),
        ...node,
        agent_url: node.agent_url || node.endpoint || '',
        access_token: '',
        has_access_token: Boolean(node.has_access_token || node.access_token_masked)
      }
      this.formVisible = true
    },
    closeForm() {
      this.formVisible = false
      this.editingId = null
      this.form = emptyForm()
    },
    async saveNode() {
      this.saving = true
      try {
        const payload = {
          name: this.form.name,
          node_type: this.form.node_type,
          agent_url: this.form.agent_url,
          slot_count: Number(this.form.slot_count),
          concurrency_limit: Number(this.form.concurrency_limit),
          priority: Number(this.form.priority || 0),
          is_local: Boolean(this.form.is_local),
          is_active: Boolean(this.form.is_active)
        }
        if (this.form.access_token) {
          payload.access_token = this.form.access_token
        }
        if (this.editingId) {
          await runtimeNodeApi.patch(this.editingId, payload)
        } else {
          await runtimeNodeApi.create(payload)
        }
        this.closeForm()
        await this.loadNodes()
      } catch (error) {
        await this.$alert(
          this.getErrorMessage(error, '节点保存失败'),
          '保存失败',
          { tone: 'error' }
        )
      } finally {
        this.saving = false
      }
    },
    async checkHealth(node) {
      this.healthCheckingId = node.id
      try {
        await runtimeNodeApi.refreshHealth(node.id)
        await this.loadNodes()
      } catch (error) {
        await this.$alert(
          this.getErrorMessage(error, 'Runtime Agent 健康检查失败'),
          '节点不可用',
          { tone: 'error' }
        )
      } finally {
        this.healthCheckingId = null
      }
    },
    async removeNode(node) {
      const confirmed = await this.$confirm(
        `确定删除运行节点“${node.name}”吗？已关联的本地 Provider 可能停止工作。`,
        '删除运行节点',
        { tone: 'danger', confirmText: '删除' }
      )
      if (!confirmed) {
        return
      }
      try {
        await runtimeNodeApi.remove(node.id)
        await this.loadNodes()
      } catch (error) {
        await this.$alert(this.getErrorMessage(error, '节点删除失败'), '删除失败', { tone: 'error' })
      }
    },
    capabilitySummary(node) {
      const capabilities = Array.isArray(node.capabilities)
        ? node.capabilities
        : Object.keys(node.capabilities || {})
      if (!capabilities.length) {
        return '等待能力上报'
      }
      const labels = capabilities.map((item) => {
        if (typeof item === 'string') {
          return item
        }
        return item.capability || item.name || item.adapter || item.model_id
      }).filter(Boolean)
      return labels.slice(0, 3).join('、') + (labels.length > 3 ? ` 等 ${labels.length} 项` : '')
    },
    modelSummary(node) {
      const adapterModels = Array.isArray(node.capabilities)
        ? node.capabilities.map((item) => typeof item === 'object' ? item.model_id || item.model : '').filter(Boolean)
        : []
      const models = node.models || node.available_models || node.capabilities?.models || adapterModels
      if (!Array.isArray(models) || !models.length) {
        return node.agent_version ? `Agent ${node.agent_version}` : '尚未上报模型版本'
      }
      return models.map((item) => typeof item === 'string' ? item : item.model_id || item.name).filter(Boolean).slice(0, 2).join('、')
    },
    availableSlots(node) {
      if (node.available_slots !== undefined) {
        return node.available_slots
      }
      const total = Number(node.slot_count || node.concurrency_limit || 1)
      const reserved = Number(node.reserved_slot_count || node.busy_slots || 0)
      return Math.max(0, total - reserved)
    },
    gpuSummary(node) {
      const hardware = node.hardware_snapshot || {}
      const gpu = hardware.gpu || hardware.gpu_name || node.gpu_name
      if (typeof gpu === 'object') {
        return gpu.name || gpu.model || 'GPU 已上报'
      }
      return gpu || (node.node_type === 'cpu' ? 'CPU 节点' : '等待硬件上报')
    },
    memorySummary(node) {
      const hardware = node.hardware_snapshot || {}
      const vram = hardware.vram_gb || hardware.gpu_memory_gb || node.vram_gb
      const ram = hardware.memory_gb || hardware.ram_gb || node.memory_gb
      if (!vram && !ram) {
        return '显存 / 内存未知'
      }
      return [vram ? `显存 ${vram}GB` : '', ram ? `内存 ${ram}GB` : ''].filter(Boolean).join('，')
    },
    healthLabel(status) {
      return {
        healthy: '健康',
        degraded: '降级',
        unavailable: '不可用',
        unknown: '未知'
      }[status] || '未知'
    },
    formatDateTime(value) {
      if (!value) {
        return '--'
      }
      const date = new Date(value)
      return Number.isNaN(date.getTime()) ? '--' : date.toLocaleString('zh-CN', { hour12: false })
    },
    getErrorMessage(error, fallback) {
      const data = error?.response?.data
      if (typeof data?.error === 'string') {
        return data.error
      }
      if (typeof data?.error?.message === 'string') {
        return data.error.message
      }
      if (typeof data?.detail === 'string') {
        return data.detail
      }
      return fallback
    }
  }
}
</script>
