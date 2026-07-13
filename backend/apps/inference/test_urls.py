"""inference 隔离测试无需业务路由。"""

from django.urls import path


urlpatterns = [path('__inference_test__/', lambda request: None)]
