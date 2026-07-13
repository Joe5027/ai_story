"""
AI 代理视图
职责: 统一代理模型列表、LLM、图片和视频请求，复用 ai_story 的鉴权与 ModelProvider 配置
"""

import time
import uuid
import logging
from typing import Any, Dict, List, Optional

import requests
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.models.models import ModelProvider
from apps.projects.models import Project
from apps.inference.services.hybrid import HybridExecutionFailed, HybridInferenceService
from apps.models.serializers import ModelProviderListSerializer
from core.ai_client.base import AIResponse
from core.ai_client.factory import create_ai_client
from core.ai_client.image_service import ImageGenerationService
from core.ai_client.schemas import ImageEditRequest, Text2ImageRequest
from core.utils.file_storage import image_storage, video_storage

logger = logging.getLogger(__name__)


class ProxyExecutionError(RuntimeError):
    """带稳定错误码的代理执行拒绝或上游失败。"""

    def __init__(self, code, message, http_status=status.HTTP_400_BAD_REQUEST):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _project_for_execution(request, provider):
    project_id = request.data.get('project_id')
    if not project_id:
        if provider.deployment_mode == 'api':
            raise ProxyExecutionError(
                'PROJECT_REQUIRED',
                'API 调用必须绑定项目，才能执行隐私、价格与预算校验。',
            )
        return None
    project = Project.objects.filter(pk=project_id, user=request.user).first()
    if not project:
        raise ProxyExecutionError('PROJECT_NOT_FOUND', '项目不存在或无权访问。', 404)
    return project


def _run_guarded(request, provider, capability, stage_type, parameters, usage, invoke):
    """所有 API 执行统一经过项目授权、手工价目表和预算三重门。"""

    project = _project_for_execution(request, provider)
    if provider.deployment_mode == 'api' and request.data.get('confirm_paid_generation') is not True:
        raise ProxyExecutionError(
            'PAID_CONFIRMATION_REQUIRED',
            '选择 API Provider 时必须先确认本次可能产生费用。',
        )
    if provider.deployment_mode == 'api':
        confirmed_max = request.data.get('confirmed_max_cost_cny')
        if confirmed_max in (None, ''):
            raise ProxyExecutionError(
                'BUDGET_DENIED',
                '选择 API Provider 时必须提交本次确认的最大费用。',
            )
        parameters = {
            **(parameters or {}),
            'confirmed_max_cost_cny': str(confirmed_max),
        }
    if project is None:
        # Mock/本地调试可兼容旧无项目调用；它们不会进入付费门。
        return invoke(provider, parameters, False)
    try:
        execution = HybridInferenceService.execute(
            project=project,
            capability=capability,
            stage_type=stage_type,
            invoke=invoke,
            request_parameters=parameters,
            usage_estimate=usage,
            explicit_provider=provider,
            manual_api=provider.deployment_mode == 'api',
        )
        return execution.value
    except HybridExecutionFailed as error:
        http_status = 403 if error.code in {
            'CLOUD_NOT_AUTHORIZED', 'BUDGET_DENIED', 'PRICE_MISSING'
        } else 502
        raise ProxyExecutionError(error.code, str(error), http_status)


PROVIDER_TYPE_LABELS = {
    'llm': 'LLM',
    'text2image': '文生图',
    'image2video': '视频',
    'image_edit': '图片编辑',
}

MODEL_CATEGORY_DEFINITIONS = [
    {
        'key': 'chat',
        'title': '对话模型',
        'description': '文本对话、问答与推理',
        'icon_name': 'Bot',
        'provider_type': 'llm',
    },
    {
        'key': 'image',
        'title': '图像生成模型',
        'description': '文生图与参考图生成',
        'icon_name': 'Image',
        'provider_type': 'text2image',
    },
    {
        'key': 'video',
        'title': '视频生成模型',
        'description': '图生视频与动态内容生成',
        'icon_name': 'Video',
        'provider_type': 'image2video',
    },
    {
        'key': 'image_edit',
        'title': '图片编辑模型',
        'description': '局部重绘、修复与图像增强',
        'icon_name': 'Palette',
        'provider_type': 'image_edit',
    },
]


