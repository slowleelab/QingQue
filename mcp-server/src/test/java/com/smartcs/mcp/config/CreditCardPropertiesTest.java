package com.smartcs.mcp.config;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Duration;
import org.junit.jupiter.api.Test;

/**
 * 业务参数配置默认值绑定测试：缺省即等价于历史硬编码取值。
 */
class CreditCardPropertiesTest {

    @Test
    void defaultsMatchLegacyHardcodedValues() {
        CreditCardProperties props = new CreditCardProperties();

        assertThat(props.supportedPeriods()).containsExactlyInAnyOrder(3, 6, 12, 24);
        assertThat(props.getInstallmentFeeRates().get(3)).isEqualByComparingTo("0.0060");
        assertThat(props.getInstallmentFeeRates().get(6)).isEqualByComparingTo("0.0060");
        assertThat(props.getInstallmentFeeRates().get(12)).isEqualByComparingTo("0.0066");
        assertThat(props.getInstallmentFeeRates().get(24)).isEqualByComparingTo("0.0072");
        assertThat(props.getTempLimitMultiplier()).isEqualByComparingTo("2.0");
        assertThat(props.getDefaultRepayChannel()).isEqualTo("本人储蓄卡快捷");
        assertThat(props.isLuhnCheck()).isFalse();
        assertThat(props.getIdempotencyTtl()).isEqualTo(Duration.ofMinutes(30));
    }
}
