package com.lumio.mcp.domain.exception;

/**
 * 业务错误码。
 *
 * <p>对齐平台错误码分层约定：2xxx 输入类、3xxx 业务类。错误码用于日志/指标维度，
 * 不向终端用户泄漏内部规则；面向用户的中文说明由 {@link BusinessException} 携带。</p>
 */
public enum ErrorCode {

    /** 入参非法（格式/取值不合法）。 */
    INVALID_PARAM("2001"),
    /** 账户不存在。 */
    ACCOUNT_NOT_FOUND("3001"),
    /** 金额超过当期尚需偿还金额。 */
    AMOUNT_EXCEEDS_OUTSTANDING("3002"),
    /** 超过风控上限（如临时提额上限）。 */
    LIMIT_EXCEEDED("3003"),
    /** 不支持的分期期数。 */
    UNSUPPORTED_PERIOD("3004"),
    /** 不支持的办理类型。 */
    UNSUPPORTED_ACTION("3005"),
    /** 未找到指定的分期计划。 */
    INSTALLMENT_NOT_FOUND("3006"),
    /** 卡片已激活，无需重复激活。 */
    ALREADY_ACTIVATED("3007"),
    /** 积分余额不足。 */
    POINTS_INSUFFICIENT("3008"),
    /** 该笔交易已申报争议，请勿重复提交。 */
    DISPUTE_ALREADY_FILED("3009");

    private final String code;

    ErrorCode(String code) {
        this.code = code;
    }

    public String code() {
        return code;
    }
}
