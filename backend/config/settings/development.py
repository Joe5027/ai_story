"""
开发环境配置
"""

import sys

from .base import *
# CELERY_ALWAYS_EAGER = True
# CELERY_TASK_ALWAYS_EAGER = True

DEBUG = True
ALLOWED_HOSTS = ['*']

# CORS配置 - 开发环境允许所有源
CORS_ALLOW_ALL_ORIGINS = True

# 数据库 - 开发环境使用SQLite
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': resolve_sqlite_path(os.getenv('SQLITE_DB_PATH')),
    }
}

# Django 单元/契约测试必须能在没有 Redis 的开发机上运行；真实 Pub/Sub 测试
# 仍由 validate-streaming-local.cjs --require-redis 单独负责。这里仅替换 Django
# 辅助缓存，不改变 Celery broker、生产配置或 Redis 专项验证。
if (
    len(sys.argv) > 1
    and sys.argv[1] == 'test'
    and os.getenv('DJANGO_TEST_USE_REDIS', '0') != '1'
):
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'ai-story-django-tests',
        }
    }
    CELERY_BROKER_URL = 'memory://'
    CELERY_RESULT_BACKEND = 'cache+memory://'

# 日志配置
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'sensitive_data': {'()': 'core.logging_filters.SensitiveDataFilter'},
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'filters': ['sensitive_data'],
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'DEBUG',
    },
}
JIANYING_DRAFT_FOLDER = "D:\JianyingPro Drafts"
