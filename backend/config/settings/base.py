"""
Django基础配置
遵循SOLID原则,使用分层设置
"""

import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# 安全配置
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'django-insecure-dev-key-change-in-production')
DEBUG = True
ALLOWED_HOSTS = []

# 应用定义
INSTALLED_APPS = [

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # 第三方应用
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'django_celery_beat',

    # 本地应用
    'apps.projects',
    'apps.prompts',
    'apps.models',
    'apps.inference',
    'apps.content',
    'apps.users',
    'apps.mock_api',
    'apps.agent',
    'apps.mcp',
    'apps.ai_proxy',
    'apps.scripts',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# 数据库配置


def resolve_sqlite_path(value=None):
    """把 SQLite 相对路径稳定地解析到项目目录，并确保父目录存在。

    开发命令既可能从仓库根目录运行，也可能从 ``backend`` 运行，不能让
    相同环境变量因当前工作目录不同而落到两套数据库。
    """

    raw_path = value or str(BASE_DIR / 'data' / 'ai_story.db')
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        parts = [part.lower() for part in path.parts]
        if parts and parts[0] == 'backend':
            path = BASE_DIR.parent / path
        else:
            path = BASE_DIR / path
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': resolve_sqlite_path(os.getenv('SQLITE_DB_PATH')),
    }
}


def database_config_from_url(database_url):
    """将 DATABASE_URL 转换为 Django 3.2 数据库配置。

    这里只支持本项目明确使用的 SQLite 与 PostgreSQL，避免引入一个仅为
    解析连接串而存在的运行时依赖。生产环境会在 production.py 中进一步
    限制为 PostgreSQL，开发环境仍固定使用 SQLite。
    """
    parsed = urlparse(database_url)
    if parsed.scheme in {'postgres', 'postgresql'}:
        options = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        return {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': unquote(parsed.path.lstrip('/')),
            'USER': unquote(parsed.username or ''),
            'PASSWORD': unquote(parsed.password or ''),
            'HOST': parsed.hostname or 'localhost',
            'PORT': parsed.port or 5432,
            'CONN_MAX_AGE': int(os.getenv('DATABASE_CONN_MAX_AGE', '60')),
            'OPTIONS': options,
        }
    if parsed.scheme == 'sqlite':
        sqlite_path = unquote(parsed.path or '')
        if parsed.netloc:
            sqlite_path = f'//{parsed.netloc}{sqlite_path}'
        return {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': resolve_sqlite_path(sqlite_path or None),
        }
    raise ValueError('DATABASE_URL 仅支持 sqlite、postgres 或 postgresql 协议')

# 密码验证
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# 国际化
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

# 静态文件
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# 媒体文件
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Storage文件 (用于存储生成的图片、视频等)
STORAGE_URL = 'storage/'
STORAGE_ROOT = BASE_DIR.parent / 'storage'  # 仓库根目录的storage文件夹

# 默认主键字段
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework配置
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'core.pagination.StandardPageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'EXCEPTION_HANDLER': 'core.api_exception_handler.safe_exception_handler',
}

# Redis配置 - 使用不同的数据库避免冲突
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))

# Celery配置
BROKER_URL = os.getenv('CELERY_BROKER_URL', f'redis://{REDIS_HOST}:{REDIS_PORT}/0')
CELERY_BROKER_URL = BROKER_URL
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', f'redis://{REDIS_HOST}:{REDIS_PORT}/1')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_VISIBILITY_TIMEOUT = int(os.getenv('CELERY_BROKER_VISIBILITY_TIMEOUT', 3600 * 3))  # 3小时，需大于最长任务执行时间
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_ROUTES = {
    'apps.inference.tasks.dispatch_work_item': {'queue': 'orchestration'},
    'apps.inference.tasks.enqueue_work_item': {'queue': 'orchestration'},
    'apps.inference.tasks.cancel_work_item': {'queue': 'orchestration'},
    'apps.inference.tasks.reconcile_work_items': {'queue': 'maintenance'},
    'apps.inference.tasks.cleanup_expired_artifacts': {'queue': 'maintenance'},
    'apps.projects.tasks.execute_llm_stage': {'queue': 'llm'},
    'apps.projects.tasks.execute_text2image_stage': {'queue': 'image'},
    'apps.projects.tasks.execute_multi_grid_image_stage': {'queue': 'image'},
    'apps.projects.tasks.execute_image_edit_stage': {'queue': 'image'},
    'apps.projects.tasks.execute_image2video_stage': {'queue': 'video'},
}
CELERY_BEAT_SCHEDULE = {
    'reconcile-generation-work-items': {
        'task': 'apps.inference.tasks.reconcile_work_items',
        'schedule': 60.0,
    },
    'cleanup-expired-ai-artifacts': {
        'task': 'apps.inference.tasks.cleanup_expired_artifacts',
        'schedule': 3600.0,
    },
}

