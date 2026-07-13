/**
 * 混合推理控制面 API。
 *
 * 这里仅封装控制面请求，不缓存访问令牌、API Key 或完整提示词。
 * 列表响应由页面统一兼容 DRF 的 { count, results } 和数组两种形态。
 */
import apiClient from '@/services/apiClient'

const createCrudApi = (resource) => ({
  list(params = {}) {
    return apiClient.get(`/models/${resource}/`, { params })
  },
  get(id) {
    return apiClient.get(`/models/${resource}/${id}/`)
  },
  create(data) {
    return apiClient.post(`/models/${resource}/`, data)
  },
  update(id, data) {
    return apiClient.put(`/models/${resource}/${id}/`, data)
  },
  patch(id, data) {
    return apiClient.patch(`/models/${resource}/${id}/`, data)
  },
  remove(id) {
    return apiClient.delete(`/models/${resource}/${id}/`)
  }
})

export const runtimeNodeApi = {
  ...createCrudApi('runtime-nodes'),
  refreshHealth(id) {
    return apiClient.post(`/models/runtime-nodes/${id}/refresh-health/`)
  },
  capabilities(id) {
    // 节点详情已包含最新能力快照，不额外假设一个不存在的子资源。
    return apiClient.get(`/models/runtime-nodes/${id}/`)
  },
  reload(id) {
    return apiClient.post(`/models/runtime-nodes/${id}/runtime-reload/`)
  }
}

export const generationRouteApi = {
  ...createCrudApi('generation-routes')
}

export const generationRouteTargetApi = createCrudApi('generation-route-targets')

export const providerPriceRateApi = createCrudApi('provider-price-rates')

export const budgetApi = {
  summary(params = {}) {
    return apiClient.get('/models/budget-summary/', { params })
  },
  listPolicies(params = {}) {
    return apiClient.get('/models/budget-policies/', { params })
  },
  createPolicy(data) {
    return apiClient.post('/models/budget-policies/', data)
  },
  updatePolicy(id, data) {
    return apiClient.patch(`/models/budget-policies/${id}/`, data)
  }
}

export const usageLedgerApi = {
  list(params = {}) {
    return apiClient.get('/models/usage-logs/', { params })
  },
  get(id) {
    return apiClient.get(`/models/usage-logs/${id}/`)
  },
  exportCsv(params = {}) {
    return apiClient.get('/models/usage-logs/export_csv/', {
      params,
      responseType: 'blob'
    })
  }
}

const projectPath = (projectId, suffix) =>
  `/projects/projects/${encodeURIComponent(projectId)}/${suffix}/`

export const projectInferenceApi = {
  getAISettings(projectId) {
    return apiClient.get(projectPath(projectId, 'ai-settings'))
  },
  updateAISettings(projectId, data) {
    return apiClient.patch(projectPath(projectId, 'ai-settings'), data)
  },
  getEstimate(projectId, data = {}) {
    return apiClient.post(projectPath(projectId, 'generation-estimates'), data)
  },
  getWorkItems(projectId, params = {}) {
    return apiClient.get(projectPath(projectId, 'generation-work-items'), { params })
  },
  retryFailedItems(projectId, data = {}) {
    return apiClient.post(projectPath(projectId, 'retry-failed-items'), data)
  },
  regenerateWorkItemWithApi(projectId, data, idempotencyKey) {
    return apiClient.post(
      projectPath(projectId, 'regenerate-work-item-with-api'),
      data,
      {
        headers: { 'Idempotency-Key': idempotencyKey }
      }
    )
  }
}

export default {
  runtimeNodeApi,
  generationRouteApi,
  generationRouteTargetApi,
  providerPriceRateApi,
  budgetApi,
  usageLedgerApi,
  projectInferenceApi
}
