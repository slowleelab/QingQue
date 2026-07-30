package com.lumio.mcp.tools;

import com.lumio.mcp.domain.exception.BusinessException;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;

/**
 * 工具层通用入参校验与解析。
 *
 * <p>集中处理完整卡号格式校验、金额/日期/账单周期校验，保证各工具对入参的防御式校验一致，
 * 并统一抛出携带错误码的 {@link BusinessException}（面向用户的中文提示）。</p>
 */
final class ToolSupport {

    private ToolSupport() {
    }

    /**
     * 校验完整卡号：非空、纯数字、13–19 位；{@code luhnCheck=true} 时额外做 Luhn 校验。
     *
     * @return 规整后的卡号
     * @throws BusinessException 校验失败（INVALID_PARAM）
     */
    static String requireCardNo(String cardNo, boolean luhnCheck) {
        if (cardNo == null) {
            throw BusinessException.invalidParam("请提供完整卡号。");
        }
        String trimmed = cardNo.trim();
        if (!trimmed.matches("\\d{13,19}")) {
            throw BusinessException.invalidParam("卡号格式不正确，请提供 13-19 位数字卡号。");
        }
        if (luhnCheck && !luhnValid(trimmed)) {
            throw BusinessException.invalidParam("卡号未通过校验，请核对后重试。");
        }
        return trimmed;
    }

    /**
     * 校验金额为正数。
     *
     * @throws BusinessException 金额非正（INVALID_PARAM）
     */
    static void requirePositiveAmount(double amount) {
        if (amount <= 0) {
            throw BusinessException.invalidParam("金额必须大于 0。");
        }
    }

    /**
     * 校验账单周期格式 yyyy-MM，返回规整后的字符串。
     */
    static String requireBillingCycle(String billingCycle) {
        if (billingCycle == null || !billingCycle.trim().matches("\\d{4}-\\d{2}")) {
            throw BusinessException.invalidParam("账单周期格式不正确，请使用 yyyy-MM 格式，例如 2026-07。");
        }
        return billingCycle.trim();
    }

    /**
     * 解析可选日期（yyyy-MM-dd）；为空返回 defaultValue。
     */
    static LocalDate parseDateOrDefault(String value, LocalDate defaultValue) {
        if (value == null || value.isBlank()) {
            return defaultValue;
        }
        try {
            return LocalDate.parse(value.trim(), DateTimeFormatter.ISO_LOCAL_DATE);
        } catch (DateTimeParseException e) {
            throw BusinessException.invalidParam("日期格式不正确，请使用 yyyy-MM-dd 格式，例如 2026-07-01。");
        }
    }

    /** Luhn 校验算法。 */
    private static boolean luhnValid(String digits) {
        int sum = 0;
        boolean doubling = false;
        for (int i = digits.length() - 1; i >= 0; i--) {
            int d = digits.charAt(i) - '0';
            if (doubling) {
                d *= 2;
                if (d > 9) {
                    d -= 9;
                }
            }
            sum += d;
            doubling = !doubling;
        }
        return sum % 10 == 0;
    }
}
