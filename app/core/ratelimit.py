from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

_s = get_settings()
limiter = Limiter(key_func=get_remote_address, storage_uri=_s.rate_limit_storage,
                  enabled=_s.rate_limit_enabled)
