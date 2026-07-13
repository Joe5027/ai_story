<template>
  <div class="page-shell model-form">
    <div class="page-header">
      <div class="page-header-main">
        <h1 class="page-title">
          {{ isEdit ? '编辑模型' : '添加模型' }}
        </h1>
        <p class="page-subtitle">
          {{ isEdit ? '更新模型配置与运行参数' : '创建单个模型提供商，适用于自定义地址或手动维护' }}
        </p>
      </div>
      <div class="header-actions">
        <button
          class="secondary-outline-action"
          :disabled="submitting"
          @click="handleCancel"
        >
          返回列表
        </button>
      </div>
    </div>

    <loading-container :loading="loading">
      <form
        class="form-layout"
        @submit.prevent="handleSubmit"
      >
        <section class="panel-card">
          <div class="card-top">
            <div>
              <h2 class="card-title">
                基础信息
              </h2>
              <p class="card-desc">
                配置模型名称、类型、执行器与访问地址
              </p>
            </div>
            <span class="pill">必填优先</span>
          </div>

          <div class="form-grid">
            <label class="field-block">
              <span class="field-label">模型别名 <em>*</em></span>
              <input
                v-model="formData.name"
                type="text"
                placeholder="例如: OpenAI GPT-4"
                class="field-input"
                required
              >
            </label>

            <label class="field-block">
              <span class="field-label">模型类型 <em>*</em></span>
              <select
                v-model="formData.provider_type"
                class="field-input"
                required
                :disabled="isEdit"
                @change="handleProviderTypeChange"
              >
                <option value="">
                  请选择类型
                </option>
                <option value="llm">
                  LLM模型
                </option>
                <option value="text2image">
                  文生图模型
                </option>
                <option value="image2video">
                  图生视频模型
                </option>
                <option value="image_edit">
                  图片编辑模型
                </option>
                <option value="motion_render">
                  非生成式视频运镜
                </option>
              </select>
            </label>

            <label class="field-block">
              <span class="field-label">执行器类 <em>*</em></span>
              <select
                v-model="formData.executor_class"
                class="field-input"
                required
                :disabled="!formData.provider_type || loadingExecutors"
              >
                <option value="">
                  {{ loadingExecutors ? '加载中...' : '请选择执行器' }}
                </option>
                <option
                  v-for="executor in availableExecutors"
                  :key="executor.value"
                  :value="executor.value"
                >
                  {{ executor.label }}
                </option>
              </select>
              <span class="field-hint">选择该模型使用的执行器类</span>
            </label>

            <label class="field-block">
              <span class="field-label">部署方式 <em>*</em></span>
              <select
                v-model="formData.deployment_mode"
                class="field-input"
                required
                @change="handleDeploymentModeChange"
              >
                <option value="api">付费 / 外部 API</option>
                <option value="local">本地 Runtime Agent</option>
                <option value="mock">Mock 测试</option>
              </select>
              <span class="field-hint">部署方式会决定密钥和运行节点的必填规则</span>
            </label>

            <label
              v-if="formData.deployment_mode === 'api'"
              class="field-block field-block-wide"
            >
              <span class="field-label">API地址 <em>*</em></span>
              <input
                v-model="formData.api_url"
                type="url"
                placeholder="https://api.openai.com/v1/chat/completions"
                class="field-input"
                required
              >
            </label>

            <label
              v-if="formData.deployment_mode === 'local'"
              class="field-block field-block-wide"
            >
              <span class="field-label">Runtime 节点 <em>*</em></span>
              <select
                v-model="formData.runtime_node"
                class="field-input"
                required
                :disabled="loadingNodes"
              >
                <option value="">{{ loadingNodes ? '节点加载中...' : '请选择运行节点' }}</option>
                <option
                  v-for="node in runtimeNodes"
                  :key="node.id"
                  :value="node.id"
                >
                  {{ node.name }} · {{ getNodeHealthLabel(node.health_status) }}
                </option>
              </select>
              <span class="field-hint">请先在“本地 AI 控制台”注册并检查 Runtime Agent</span>
            </label>

            <label class="field-block">
              <span class="field-label">模型名称 <em>*</em></span>
              <input
                v-model="formData.model_name"
                type="text"
                placeholder="例如: gpt-4-turbo-preview"
                class="field-input"
                required
              >
            </label>

            <label
              v-if="formData.deployment_mode === 'local'"
              class="field-block"
            >
              <span class="field-label">Runtime 模型 ID <em>*</em></span>
              <input
                v-model="formData.runtime_model_id"
                type="text"
                placeholder="例如: qwen3.5:9b-q4"
                class="field-input"
                required
              >
            </label>

            <label
              v-if="formData.deployment_mode === 'local'"
              class="field-block"
            >
              <span class="field-label">Runtime Adapter</span>
              <input
                v-model="formData.runtime_adapter"
                type="text"
                placeholder="ollama / comfyui / lightx2v"
                class="field-input"
              >
            </label>

            <label
              v-if="formData.deployment_mode === 'api'"
              class="field-block"
            >
              <span class="field-label">API密钥 <em v-if="!isEdit">*</em></span>
              <input
                v-model="formData.api_key"
                type="password"
                autocomplete="new-password"
                :placeholder="isEdit && formData.has_api_key ? `${formData.api_key_masked}，留空保持不变` : 'sk-...'"
                class="field-input"
                :required="!isEdit"
              >
              <span
                v-if="isEdit && formData.has_api_key"
                class="field-hint"
              >已配置 {{ formData.api_key_masked }}，服务器不会回显完整密钥</span>
            </label>
          </div>
        </section>

        <section
          v-if="formData.provider_type === 'llm'"
          class="panel-card"
        >
          <div class="card-top">
            <div>
              <h2 class="card-title">
                LLM 参数配置
              </h2>
              <p class="card-desc">
                控制文本生成的采样和长度
              </p>
            </div>
          </div>

          <div class="form-grid form-grid-3">
            <label class="field-block">
              <span class="field-label">最大Token数</span>
              <input
                v-model.number="formData.max_tokens"
                type="number"
                min="1"
                max="128000"
                class="field-input"
              >
            </label>

            <label class="field-block">
              <span class="field-label">温度 (0-2)</span>
              <input
                v-model.number="formData.temperature"
                type="number"
                min="0"
                max="2"
                step="0.1"
                class="field-input"
              >
            </label>

            <label class="field-block">
              <span class="field-label">Top P (0-1)</span>
              <input
                v-model.number="formData.top_p"
                type="number"
                min="0"
                max="1"
                step="0.1"
                class="field-input"
              >
            </label>
          </div>
        </section>

        <section
          v-if="formData.provider_type === 'text2image'"
          class="panel-card"
        >
          <div class="card-top">
            <div>
              <h2 class="card-title">
                文生图参数配置
              </h2>
              <p class="card-desc">
                设置默认输出尺寸
              </p>
            </div>
          </div>

          <div class="form-grid">
            <label class="field-block">
              <span class="field-label">默认宽度</span>
              <input
                v-model.number="extraConfig.width"
                type="number"
                min="256"
                max="10240"
                step="32"
                class="field-input"
                placeholder="1024"
              >
            </label>

            <label class="field-block">
              <span class="field-label">默认高度</span>
              <input
                v-model.number="extraConfig.height"
                type="number"
                min="256"
                max="10240"
                step="32"
                class="field-input"
                placeholder="1024"
              >
            </label>
          </div>
        </section>

        <section
          v-if="formData.provider_type === 'image2video'"
          class="panel-card"
        >
          <div class="card-top">
            <div>
              <h2 class="card-title">
                图生视频参数配置
              </h2>
              <p class="card-desc">
                设置默认帧率与时长
              </p>
            </div>
          </div>

          <div class="form-grid">
            <label class="field-block">
              <span class="field-label">默认FPS</span>
              <input
                v-model.number="extraConfig.fps"
                type="number"
                min="12"
                max="60"
                class="field-input"
                placeholder="24"
              >
            </label>

            <label class="field-block">
              <span class="field-label">默认时长(秒)</span>
              <input
                v-model.number="extraConfig.duration"
                type="number"
                min="1"
                max="30"
                class="field-input"
                placeholder="5"
              >
            </label>
          </div>
        </section>

        <section
          v-if="formData.provider_type === 'image_edit'"
          class="panel-card"
        >
          <div class="card-top">
            <div>
              <h2 class="card-title">
                图片编辑参数配置
              </h2>
              <p class="card-desc">
                设置默认画布尺寸与重绘强度
              </p>
            </div>
          </div>

          <div class="form-grid form-grid-3">
            <label class="field-block">
              <span class="field-label">默认宽度</span>
              <input
                v-model.number="extraConfig.width"
                type="number"
                min="256"
                max="10240"
                step="32"
                class="field-input"
                placeholder="1024"
              >
            </label>

            <label class="field-block">
              <span class="field-label">默认高度</span>
              <input
                v-model.number="extraConfig.height"
                type="number"
                min="256"
                max="10240"
                step="32"
                class="field-input"
                placeholder="1024"
              >
            </label>

            <label class="field-block">
              <span class="field-label">默认重绘强度</span>
              <input
                v-model.number="extraConfig.strength"
                type="number"
                min="0"
                max="1"
                step="0.05"
                class="field-input"
                placeholder="0.35"
              >
            </label>
          </div>
        </section>

        <section class="panel-card">
          <div class="card-top">
            <div>
              <h2 class="card-title">
                通用配置
              </h2>
              <p class="card-desc">
                控制超时、限流和启用状态
              </p>
            </div>
          </div>

          <div class="form-grid form-grid-3">
            <label class="field-block">
              <span class="field-label">优先级</span>
              <input
                v-model.number="formData.priority"
                type="number"
                min="0"
                class="field-input"
              >
              <span class="field-hint">数值越大优先级越高</span>
            </label>

            <label class="field-block">
              <span class="field-label">每分钟请求限制</span>
              <input
                v-model.number="formData.rate_limit_rpm"
                type="number"
                min="1"
                class="field-input"
              >
            </label>

            <label class="field-block">
              <span class="field-label">每天请求限制</span>
              <input
                v-model.number="formData.rate_limit_rpd"
                type="number"
                min="1"
                class="field-input"
              >
            </label>
          </div>

          <div class="form-grid utility-grid">
            <label class="field-block">
              <span class="field-label">超时时间(秒)</span>
              <input
                v-model.number="formData.timeout"
                type="number"
                min="1"
                max="600"
                class="field-input"
              >
            </label>

            <label class="toggle-card">
              <span class="field-label">状态</span>
              <span class="toggle-inner">
                <input
                  v-model="formData.is_active"
                  type="checkbox"
                >
                <span class="toggle-text">{{ formData.is_active ? '已激活' : '未激活' }}</span>
              </span>
            </label>

            <label
              v-if="formData.deployment_mode === 'local'"
              class="toggle-card"
            >
              <span class="field-label">技术失败回退能力</span>
              <span class="toggle-inner">
                <input
                  v-model="formData.supports_cloud_fallback"
                  type="checkbox"
                >
                <span class="toggle-text">{{ formData.supports_cloud_fallback ? '允许进入付费门检查' : '仅本地' }}</span>
              </span>
            </label>
          </div>
        </section>

        <div class="form-actions">
          <button
            type="submit"
            class="primary-action"
            :disabled="submitting"
          >
            <span>{{ submitting ? '保存中...' : '保存' }}</span>
          </button>
          <button
            type="button"
            class="secondary-outline-action"
            :disabled="submitting"
            @click="handleCancel"
          >
            取消
          </button>
        </div>
      </form>
    </loading-container>
  </div>