def _ensure_list(value: Any) -> List[Any]:
    """将输入统一转换为列表。"""
    if value is None or value == '':
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """安全解析整数。"""
    if value in (None, ''):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """安全解析浮点数。"""
    if value in (None, ''):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_size(value: Any) -> Dict[str, Optional[int]]:
    """解析 `1024x1024` 风格尺寸。"""
    if not value or not isinstance(value, str) or 'x' not in value.lower():
        return {'width': None, 'height': None}

    width_str, height_str = value.lower().split('x', 1)
    return {
        'width': _parse_int(width_str),
        'height': _parse_int(height_str),
    }


def _build_provider_payload(provider: ModelProvider) -> Dict[str, Any]:
    """构造响应中的 provider 摘要。"""
    return {
        'id': str(provider.id),
        'name': provider.name,
        'provider_type': provider.provider_type,
        'provider_type_display': provider.get_provider_type_display(),
        'model_name': provider.model_name,
        'deployment_mode': provider.deployment_mode,
        'health_status': provider.health_status,
    }


def _pick_provider(provider_type: str, model: str = '') -> Optional[ModelProvider]:
    """按模型名或 provider id 解析当前要使用的模型配置。"""
    queryset = ModelProvider.objects.filter(
        provider_type=provider_type,
        is_active=True,
    ).order_by('-priority', '-created_at')

    if model:
        provider = queryset.filter(model_name=model).first()
        if provider:
            return provider

        try:
            provider = queryset.filter(id=model).first()
            if provider:
                return provider
        except (ValueError, TypeError):
            pass

    # 未显式选择时优先免费本地/Mock。API 只作为最后候选，且后续仍需三重门。
    for mode in ('local', 'mock', 'api'):
        provider = queryset.filter(deployment_mode=mode).first()
        if provider:
            return provider
    return None


