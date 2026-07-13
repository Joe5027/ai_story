"""
ASGI配置
用于异步Web服务器
"""

import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

django_application = get_asgi_application()

from apps.projects.asgi_sse import ProjectSSEASGIApplication  # noqa: E402

application = ProjectSSEASGIApplication(django_application)