</template>

<script>
import { mapActions, mapState } from 'vuex'
import LoadingContainer from '@/components/common/LoadingContainer.vue'
import { runtimeNodeApi } from '@/api/inference'

export default {
  name: 'ModelForm',
  components: {
    LoadingContainer
  },
  data() {
    return {
      formData: {
        name: '',
        provider_type: '',
        deployment_mode: 'api',
        api_url: '',
        api_key: '',
        has_api_key: false,
        api_key_masked: '',
        model_name: '',
        executor_class: '',
        runtime_node: '',
        runtime_model_id: '',
        runtime_adapter: '',
        supports_cloud_fallback: false,
        max_tokens: 4096,
        temperature: 0.7,
        top_p: 1.0,
        timeout: 60,
        is_active: true,
        priority: 0,
        rate_limit_rpm: 60,
        rate_limit_rpd: 1000,
        extra_config: {}
      },
      extraConfig: {
        width: 1024,
        height: 1024,
        fps: 24,
        duration: 5,
        strength: 0.35
      },
      availableExecutors: [],
      loadingExecutors: false,
      runtimeNodes: [],
      loadingNodes: false,
      submitting: false
    }
  },
  computed: {
    ...mapState('models', {
      currentProvider: (state) => state.currentProvider,
      loading: (state) => state.loading.currentProvider
    }),
    isEdit() {
      return !!this.$route.params.id
    }
  },
  async created() {
    await this.loadRuntimeNodes()
    if (this.isEdit) {
      await this.loadProvider()
    }
  },
  methods: {
    ...mapActions('models', ['fetchProvider', 'createProvider', 'updateProvider']),

    async loadProvider() {
      try {
        const provider = await this.fetchProvider(this.$route.params.id)
        this.formData = { ...this.formData, ...provider }

        if (provider.extra_config) {
          this.extraConfig = { ...this.extraConfig, ...provider.extra_config }
        }

        if (provider.provider_type) {
          await this.loadExecutorChoices(provider.provider_type)
        }
      } catch (error) {
        console.error('加载模型失败:', error)
        await this.$alert('加载模型失败', '加载失败', { tone: 'error' })
        this.$router.push({ name: 'ModelList' })
      }
    },

    async handleProviderTypeChange() {
      this.formData.executor_class = ''
      if (this.formData.provider_type) {
        await this.loadExecutorChoices(this.formData.provider_type)
      } else {
        this.availableExecutors = []
      }
    },

    handleDeploymentModeChange() {
      if (this.formData.deployment_mode !== 'api') {
        this.formData.api_url = ''
        this.formData.api_key = ''
      }
      if (this.formData.deployment_mode !== 'local') {
        this.formData.runtime_node = ''
        this.formData.runtime_model_id = ''
        this.formData.runtime_adapter = ''
      }
    },

    async loadRuntimeNodes() {
      this.loadingNodes = true
      try {
        const response = await runtimeNodeApi.list({ is_active: true, page_size: 500 })
        this.runtimeNodes = Array.isArray(response) ? response : (response.results || [])
      } catch (error) {
        console.error('加载 Runtime 节点失败:', error)
        this.runtimeNodes = []
      } finally {
        this.loadingNodes = false
      }
    },

    async loadExecutorChoices(providerType) {
      this.loadingExecutors = true
      try {
        const { modelProviderApi } = await import('@/api/models')
        const response = await modelProviderApi.getExecutorChoices(providerType)

        if (response.executors) {
          this.availableExecutors = response.executors
        } else if (response[providerType]) {
          this.availableExecutors = response[providerType]
        } else {
          this.availableExecutors = []
        }

        if (this.availableExecutors.length === 1 && !this.formData.executor_class) {
          this.formData.executor_class = this.availableExecutors[0].value
        }
      } catch (error) {
        console.error('加载执行器选项失败:', error)
        this.availableExecutors = []
      } finally {
        this.loadingExecutors = false
      }
    },

    async handleSubmit() {
      this.submitting = true

      try {
        const submitData = {
          ...this.formData,
          extra_config: this.extraConfig
        }
        delete submitData.has_api_key
        delete submitData.api_key_masked
        delete submitData.runtime_node_name
        if (this.isEdit && !submitData.api_key) {
          delete submitData.api_key
        }
        if (submitData.deployment_mode !== 'api') {
          submitData.api_url = ''
          submitData.api_key = ''
        }
        if (submitData.deployment_mode !== 'local') {
          submitData.runtime_node = null
          submitData.runtime_model_id = ''
          submitData.runtime_adapter = ''
        }

        if (this.isEdit) {
          await this.updateProvider({
            id: this.$route.params.id,
            data: submitData
          })
          await this.$alert('更新成功', '操作完成', { tone: 'success' })
        } else {
          await this.createProvider(submitData)
          await this.$alert('创建成功', '操作完成', { tone: 'success' })
        }

        this.$router.push({ name: 'ModelList' })
      } catch (error) {
        console.error('保存失败:', error)
        const errorMsg = error.response?.data?.error ||
          error.response?.data?.message ||
          Object.values(error.response?.data || {}).flat().join(', ') ||
          '保存失败'
        await this.$alert(errorMsg, '保存失败', { tone: 'error' })
      } finally {
        this.submitting = false
      }
    },

    handleCancel() {
      this.$router.push({ name: 'ModelList' })
    },

    getNodeHealthLabel(status) {
      return {
        healthy: '健康',
        degraded: '降级',
        unavailable: '不可用',
        unknown: '未知'
      }[status] || '未知'
    }
  }
}
</script>

