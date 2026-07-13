import { projectInferenceApi } from '@/api/inference'

export const PAID_CONFIRMATION_REQUIRED = 'PAID_CONFIRMATION_REQUIRED'

const missingConfigurationLabels = {
  generation_route: '可用的生成路由',
  cloud_authorization: '项目云端数据授权',
  project_budget: '大于 0 的项目 API 预算',
  global_daily_budget: '大于 0 的全局每日预算',
  global_monthly_budget: '大于 0 的全局月度预算',
}

const capabilityLabels = {
  llm: '文本生成',
  text2image: '图片生成',
  image_edit: '图片编辑',
  image2video: '视频生成',
  motion_render: '静态运镜',
}

function normalizeErrorPayload(error) {
  const payload = error?.response?.data?.error
  if (payload && typeof payload === 'object') {
    return payload
  }
  return {
    code: '',
    message: typeof payload === 'string' ? payload : (error?.message || '请求失败'),
  }
}

export function isPaidConfirmationRequired(error) {
  const payload = normalizeErrorPayload(error)
  return payload.code === PAID_CONFIRMATION_REQUIRED
    || String(payload.message || '').includes(PAID_CONFIRMATION_REQUIRED)
}

export function getPaidConfirmationDemand(error) {
  const payload = normalizeErrorPayload(error)
  return payload.code === PAID_CONFIRMATION_REQUIRED ? payload : {}
}

function missingLabel(code) {
  if (String(code).startsWith('price:')) {
    return '匹配当前 Provider 的有效价目表'
  }
  return missingConfigurationLabels[code] || code
}

function formatCny(value) {
  const amount = Number(value || 0)
  if (!Number.isFinite(amount)) {
    return '无法计算'
  }
  return `¥${amount.toFixed(4)}`
}

function formatAuthorization(settings) {
  if (!settings?.allow_cloud_data_transfer) {
    return '未授权'
  }
  const operator = settings.cloud_authorized_by_name
    ? `，授权人：${settings.cloud_authorized_by_name}`
    : ''
  return `已授权${operator}`
}

/**
 * 在一次明确的付费生成前读取服务端估算并请求用户确认。
 *
 * 这里故意采用 fail-closed：云端授权、价目表或预算任一项缺失时，
 * 前端不会发送确认字段。确认只对当前一次请求有效，不能复用为后续授权。
 */
export async function confirmPaidGeneration({
  projectId,
  capability,
  stageType,
  taskCount = 1,
  usagePerItem = {},
  durationSeconds,
  providerId = null,
  operationLabel = '使用 API 生成',
  confirm,
  alert,
}) {
  try {
    const estimateRequest = {
      capability,
      stage_type: stageType,
      task_count: Math.max(1, Number(taskCount || 1)),
      usage_per_item: usagePerItem,
    }
    if (durationSeconds != null) {
      estimateRequest.duration_seconds = durationSeconds
    }
    if (providerId) {
      // 新版估算接口可据此精确匹配显式 Provider；旧版会安全忽略该字段。
      estimateRequest.provider_id = providerId
    }

    const [settings, estimate] = await Promise.all([
      projectInferenceApi.getAISettings(projectId),
      projectInferenceApi.getEstimate(projectId, estimateRequest),
    ])

    const missing = (estimate?.missing_configuration || [])
      // 手动选择 API 不依赖“自动付费回退”开关，其余安全门仍全部生效。
      .filter((code) => code !== 'paid_fallback_disabled')
    const hasCompletePrice = (estimate?.price_details || []).some((item) => item.complete)
    const cloudAuthorized = Boolean(settings?.allow_cloud_data_transfer)
    const manualExecutionDenied = estimate?.manual_paid_execution_allowed === false

    if (!cloudAuthorized || missing.length || !hasCompletePrice || manualExecutionDenied) {
      const missingItems = new Set(missing.map(missingLabel))
      if (!cloudAuthorized) {
        missingItems.add('项目云端数据授权')
      }
      if (!hasCompletePrice) {
        missingItems.add('可可靠计算最大费用的有效价目表')
      }
      if (manualExecutionDenied && !missingItems.size) {
        missingItems.add('服务端手工付费执行许可')
      }
      await alert(
        [
          `${operationLabel}未提交。`,
          `云端授权：${formatAuthorization(settings)}`,
          '最大预计费用：无法可靠计算',
          `缺失配置：${Array.from(missingItems).join('、')}`,
          '请先到“本地 AI → 预算与账本 / 路由与价目表”补齐配置。',
        ].join('\n'),
        '付费调用已阻止',
        { tone: 'warning' }
      )
      return false
    }

    const confirmed = await confirm(
      [
        `操作：${operationLabel}`,
        `能力：${capabilityLabels[capability] || capability}`,
        `云端授权：${formatAuthorization(settings)}`,
        `最大预计费用：${formatCny(estimate.worst_api_cost_cny)} CNY`,
        `预计工作项：${estimate.effective_work_item_count || taskCount}`,
        '确认后仅授权本次付费请求；实际费用仍受项目、每日和月度预算限制。',
        '主观质量不满意不会触发后续自动付费。',
      ].join('\n'),
      '确认本次 API 费用',
      { tone: 'warning', confirmText: '确认并使用 API' }
    )
    if (!confirmed) {
      return null
    }
    return {
      confirmed: true,
      // 保留后端 Decimal 的字符串精度，不能先转成浮点数再作为费用上限回传。
      maxCostCny: String(estimate.worst_api_cost_cny ?? '0'),
      estimateScope: estimate.estimate_scope || 'route',
    }
  } catch (error) {
    await alert(
      `无法读取云端授权或最大费用，已安全阻止本次付费请求。\n${normalizeErrorPayload(error).message}`,
      '付费估算失败',
      { tone: 'error' }
    )
    return false
  }
}