# Redis Pub/Sub配置 (用于实时流式推送)
REDIS_PUBSUB_URL = os.getenv('REDIS_PUBSUB_URL', f'redis://{REDIS_HOST}:{REDIS_PORT}/2')  # 数据库2: Pub/Sub专用

# 混合推理控制面默认关闭，先通过影子模式验证新旧路由决策一致性。
AI_ROUTER_V2_ENABLED = os.getenv('AI_ROUTER_V2_ENABLED', 'false').lower() == 'true'
AI_ROUTER_V2_SHADOW_MODE = os.getenv('AI_ROUTER_V2_SHADOW_MODE', 'true').lower() == 'true'
AI_ENFORCE_PAID_PROVIDER_GUARD = (
    os.getenv('AI_ENFORCE_PAID_PROVIDER_GUARD', 'true').lower() == 'true'
)
AI_WORK_ITEM_EVENTS_ENABLED = os.getenv('AI_WORK_ITEM_EVENTS_ENABLED', 'true').lower() == 'true'
AI_DISTRIBUTED_RESOURCE_LEASES_ENABLED = (
    os.getenv('AI_DISTRIBUTED_RESOURCE_LEASES_ENABLED', 'true').lower() == 'true'
)
AI_RUNTIME_ROOT = os.getenv('AI_RUNTIME_ROOT', r'E:\AI\ai-story-runtime')
AI_RUNTIME_AGENT_URL = os.getenv('AI_RUNTIME_AGENT_URL', 'http://127.0.0.1:9100').rstrip('/')
AI_RUNTIME_AGENT_TOKEN = os.getenv('AI_RUNTIME_AGENT_TOKEN', '')
AI_INTERMEDIATE_RETENTION_DAYS = int(os.getenv('AI_INTERMEDIATE_RETENTION_DAYS', '30'))
AI_ARTIFACT_CLEANUP_ENABLED = os.getenv('AI_ARTIFACT_CLEANUP_ENABLED', 'false').lower() == 'true'

# CORS配置
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [
    "http://127.0.0.1:3000",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

AGENT_SERVER_BASE_URL = os.getenv('AGENT_SERVER_BASE_URL', 'http://127.0.0.1:9002').strip()
AGENT_SERVER_USERNAME = os.getenv('AGENT_SERVER_USERNAME', 'opencode').strip()
AGENT_SERVER_PASSWORD = os.getenv('AGENT_SERVER_PASSWORD', '').strip()
MCP_ACCESS_TOKEN = os.getenv('MCP_ACCESS_TOKEN', 'test').strip()
AGENT_MODEL_PROVIDER_ID = os.getenv('AGENT_MODEL_PROVIDER_ID', 'opencode').strip()
AGENT_MODEL_ID = os.getenv('AGENT_MODEL_ID', 'big-pickle').strip()
AGENT_MODEL_VARIANT = os.getenv('AGENT_MODEL_VARIANT', '').strip()
AGENT_REMOTE_AGENT_NAME = os.getenv('AGENT_REMOTE_AGENT_NAME', 'build').strip()
AGENT_SHOW_FREE_MODELS = os.getenv('AGENT_SHOW_FREE_MODELS', 'false').strip()
OPENCODE_CONFIG_FILE = os.getenv('OPENCODE_CONFIG_FILE', str(Path.home() / '.config' / 'opencode' / 'opencode.json')).strip()
OPENCODE_MANAGED_PROVIDER_PREFIX = os.getenv('OPENCODE_MANAGED_PROVIDER_PREFIX', 'ai_story').strip()
OPENCODE_DEFAULT_PROVIDER_NPM = os.getenv('OPENCODE_DEFAULT_PROVIDER_NPM', '@ai-sdk/openai-compatible').strip()

# JWT配置
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=8),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,

    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'VERIFYING_KEY': None,
    'AUDIENCE': None,
    'ISSUER': None,

    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',

    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    'TOKEN_TYPE_CLAIM': 'token_type',

    'JTI_CLAIM': 'jti',
}

# 缓存配置
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': f'redis://{REDIS_HOST}:{REDIS_PORT}/4',  # 数据库4: Django缓存
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        }
    }
}
