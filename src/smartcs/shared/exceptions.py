"""统一异常定义

对应概要设计 §5.1 错误分级：
- 5xxx 系统错误
- 4xxx 外部依赖错误
- 3xxx 业务错误
- 2xxx 输入错误
"""


class SmartCSError(Exception):
    """基础异常"""

    code: int = 5000
    message: str = "系统内部错误"

    def __init__(self, message: str | None = None, code: int | None = None):
        self.message = message or self.message
        self.code = code or self.code
        super().__init__(self.message)


# ── 输入错误 2xxx ──


class IntentUnrecognizedError(SmartCSError):
    """2001: 意图无法识别"""

    code = 2001
    message = "意图无法识别"


class EntityIncompleteError(SmartCSError):
    """2002: 实体抽取不完整"""

    code = 2002
    message = "实体抽取不完整"


class QueryOutOfRangeError(SmartCSError):
    """2003: 查询超出范围"""

    code = 2003
    message = "查询超出范围"


class DocumentFormatError(SmartCSError):
    """2010: 不支持的文档格式"""

    code = 2010
    message = "不支持的文档格式"


# ── 业务错误 3xxx ──


class KnowledgeMissError(SmartCSError):
    """3001: 知识库未命中"""

    code = 3001
    message = "知识库未命中"


class CustomerNotAuthenticatedError(SmartCSError):
    """3002: 客户身份未认证"""

    code = 3002
    message = "客户身份未认证"


class HighRiskBlockedError(SmartCSError):
    """3003: 高风险业务拦截"""

    code = 3003
    message = "高风险业务拦截"


class IngestionConflictError(SmartCSError):
    """3010: 文档正在被处理，拒绝并发写入"""

    code = 3010
    message = "文档正在被处理，拒绝并发写入"


# ── 外部依赖错误 4xxx ──


class LLMTimeoutError(SmartCSError):
    """4001: 大模型推理超时"""

    code = 4001
    message = "大模型推理超时"


class LLMInferenceError(SmartCSError):
    """4002: 大模型推理异常"""

    code = 4002
    message = "大模型推理异常"


class BankAPIError(SmartCSError):
    """4003: 银行 API 调用失败"""

    code = 4003
    message = "银行 API 调用失败"


class VectorSearchError(SmartCSError):
    """4004: 向量检索异常"""

    code = 4004
    message = "向量检索异常"


class EmbeddingServiceError(SmartCSError):
    """4005: 嵌入服务调用失败"""

    code = 4005
    message = "嵌入服务调用失败"


class EmbeddingTimeoutError(SmartCSError):
    """4006: 嵌入服务调用超时"""

    code = 4006
    message = "嵌入服务调用超时"


class MinIOError(SmartCSError):
    """4010: 对象存储读写异常"""

    code = 4010
    message = "对象存储读写异常"


class DualWriteError(SmartCSError):
    """4012: 双写部分失败"""

    code = 4012
    message = "双写部分失败"


# ── 系统错误 5xxx ──


class SessionCorruptedError(SmartCSError):
    """5001: 会话状态损坏"""

    code = 5001
    message = "会话状态损坏"


class ServiceOverloadedError(SmartCSError):
    """5002: 服务过载"""

    code = 5002
    message = "服务过载"
