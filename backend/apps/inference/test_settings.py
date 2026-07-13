"""inference 应用的最小隔离测试设置，不触发项目 Celery 初始化。"""


SECRET_KEY = 'inference-tests-only'
DEBUG = False
USE_TZ = True
TIME_ZONE = 'Asia/Shanghai'
LANGUAGE_CODE = 'zh-hans'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
ROOT_URLCONF = 'apps.inference.test_urls'
MIDDLEWARE = []
TEMPLATES = []

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'apps.models',
    'apps.prompts',
    'apps.scripts',
    'apps.projects',
    'apps.inference',
]
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
        'TEST': {'SERIALIZE': False},
    }
}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'inference-tests',
    }
}
