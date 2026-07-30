package com.lumio.mcp.domain.support;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.concurrent.ThreadLocalRandom;

/**
 * 受理流水号与日志辅助工具。
 *
 * <p>流水号仅用于演示（非真实交易号）；{@link #tail(String)} 用于日志卫生——
 * 日志中只记录卡号尾号，避免完整卡号进入日志系统。</p>
 */
public final class Ids {

    private Ids() {
    }

    /**
     * 生成受理流水号：前缀 + 日期 + 6 位随机数。
     */
    public static String referenceNo(String prefix) {
        int suffix = ThreadLocalRandom.current().nextInt(100000, 1000000);
        return "%s%s%d".formatted(prefix, LocalDate.now().format(DateTimeFormatter.BASIC_ISO_DATE), suffix);
    }

    /**
     * 取卡号尾号（末 4 位）用于日志展示；不足 4 位时原样返回。
     */
    public static String tail(String cardNo) {
        if (cardNo == null) {
            return "----";
        }
        int len = cardNo.length();
        return len <= 4 ? cardNo : cardNo.substring(len - 4);
    }
}
