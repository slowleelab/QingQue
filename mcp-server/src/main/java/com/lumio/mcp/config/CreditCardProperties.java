package com.lumio.mcp.config;

import java.math.BigDecimal;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 信用卡业务参数配置（{@code lumio.creditcard.*}）。
 *
 * <p>将费率、风控倍数、默认渠道、幂等 TTL 等业务参数从代码中外置，便于按环境调整而无需改代码。
 * 均提供合理默认值，缺省即等价于历史硬编码取值。</p>
 */
@ConfigurationProperties(prefix = "lumio.creditcard")
public class CreditCardProperties {

    /** 分期期数 → 每期手续费率。默认 3/6 期 0.60%、12 期 0.66%、24 期 0.72%。 */
    private Map<Integer, BigDecimal> installmentFeeRates = defaultFeeRates();

    /** 临时提额上限相对固定额度的倍数（风控上限）。默认 2.0。 */
    private BigDecimal tempLimitMultiplier = new BigDecimal("2.0");

    /** 默认还款渠道。 */
    private String defaultRepayChannel = "本人储蓄卡快捷";

    /** 是否对卡号启用 Luhn 校验。默认关闭（仅校验位数与数字）。 */
    private boolean luhnCheck = false;

    /** 幂等结果保留时长。默认 30 分钟。 */
    private Duration idempotencyTtl = Duration.ofMinutes(30);

    private static Map<Integer, BigDecimal> defaultFeeRates() {
        Map<Integer, BigDecimal> rates = new LinkedHashMap<>();
        rates.put(3, new BigDecimal("0.0060"));
        rates.put(6, new BigDecimal("0.0060"));
        rates.put(12, new BigDecimal("0.0066"));
        rates.put(24, new BigDecimal("0.0072"));
        return rates;
    }

    /** 支持的分期期数（由费率表键集派生）。 */
    public Set<Integer> supportedPeriods() {
        return installmentFeeRates.keySet();
    }

    public Map<Integer, BigDecimal> getInstallmentFeeRates() {
        return installmentFeeRates;
    }

    public void setInstallmentFeeRates(Map<Integer, BigDecimal> installmentFeeRates) {
        this.installmentFeeRates = installmentFeeRates;
    }

    public BigDecimal getTempLimitMultiplier() {
        return tempLimitMultiplier;
    }

    public void setTempLimitMultiplier(BigDecimal tempLimitMultiplier) {
        this.tempLimitMultiplier = tempLimitMultiplier;
    }

    public String getDefaultRepayChannel() {
        return defaultRepayChannel;
    }

    public void setDefaultRepayChannel(String defaultRepayChannel) {
        this.defaultRepayChannel = defaultRepayChannel;
    }

    public boolean isLuhnCheck() {
        return luhnCheck;
    }

    public void setLuhnCheck(boolean luhnCheck) {
        this.luhnCheck = luhnCheck;
    }

    public Duration getIdempotencyTtl() {
        return idempotencyTtl;
    }

    public void setIdempotencyTtl(Duration idempotencyTtl) {
        this.idempotencyTtl = idempotencyTtl;
    }
}
