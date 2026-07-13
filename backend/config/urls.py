"""
主URL配置
遵循REST API最佳实践
"""

from importlib.util import find_spec

from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path, include


def optional_include(route, module_path):
    """Only mount optional closed-source/generated modules when present."""
    if find_spec(module_path) is None:
        return []
    return [path(route, include(module_path))]


urlpatterns = [
    path('admin/', admin.site.urls),
    *optional_include('mcp/', 'apps.mcp.urls'),
    path('api/v1/projects/', include('apps.projects.urls')),
    path('api/v1/prompts/', include('apps.prompts.urls')),
    path('api/v1/models/', include('apps.models.urls')),
    path('api/v1/content/', include('apps.content.urls')),
    path('api/v1/users/', include('apps.users.urls')),
    *optional_include('api/v1/agent/', 'apps.agent.urls'),
    path('api/v1/ai/', include('apps.ai_proxy.urls')),
    path('api/v1/scripts/', include('apps.scripts.urls')),
    path('api/mock/', include('apps.mock_api.urls')),
]

# 开发环境下提供媒体文件和 storage 文件访问
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STORAGE_URL, document_root=settings.STORAGE_ROOT)
