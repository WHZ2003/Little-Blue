"""Public error messages built only from trusted local values, never API bodies."""
import httpx


class BudgetExceeded(RuntimeError):
    def __init__(self, dimension, used, limit, required=0):
        self.dimension = dimension
        if dimension == 'requests':
            message = f'今日本地 API 请求次数已达上限（{used}/{limit}）。次日自动重置，也可在桌面设置调整。'
        else:
            message = (f'本次回复被本地 Token 预算拦截：已计入 {used}/{limit}，本次需预留 {required}。'
                       '次日自动重置，也可在桌面设置调整；不是微信连接故障。')
        super().__init__(message)


class CognitiveError(RuntimeError):
    """Only construct with application-authored text or an integer HTTP status."""


def public_failure(error):
    if isinstance(error, (BudgetExceeded, CognitiveError)):
        return str(error)
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return 'DeepSeek 回复超时，消息已收到；稍后可以再试。'
    if isinstance(error, httpx.RequestError):
        return '核心暂时无法连接 DeepSeek；请检查核心的网络连接。'
    return '暂时无法回复，请检查桌面端的联网设置、预算及网络。'
