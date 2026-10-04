class ModuleError(Exception):
    def __init__(self, code, message, reason=None, retryable=False):
        super().__init__(message)
        self.code, self.message = code, message
        self.reason, self.retryable = reason, retryable

    def as_dict(self):
        return dict(code=self.code, message=self.message, retryable=self.retryable,
                    details={'reason': self.reason} if self.reason else None)


def invalid(message):
    return ModuleError('INVALID_REQUEST', message, 'CONFIG_INVALID')


def unavailable(reason, message, retryable=False):
    return ModuleError('PROFILE_UNAVAILABLE', message, reason, retryable)


def database_error(exc):
    """Never return database exception strings (they may contain credentials)."""
    original = getattr(exc, 'orig', exc)
    code = getattr(original, 'sqlstate', None)
    import sqlite3
    if isinstance(original, sqlite3.Error) and (getattr(original, 'sqlite_errorcode', None) == getattr(sqlite3, 'SQLITE_INTERRUPT', 9) or str(original)=='interrupted'):
        return unavailable('METADATA_TIMEOUT', '数据库操作超时', True)
    if code in ('28P01', '28000'):
        return unavailable('AUTHENTICATION_FAILED', '数据库身份验证失败；请核对服务端凭据')
    if code == '42501':
        return unavailable('ACCESS_DENIED', '数据库权限不足')
    if code == '57014':
        return unavailable('METADATA_TIMEOUT', '数据库操作超时', True)
    return unavailable('CONNECTION_FAILED', '数据库连接或结构读取失败；请核对服务端连接、权限与配置')
