"""日志凭据脱敏过滤器。"""

import logging

from apps.inference.services.security import mask_sensitive_data


class SensitiveDataFilter(logging.Filter):
    """在格式化前清除常见 Key、Token、密码和带密钥查询参数。"""

    def filter(self, record):
        try:
            record.msg = mask_sensitive_data(record.getMessage())
            record.args = ()
        except Exception:
            # 脱敏器自身不能阻断业务日志；失败时给出不包含原消息的固定文本。
            record.msg = '[日志消息因脱敏失败已隐藏]'
            record.args = ()
        return True
