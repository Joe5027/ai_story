"""
生产环境配置
"""

from .base import *
from django.core.exceptions import ImproperlyConfigured

DEBUG = False
ALLOWED_HOSTS = ['*']

# CORS配置 - 按需放开来源
CORS_ALLOW_ALL_ORIGINS = True

# 生产批处理需要 PostgreSQL 的行锁与并发预算事务；显式逃生开关只用于
# 单机迁移排障，不能作为高强度生产配置长期使用。
DATABASE_URL = os.getenv('DATABASE_URL', '').strip()
ALLOW_PRODUCTION_SQLITE = os.getenv('ALLOW_PRODUCTION_SQLITE', 'false').lower() == 'true'
if not DATABASE_URL:
    if not ALLOW_PRODUCTION_SQLITE:
        raise ImproperlyConfigured('生产环境必须设置 PostgreSQL DATABASE_URL')
    DATABASE_URL = f"sqlite:///{os.getenv('SQLITE_DB_PATH', str(BASE_DIR / 'data' / 'ai_story.db'))}"

DATABASES = {'default': database_config_from_url(DATABASE_URL)}
if DATABASES['default']['ENGINE'] != 'django.db.backends.postgresql' and not ALLOW_PRODUCTION_SQLITE:
    raise ImproperlyConfigured('生产环境 DATABASE_URL 必须使用 PostgreSQL')

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
        'level': 'INFO',
    },
}