class AIModelsView(APIView):
    """
    统一模型列表接口
    GET /api/v1/ai/models
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        provider_map = {}
        categories = []

        for category in MODEL_CATEGORY_DEFINITIONS:
            providers = ModelProvider.objects.filter(
                provider_type=category['provider_type'],
                is_active=True,
            ).order_by('-priority', '-created_at')
            serialized = ModelProviderListSerializer(providers, many=True).data
            provider_map[category['key']] = serialized
            categories.append({
                **category,
                'count': len(serialized),
                'items': serialized,
            })

        return Response({
            **provider_map,
            'categories': categories,
        })


class ChatCompletionsProxyView(APIView):
    """
    LLM 代理接口
    POST /api/v1/ai/chat/completions
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        model = request.data.get('model', '')
        messages = request.data.get('messages', [])
        temperature = request.data.get('temperature', 0.7)
        max_tokens = request.data.get('max_tokens', 2000)

        if not messages:
            return Response(
                {'error': 'messages 不能为空'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        provider = _pick_provider('llm', model)
        if not provider:
            return Response(
                {'error': '没有可用的 LLM 模型提供商，请在 ai_story 后台配置 ModelProvider'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        payload = {
            'model': provider.model_name,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
            'stream': False,
        }

        try:
            started = time.time()

            def invoke(selected_provider, parameters, repaired_structure=False):
                if selected_provider.deployment_mode in {'local', 'mock'}:
                    client = create_ai_client(selected_provider)
                    prompt = '\n'.join(
                        f"{item.get('role', 'user')}: {item.get('content', '')}"
                        for item in parameters['messages']
                    )
                    response = client._generate_text(
                        prompt,
                        max_tokens=parameters['max_tokens'],
                        temperature=parameters['temperature'],
                        repair_structure=repaired_structure,
                        project_id=str(request.data.get('project_id') or ''),
                        stage_type='chat',
                    )
                    if not response.success:
                        failure = RuntimeError(response.error or '本地 LLM 执行失败')
                        failure.code = str(response.error or 'RUNTIME_CRASH').split(':', 1)[0]
                        raise failure
                    return {
                        'id': f'chatcmpl-{uuid.uuid4().hex[:8]}',
                        'object': 'chat.completion',
                        'model': selected_provider.model_name,
                        'choices': [{
                            'index': 0,
                            'message': {'role': 'assistant', 'content': response.text},
                            'finish_reason': 'stop',
                        }],
                        'usage': (response.metadata or {}).get('usage', {}),
                    }

                upstream_response = requests.post(
                    selected_provider.api_url,
                    headers={
                        'Authorization': f'Bearer {selected_provider.api_key}',
                        'Content-Type': 'application/json',
                    },
                    json={**parameters, 'model': selected_provider.model_name},
                    timeout=selected_provider.timeout,
                )
                if upstream_response.status_code != 200:
                    failure = RuntimeError(
                        f'上游 LLM API 返回 HTTP {upstream_response.status_code}'
                    )
                    failure.code = (
                        'INVALID_REQUEST' if upstream_response.status_code < 500
                        else 'RUNTIME_CRASH'
                    )
                    raise failure
                return upstream_response.json()

            result = _run_guarded(
                request,
                provider,
                'llm',
                'chat',
                payload,
                {
                    'input_tokens': max(1, len(str(messages)) // 2),
                    'output_tokens': int(max_tokens),
                },
                invoke,
            )
            latency_ms = int((time.time() - started) * 1000)
            if 'id' not in result:
                result['id'] = f'chatcmpl-{uuid.uuid4().hex[:8]}'
            result.setdefault('model', provider.model_name)
            result.setdefault('metadata', {})
            result['metadata'].update({
                'latency_ms': latency_ms,
                'provider': _build_provider_payload(provider),
            })
            return Response(result)

        except ProxyExecutionError as exc:
            return Response(
                {'error': {'code': exc.code, 'message': str(exc)}}, status=exc.http_status
            )
        except requests.Timeout:
            return Response(
                {'error': '上游 API 请求超时'},
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except Exception as exc:
            logger.error('LLM 代理异常: %s', exc, exc_info=True)
            return Response(
                {'error': f'代理请求失败: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ImagesGenerationsProxyView(APIView):
    """
    图片统一代理接口
    POST /api/v1/ai/images/generations
    """

    permission_classes = [permissions.IsAuthenticated]

    def _resolve_provider_type(self, request) -> str:
        mode = str(request.data.get('mode', '') or request.data.get('edit_mode', '')).lower()
        source_images = _ensure_list(
            request.data.get('image') or request.data.get('images') or request.data.get('source_images')
        )
        mask = request.data.get('mask') or request.data.get('mask_image')

        if mode in {'inpaint', 'img2img', 'image_edit', 'edit'}:
            return 'image_edit'
        if mask:
            return 'image_edit'
        if request.data.get('provider_type') == 'image_edit':
            return 'image_edit'
        if source_images and request.data.get('force_edit') is True:
            return 'image_edit'
        return 'text2image'

    def _build_request_context(self, request) -> Dict[str, Any]:
        size = _parse_size(request.data.get('size'))
        width = _parse_int(request.data.get('width'), size['width'])
        height = _parse_int(request.data.get('height'), size['height'])
        reference_images = _ensure_list(
            request.data.get('image') or request.data.get('images') or request.data.get('source_images')
        )
        payload_model = request.data.get('model', '')

        return {
            'model': payload_model,
            'prompt': request.data.get('prompt', ''),
            'negative_prompt': request.data.get('negative_prompt', ''),
            'mask': request.data.get('mask') or request.data.get('mask_image') or '',
            'width': width,
            'height': height,
            'reference_images': reference_images,
            'aspect_ratio': request.data.get('aspect_ratio') or request.data.get('ratio') or '',
            'sample_count': _parse_int(request.data.get('n'), _parse_int(request.data.get('sample_count'), 1)) or 1,
            'seed': _parse_int(request.data.get('seed')),
            'strength': _parse_float(request.data.get('strength'), 0.35) or 0.35,
            'mode': request.data.get('mode') or request.data.get('edit_mode') or '',
            'extra': {
                key: value for key, value in request.data.items()
                if key not in {
                    'model', 'prompt', 'negative_prompt', 'mask', 'mask_image', 'width', 'height',
                    'image', 'images', 'source_images', 'aspect_ratio', 'ratio', 'n', 'sample_count',
                    'seed', 'strength', 'mode', 'edit_mode', 'size', 'provider_type', 'force_edit',
                    'project_id', 'confirm_paid_generation',
                }
            },
        }

    def _normalize_image_result(
        self,
        result: AIResponse,
        provider: ModelProvider,
        provider_type: str,
    ) -> Response:
        if not result.success:
            return Response(
                {'error': result.error or '图片生成失败'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({
            'id': f'imggen-{uuid.uuid4().hex[:8]}',
            'object': 'list',
            'created': int(time.time()),
            'model': provider.model_name,
            'provider': _build_provider_payload(provider),
            'provider_type': provider_type,
            'data': result.data if isinstance(result.data, list) else _ensure_list(result.data),
            'text': result.text,
            'metadata': result.metadata,
        })

    def post(self, request):
        provider_type = self._resolve_provider_type(request)
        context = self._build_request_context(request)

        if not context['prompt']:
            return Response(
                {'error': 'prompt 不能为空'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        provider = _pick_provider(provider_type, context['model'])
        if not provider:
            return Response(
                {'error': f'没有可用的 {PROVIDER_TYPE_LABELS.get(provider_type, provider_type)} 模型提供商'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            def invoke(selected_provider, parameters, repaired_structure=False):
                client = create_ai_client(selected_provider)
                if provider_type == 'image_edit':
                    return ImageGenerationService.edit(
                        selected_provider,
                        ImageEditRequest(
                            source_images=parameters['reference_images'],
                            prompt=parameters['prompt'],
                            mask_image=parameters['mask'],
                            negative_prompt=parameters['negative_prompt'],
                            strength=parameters['strength'],
                            width=parameters['width'],
                            height=parameters['height'],
                            edit_mode=parameters['mode'] or 'img2img',
                            extra={
                                **parameters['extra'],
                                'project_id': str(request.data.get('project_id') or ''),
                                'stage_type': 'image_edit',
                            },
                        ),
                        client=client,
                    )
                return ImageGenerationService.generate(
                    selected_provider,
                    Text2ImageRequest(
                        prompt=parameters['prompt'],
                        negative_prompt=parameters['negative_prompt'],
                        reference_images=parameters['reference_images'],
                        width=parameters['width'],
                        height=parameters['height'],
                        aspect_ratio=parameters['aspect_ratio'],
                        sample_count=parameters['sample_count'],
                        seed=parameters['seed'],
                        extra={
                            **parameters['extra'],
                            'project_id': str(request.data.get('project_id') or ''),
                            'stage_type': provider_type,
                        },
                    ),
                    client=client,
                )

            width = context['width'] or 1024
            height = context['height'] or 1024
            ai_response = _run_guarded(
                request,
                provider,
                provider_type,
                provider_type,
                context,
                {
                    'image_count': context['sample_count'],
                    'width': width,
                    'height': height,
                    'sample_count': context['sample_count'],
                },
                invoke,
            )
            return self._normalize_image_result(ai_response, provider, provider_type)
        except ProxyExecutionError as exc:
            return Response(
                {'error': {'code': exc.code, 'message': str(exc)}}, status=exc.http_status
            )
        except Exception as exc:
            logger.error('图片代理异常: %s', exc, exc_info=True)
            return Response(
                {'error': f'图片代理请求失败: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class VideosGenerationsProxyView(APIView):
    """
    视频统一代理接口
    POST /api/v1/ai/videos/generations
    """

    permission_classes = [permissions.IsAuthenticated]

    def _normalize_video_result(self, result: Any) -> Dict[str, Any]:
        if isinstance(result, AIResponse):
            return {
                'success': result.success,
                'data': result.data if isinstance(result.data, list) else _ensure_list(result.data),
                'metadata': result.metadata,
                'error': result.error,
            }

        if isinstance(result, dict):
            normalized_data = result.get('data', [])
            if not isinstance(normalized_data, list):
                normalized_data = _ensure_list(normalized_data)
            return {
                'success': result.get('success', True),
                'data': normalized_data,
                'metadata': result.get('metadata', {}),
                'error': result.get('error'),
            }

        return {
            'success': False,
            'data': [],
            'metadata': {},
            'error': '无法识别的视频响应格式',
        }

    def post(self, request):
        prompt = request.data.get('prompt', '')
        model = request.data.get('model', '')
        image_inputs = _ensure_list(request.data.get('images') or request.data.get('source_images'))
        image_input = request.data.get('image_url') or request.data.get('image')
        if image_input and image_input not in image_inputs:
            image_inputs.insert(0, image_input)
        image_base64 = request.data.get('image_base64')
        image_base64s = _ensure_list(request.data.get('image_base64s'))

        if not prompt:
            return Response(
                {'error': 'prompt 不能为空'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        provider = _pick_provider('image2video', model)
        if not provider:
            return Response(
                {'error': '没有可用的视频模型提供商'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            duration_seconds = _parse_int(
                request.data.get('duration_seconds'), _parse_int(request.data.get('duration'), 5)
            ) or 5
            sample_count = _parse_int(
                request.data.get('sample_count'), _parse_int(request.data.get('n'), 1)
            ) or 1
            parameters = {
                'prompt': prompt,
                'image_inputs': image_inputs,
                'image_base64': image_base64,
                'image_base64s': image_base64s,
                'image_mime_type': request.data.get('image_mime_type', 'image/jpeg'),
                'duration_seconds': duration_seconds,
                'sample_count': sample_count,
                'aspect_ratio': request.data.get('aspect_ratio') or request.data.get('ratio') or '16:9',
                'resolution': request.data.get('resolution'),
                'seed': _parse_int(request.data.get('seed')),
                'negative_prompt': request.data.get('negative_prompt'),
                'generate_audio': request.data.get('generate_audio', True),
                'camera_movement_description': (
                    request.data.get('camera_movement_description')
                    or request.data.get('cameraMovementDescription')
                    or ''
                ),
            }

            def invoke(selected_provider, call_parameters, repaired_structure=False):
                client = create_ai_client(selected_provider)
                return client._generate_video(
                    prompt=call_parameters['prompt'],
                    model=selected_provider.model_name,
                    image_uri=(
                        call_parameters['image_inputs'][0]
                        if call_parameters['image_inputs'] else ''
                    ),
                    image_uris=call_parameters['image_inputs'],
                    image_base64=call_parameters['image_base64'],
                    image_base64s=call_parameters['image_base64s'],
                    image_mime_type=call_parameters['image_mime_type'],
                    duration_seconds=call_parameters['duration_seconds'],
                    sample_count=call_parameters['sample_count'],
                    aspect_ratio=call_parameters['aspect_ratio'],
                    resolution=call_parameters['resolution'],
                    seed=call_parameters['seed'],
                    negative_prompt=call_parameters['negative_prompt'],
                    generate_audio=call_parameters['generate_audio'],
                    camera_movement_description=call_parameters['camera_movement_description'],
                    project_id=str(request.data.get('project_id') or ''),
                    stage_type='image2video',
                )

            raw_result = _run_guarded(
                request,
                provider,
                'image2video',
                'image2video',
                parameters,
                {
                    'video_seconds': duration_seconds * sample_count,
                    'video_tasks': sample_count,
                    'sample_count': sample_count,
                },
                invoke,
            )
            result = self._normalize_video_result(raw_result)
            if not result['success']:
                return Response(
                    {'error': result['error'] or '视频生成失败'},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

            return Response({
                'id': f'vidgen-{uuid.uuid4().hex[:8]}',
                'object': 'list',
                'created': int(time.time()),
                'model': provider.model_name,
                'provider': _build_provider_payload(provider),
                'data': result['data'],
                'metadata': result['metadata'],
            })
        except ProxyExecutionError as exc:
            return Response(
                {'error': {'code': exc.code, 'message': str(exc)}}, status=exc.http_status
            )
        except Exception as exc:
            logger.error('视频代理异常: %s', exc, exc_info=True)
            return Response(
                {'error': f'视频代理请求失败: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class FileUploadView(APIView):
    """
    文件上传接口
    POST /api/v1/ai/files/upload
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            return Response(
                {'error': '请上传文件（字段名: file）'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        content_type = file.content_type or ''
        filename = file.name

        try:
            file_content = file.read()

            if content_type.startswith('image/'):
                _, relative_path = image_storage.save_file(filename, file_content)
                url_path = f'storage/image/{relative_path}'
            else:
                _, relative_path = video_storage.save_file(filename, file_content)
                url_path = f'storage/video/{relative_path}'

            scheme = request.scheme
            host = request.get_host()
            file_url = f'{scheme}://{host}/{url_path}'

            return Response({
                'id': f'file-{uuid.uuid4().hex[:8]}',
                'filename': filename,
                'url': file_url,
                'size': len(file_content),
                'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            }, status=status.HTTP_201_CREATED)

        except Exception as exc:
            logger.error('文件上传失败: %s', exc, exc_info=True)
            return Response(
                {'error': f'文件上传失败: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