<style scoped>
.page-shell {
  min-height: 100vh;
  padding: 2.5rem 3.5rem 3rem;
  background: transparent;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1.5rem;
  margin-bottom: 2rem;
}

.page-header-main {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.page-title {
  font-size: 2.2rem;
  font-weight: 600;
  color: #0f172a;
  margin: 0;
  letter-spacing: -0.02em;
}

.layout-shell.theme-dark .page-title {
  color: #e2e8f0;
}

.page-subtitle {
  font-size: 0.95rem;
  color: #64748b;
  margin: 0;
}

.layout-shell.theme-dark .page-subtitle {
  color: #94a3b8;
}

.header-actions {
  display: flex;
  gap: 0.75rem;
}

.form-layout {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.panel-card {
  background: linear-gradient(90deg, rgba(20, 184, 166, 0.7) 0%, rgba(14, 165, 233, 0.7) 100%) 0 0 / 0 3px no-repeat,
    rgba(255, 255, 255, 0.92);
  border-radius: 18px;
  padding: 1.35rem;
  border: 1px solid rgba(148, 163, 184, 0.2);
  box-shadow: 0 16px 32px rgba(15, 23, 42, 0.08);
  transition: all 0.3s ease;
}

.layout-shell.theme-dark .panel-card {
  background: linear-gradient(90deg, rgba(94, 234, 212, 0.5) 0%, rgba(56, 189, 248, 0.5) 100%) 0 0 / 0 3px no-repeat,
    rgba(15, 23, 42, 0.92);
  border-color: rgba(148, 163, 184, 0.2);
  box-shadow: 0 16px 32px rgba(2, 6, 23, 0.55);
}

.panel-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 18px 36px rgba(15, 23, 42, 0.12);
  border-color: rgba(148, 163, 184, 0.35);
  background-size: 100% 3px, auto;
}

.layout-shell.theme-dark .panel-card:hover {
  box-shadow: 0 18px 36px rgba(2, 6, 23, 0.6);
}

.card-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  margin-bottom: 1rem;
}

