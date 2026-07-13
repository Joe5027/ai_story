"""
执行器工厂
职责: 根据ModelProvider配置动态创建AI客户端实例
遵循工厂模式: 封装复杂的对象创建逻辑
"""

import logging
from typing import Optional
from .base import BaseAIClient
from .outbound_guard import require_paid_provider_authorization
from .registry import get_executor_class, validate_executor_for_provider

logger = logging.getLogger(__name__)


def create_ai_client(provider) -> BaseAIClient:
    """
    根据ModelProvider实例创建AI客户端

    Args:
        provider: ModelProvider实例（来自apps.models.models）

    Returns:
        BaseAIClient: 客户端实例

    Raises:
        ValueError: 配置无效
        ImportError: 执行器类无法导入
        Exception: 客户端创建失败
    """
    # 验证provider对象
    if not provider:
        raise ValueError("ModelProvider实例不能为空")

    # 任何遗留 Processor 或新代码若绕过 Hybrid 路由直接创建付费客户端，都会
    # 在这里 fail closed；本地与 Mock Provider 不受影响。
    require_paid_provider_authorization(provider)

    # 获取执行器类路径
    executor_class_path = provider.executor_class

    # 如果未配置执行器，使用默认执行器
    if not executor_class_path:
        executor_class_path = provider.get_default_executor()
        logger.warning(
            f"ModelProvider '{provider.name}' 未配置executor_class，"
            f"使用默认执行器: {executor_class_path}"
        )

    if not executor_class_path:
        raise ValueError(
            f"ModelProvider '{provider.name}' 未配置执行器，"
            f"且无法获取默认执行器"
        )

    try:
        # 动态导入执行器类
        executor_class = get_executor_class(executor_class_path)

        # 准备配置参数
        config = {
            'timeout': provider.timeout,
            'max_tokens': provider.max_tokens,
            'temperature': provider.temperature,
            'top_p': provider.top_p,
            **provider.extra_config  # 合并额外配置
        }

        api_url = provider.api_url
        api_key = provider.api_key
        model_name = provider.model_name
        if getattr(provider, 'deployment_mode', 'api') == 'local':
            runtime_node = getattr(provider, 'runtime_node', None)
            if runtime_node:
                api_url = runtime_node.agent_url
                api_key = runtime_node.access_token
            model_name = getattr(provider, 'runtime_model_id', '') or model_name
            config['runtime_adapter'] = getattr(provider, 'runtime_adapter', '')

        # 创建客户端实例
        client = executor_class(
            api_url=api_url,
            api_key=api_key,
            model_name=model_name,
            **config
        )

        logger.info(
            f"成功创建AI客户端: provider='{provider.name}', "
            f"executor='{executor_class_path}'"
        )

        return client

    except ImportError as e:
        logger.error(f"无法导入执行器类 '{executor_class_path}': {str(e)}")
        raise

    except Exception as e:
        logger.error(
            f"创建AI客户端失败: provider='{provider.name}', "
            f"executor='{executor_class_path}', error={str(e)}",
            exc_info=True
        )
        raise Exception(f"创建AI客户端失败: {str(e)}")


def create_ai_client_safe(provider) -> Optional[BaseAIClient]:
    """
    安全版本的create_ai_client，捕获所有异常并返回None

    Args:
        provider: ModelProvider实例

    Returns:
        Optional[BaseAIClient]: 客户端实例，失败时返回None
    """
    try:
        return create_ai_client(provider)
    except Exception as e:
        logger.error(f"创建AI客户端失败（安全模式）: {str(e)}")
        return None
