package com.smartcs.mcp.domain.exception;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

/**
 * 错误码与业务异常工厂：校验错误码取值与面向用户的中文文案语义。
 */
class ErrorCodeTest {

    @Test
    void errorCodesAlignWithPlatformHierarchy() {
        assertThat(ErrorCode.INVALID_PARAM.code()).isEqualTo("2001");
        assertThat(ErrorCode.ACCOUNT_NOT_FOUND.code()).isEqualTo("3001");
        assertThat(ErrorCode.AMOUNT_EXCEEDS_OUTSTANDING.code()).isEqualTo("3002");
        assertThat(ErrorCode.LIMIT_EXCEEDED.code()).isEqualTo("3003");
        assertThat(ErrorCode.UNSUPPORTED_PERIOD.code()).isEqualTo("3004");
        assertThat(ErrorCode.UNSUPPORTED_ACTION.code()).isEqualTo("3005");
    }

    @Test
    void factoryMethodsCarryCodeAndUserSafeMessage() {
        assertThat(BusinessException.invalidParam("请提供完整卡号。").getErrorCode())
                .isEqualTo(ErrorCode.INVALID_PARAM);

        BusinessException notFound = BusinessException.accountNotFound("6225880099999999");
        assertThat(notFound.getErrorCode()).isEqualTo(ErrorCode.ACCOUNT_NOT_FOUND);
        assertThat(notFound.getMessage()).contains("未找到").contains("6225880099999999");

        assertThat(BusinessException.amountExceedsOutstanding("8650").getMessage()).contains("超过");
        assertThat(BusinessException.limitExceeded("上限 100000 元").getErrorCode())
                .isEqualTo(ErrorCode.LIMIT_EXCEEDED);
        assertThat(BusinessException.unsupportedPeriod().getMessage()).contains("期数");
        assertThat(BusinessException.unsupportedAction().getMessage()).contains("挂失");
    }
}
