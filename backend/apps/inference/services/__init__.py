"""推理领域服务的稳定惰性导出面。

日志过滤器会在 Django 应用注册表初始化前加载。这里不能 eager import 任何
依赖 ORM 的预算、路由或价格模块，否则仅配置日志就会触发
``AppRegistryNotReady``。外部仍可使用原有 ``from ...services import X`` 写法，
真正访问符号时才加载对应模块。
"""

from importlib import import_module


_EXPORTS = {
    'BudgetService': ('.budget', 'BudgetService'),
    'CostEstimate': ('.pricing', 'CostEstimate'),
    'ErrorClassification': ('.errors', 'ErrorClassification'),
    'InferenceErrorClassifier': ('.errors', 'InferenceErrorClassifier'),
    'ParameterMergeResult': ('.parameters', 'ParameterMergeResult'),
    'PricingService': ('.pricing', 'PricingService'),
    'RouteSelection': ('.routing', 'RouteSelection'),
    'RoutingService': ('.routing', 'RoutingService'),
    'WorkItemService': ('.work_items', 'WorkItemService'),
    'classify_inference_error': ('.errors', 'classify_inference_error'),
    'estimate_generation_cost': ('.pricing', 'estimate_generation_cost'),
    'mask_sensitive_data': ('.security', 'mask_sensitive_data'),
    'merge_generation_parameters': ('.parameters', 'merge_generation_parameters'),
    'plan_video_segments': ('.segments', 'plan_video_segments'),
    'reserve_budget': ('.budget', 'reserve_budget'),
    'select_generation_targets': ('.routing', 'select_generation_targets'),
    'settle_budget': ('.budget', 'settle_budget'),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    """按需导入服务，避免 Django 启动阶段的隐式 ORM 依赖。"""

    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(name) from error
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value
