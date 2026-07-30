package com.smartcs.mcp.domain.exception;

/**
 * 业务异常：携带 {@link ErrorCode} 与面向用户的中文说明。
 *
 * <p>工具方法抛出本异常后，由 Spring AI MCP 框架将 {@link #getMessage()} 作为工具错误结果回传给编排层，
 * 因此消息必须是安全、可直接呈现给客户的中文文案，不得包含内部规则细节或敏感数据。</p>
 */
public class BusinessException extends RuntimeException {

    private final transient ErrorCode errorCode;

    public BusinessException(ErrorCode errorCode, String message) {
        super(message);
        this.errorCode = errorCode;
    }

    public ErrorCode getErrorCode() {
        return errorCode;
    }

    // ── 常用工厂方法 ──

    public static BusinessException invalidParam(String message) {
        return new BusinessException(ErrorCode.INVALID_PARAM, message);
    }

    public static BusinessException accountNotFound(String cardNo) {
        return new BusinessException(ErrorCode.ACCOUNT_NOT_FOUND,
                "未找到卡号 " + cardNo + " 的信用卡账户，请核对后重试。");
    }

    public static BusinessException amountExceedsOutstanding(String outstandingText) {
        return new BusinessException(ErrorCode.AMOUNT_EXCEEDS_OUTSTANDING,
                "分期金额超过当期尚需偿还金额（" + outstandingText + " 元）。");
    }

    public static BusinessException limitExceeded(String message) {
        return new BusinessException(ErrorCode.LIMIT_EXCEEDED, message);
    }

    public static BusinessException unsupportedPeriod() {
        return new BusinessException(ErrorCode.UNSUPPORTED_PERIOD, "不支持的分期期数，仅支持 3、6、12、24 期。");
    }

    public static BusinessException unsupportedAction() {
        return new BusinessException(ErrorCode.UNSUPPORTED_ACTION, "办理类型仅支持「挂失」或「临时冻结」。");
    }

    public static BusinessException installmentNotFound(String planId) {
        return new BusinessException(ErrorCode.INSTALLMENT_NOT_FOUND,
                "未找到分期计划 " + planId + "，或该计划已结清/已取消，请核对后重试。");
    }

    public static BusinessException alreadyActivated() {
        return new BusinessException(ErrorCode.ALREADY_ACTIVATED, "该卡片已处于可用状态，无需重复激活。");
    }

    public static BusinessException pointsInsufficient(long balance) {
        return new BusinessException(ErrorCode.POINTS_INSUFFICIENT,
                "积分余额不足，当前可用积分为 " + balance + " 分。");
    }

    public static BusinessException disputeAlreadyFiled(String txnRef) {
        return new BusinessException(ErrorCode.DISPUTE_ALREADY_FILED,
                "交易 " + txnRef + " 已在处理中的争议申报，请勿重复提交，如需查询进度可联系客服。");
    }
}
