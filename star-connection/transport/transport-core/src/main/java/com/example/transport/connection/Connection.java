package com.example.transport.connection;

import java.net.InetSocketAddress;
import java.util.Map;
import java.util.concurrent.CompletableFuture;

/**
 * 连接接口
 *
 * <p>这是传输层的核心抽象接口，表示一个网络连接。
 * 提供统一的连接操作API，底层可以由不同的网络框架实现（如Netty）。</p>
 *
 * <h3>设计理念：</h3>
 * <ul>
 *   <li><b>抽象层</b>：屏蔽底层网络框架差异，便于切换实现</li>
 *   <li><b>监听器模式</b>：支持注册监听器处理连接事件</li>
 *   <li><b>异步优先</b>：提供同步和异步两种发送方式</li>
 *   <li><b>属性存储</b>：支持附加自定义属性</li>
 * </ul>
 *
 * <h3>生命周期：</h3>
 * <pre>
 * ┌──────────┐     ┌──────────┐     ┌──────────┐
 * │ CREATED  │────▶│  ACTIVE  │────▶│  CLOSED  │
 * └──────────┘     └──────────┘     └──────────┘
 *                       │
 *                       │ send()
 *                       │ sendAsync()
 *                       │
 *                       ▼
 *                  ┌──────────┐
 *                  │ 发送消息  │
 *                  └──────────┘
 * </pre>
 *
 * <h3>使用示例：</h3>
 * <pre>{@code
 * // 获取连接
 * Connection conn = connectionPool.getConnection("router-1");
 *
 * // 同步发送
 * conn.send(message);
 *
 * // 异步发送
 * conn.sendAsync(message).thenAccept(v -> {
 *     System.out.println("发送成功");
 * }).exceptionally(e -> {
 *     System.err.println("发送失败: " + e.getMessage());
 *     return null;
 * });
 *
 * // 添加监听器
 * conn.addListener(new ConnectionListener() {
 *     @Override
 *     public void onDisconnected(Connection connection) {
 *         System.out.println("连接断开: " + connection.getId());
 *     }
 * });
 *
 * // 关闭连接
 * conn.close();
 * }</pre>
 *
 * <h3>实现类：</h3>
 * <ul>
 *   <li>{@link com.example.transport.netty.NettyConnection} - 基于Netty的实现</li>
 * </ul>
 *
 * @author Customer Service Platform Team
 * @version 1.0.0
 * @see ConnectionPool
 * @see ConnectionListener
 */
public interface Connection {

    /**
     * 获取连接唯一标识
     *
     * <p>通常使用底层Channel的ID，如Netty的channel.id().asShortText()</p>
     *
     * @return 连接ID
     */
    String getId();

    /**
     * 检查连接是否活跃
     *
     * <p>活跃状态表示连接可以发送消息。
     * 非活跃状态的连接调用send()会抛出异常。</p>
     *
     * @return 如果连接活跃返回true
     */
    boolean isActive();

    /**
     * 同步发送消息
     *
     * <p>阻塞式发送，等待消息写入网络缓冲区。
     * 不保证消息已到达对方，只保证已发送到网络层。</p>
     *
     * @param message 要发送的消息对象
     * @throws IllegalStateException 如果连接不活跃
     * @throws RuntimeException 如果发送失败
     */
    void send(Object message);

    /**
     * 异步发送消息
     *
     * <p>非阻塞式发送，立即返回CompletableFuture。
     * 可以通过Future获取发送结果或处理异常。</p>
     *
     * @param message 要发送的消息对象
     * @return CompletableFuture，发送成功时完成，失败时包含异常
     */
    CompletableFuture<Void> sendAsync(Object message);

    /**
     * 关闭连接
     *
     * <p>优雅关闭连接，会触发所有监听器的onDisconnected回调。
     * 多次调用是安全的。</p>
     */
    void close();

    /**
     * 添加连接监听器
     *
     * @param listener 监听器实例
     */
    void addListener(ConnectionListener listener);

    /**
     * 移除连接监听器
     *
     * @param listener 监听器实例
     */
    void removeListener(ConnectionListener listener);

    /**
     * 获取远程地址
     *
     * @return 远程SocketAddress，如果连接已关闭可能返回null
     */
    InetSocketAddress getRemoteAddress();

    /**
     * 获取所有连接属性
     *
     * <p>返回属性的副本，修改不影响原属性</p>
     *
     * @return 属性Map副本
     */
    Map<String, Object> getAttributes();

    /**
     * 设置连接属性
     *
     * <p>可用于存储连接相关的元数据，如：
     * <ul>
     *   <li>serviceId - 服务ID</li>
     *   <li>authenticated - 是否已认证</li>
     *   <li>lastHeartbeat - 最后心跳时间</li>
     * </ul></p>
     *
     * @param key   属性键
     * @param value 属性值
     */
    void setAttribute(String key, Object value);

    /**
     * 获取连接属性
     *
     * @param key 属性键
     * @return 属性值，不存在返回null
     */
    Object getAttribute(String key);

    /**
     * 获取连接创建时间
     *
     * @return 创建时间戳（毫秒）
     */
    long getCreateTime();

    /**
     * 获取最后活动时间
     *
     * <p>每次发送或接收消息时更新</p>
     *
     * @return 最后活动时间戳（毫秒）
     */
    long getLastActiveTime();
}
