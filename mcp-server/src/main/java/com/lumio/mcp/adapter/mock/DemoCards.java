package com.lumio.mcp.adapter.mock;

/**
 * 演示卡号常量（<b>构造的假卡号，非真实 PAN</b>）。
 *
 * <p>供 {@link MockDataSeeder} 与单元测试共享，避免散落魔法值。卡号均为 Luhn 合法值，
 * 因此即便开启 {@code lumio.creditcard.luhn-check=true} 也能通过校验。</p>
 */
public final class DemoCards {

    /** 演示主卡（额度 5 万、有当期未还账单）。 */
    public static final String PRIMARY = "6225880012346780";

    /** 演示副卡（额度 2 万、含临时额度、已还清）。 */
    public static final String SECONDARY = "6225880000001231";

    /** 演示新卡（未激活，用于激活/开卡演示）。 */
    public static final String UNACTIVATED = "6225880000007899";

    private DemoCards() {
    }
}
