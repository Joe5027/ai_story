"""项目统一 DRF 分页。"""

from rest_framework.pagination import PageNumberPagination


class StandardPageNumberPagination(PageNumberPagination):
    """允许控制面在上限内调整页大小，避免前端私自假设无限列表。"""

    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 500