.card-title {
  margin: 0;
  font-size: 1.1rem;
  font-weight: 600;
  color: #0f172a;
}

.layout-shell.theme-dark .card-title {
  color: #e2e8f0;
}

.card-desc {
  margin: 0.4rem 0 0;
  color: #64748b;
  font-size: 0.9rem;
}

.layout-shell.theme-dark .card-desc {
  color: #94a3b8;
}

.pill {
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  font-size: 0.75rem;
  background: rgba(20, 184, 166, 0.16);
  color: #0f172a;
}

.layout-shell.theme-dark .pill {
  background: rgba(94, 234, 212, 0.22);
  color: #e2e8f0;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1rem;
}

.form-grid-3 {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.utility-grid {
  margin-top: 1rem;
}

.field-block {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}

.field-block-wide {
  grid-column: 1 / -1;
}

.field-label {
  font-size: 0.82rem;
  color: #64748b;
}

.field-label em {
  font-style: normal;
  color: #ef4444;
}

.layout-shell.theme-dark .field-label {
  color: #94a3b8;
}

.field-input {
  width: 100%;
  padding: 0.8rem 0.95rem;
  border-radius: 14px;
  border: 1px solid rgba(148, 163, 184, 0.28);
  background: rgba(255, 255, 255, 0.9);
  color: #0f172a;
  outline: none;
  transition: all 0.2s ease;
}

.layout-shell.theme-dark .field-input {
  background: rgba(15, 23, 42, 0.9);
  border-color: rgba(148, 163, 184, 0.22);
  color: #e2e8f0;
}

.field-input:focus {
  border-color: rgba(20, 184, 166, 0.6);
  box-shadow: 0 0 0 3px rgba(20, 184, 166, 0.18);
}

.field-input:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.field-hint {
  font-size: 0.78rem;
  color: #94a3b8;
}

.toggle-card {
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
  padding: 0.85rem 1rem;
  border-radius: 14px;
  background: rgba(148, 163, 184, 0.1);
}

.layout-shell.theme-dark .toggle-card {
  background: rgba(30, 41, 59, 0.6);
}

.toggle-inner {
  display: inline-flex;
  align-items: center;
  gap: 0.65rem;
  color: #475569;
}

.layout-shell.theme-dark .toggle-inner {
  color: #cbd5e1;
}

.form-actions {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.primary-action,
.secondary-outline-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0.72rem 1.35rem;
  border-radius: 999px;
  font-size: 0.92rem;
  cursor: pointer;
  transition: all 0.2s ease;
}

.primary-action {
  border: 1px solid rgba(15, 23, 42, 0.12);
  background: #ffffff;
  color: #0f172a;
}

.secondary-outline-action {
  border: 1px solid rgba(148, 163, 184, 0.28);
  background: rgba(255, 255, 255, 0.85);
  color: #334155;
}

.layout-shell.theme-dark .primary-action,
.layout-shell.theme-dark .secondary-outline-action {
  background: rgba(15, 23, 42, 0.9);
  color: #e2e8f0;
  border-color: rgba(148, 163, 184, 0.24);
}

.primary-action:hover,
.secondary-outline-action:hover {
  border-color: rgba(20, 184, 166, 0.6);
  box-shadow: 0 12px 24px rgba(20, 184, 166, 0.18);
  transform: translateY(-1px);
}

.primary-action:disabled,
.secondary-outline-action:disabled {
  opacity: 0.65;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

@media (max-width: 1024px) {
  .form-grid-3 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .page-shell {
    padding: 2rem 1.5rem;
  }

  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .header-actions,
  .form-actions {
    width: 100%;
  }

  .form-grid,
  .form-grid-3,
  .utility-grid {
    grid-template-columns: 1fr;
  }

  .primary-action,
  .secondary-outline-action {
    width: 100%;
  }
}
</style>
