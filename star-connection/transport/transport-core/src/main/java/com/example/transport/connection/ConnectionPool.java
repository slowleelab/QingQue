package com.example.transport.connection;

import java.util.Set;
import java.util.concurrent.CompletableFuture;

/**
 * 连接池接口
 *
 * <p>管理多个目标节点的连接，提供统一的连接获取、注册和消息发送API。
 * 是transport模块的核心接口之一。</p>
 *
 * <h3>设计理念：</h3>
 * <ul>
 *   <li><b>多目标管理</b>：一个连接池可以管理到多个目标的连接</li>
 *   <li><b>监听器模式</b>：支持监听连接创建、关闭、丢失事件</li>
 *   <li><b>异步支持</b>：提供异步获取连接的能力</li>
 *   <li><b>广播能力</b>：支持向所有连接广播消息</li>
 * </ul>
 *
 * <h3>在客服系统中的应用：</h3>
 * <pre>
 * CF端（客户前置）：
 * ┌────────────────────────────────────────┐
 * │            ConnectionPool              │
 * │  ┌──────────┐  ┌──────────┐           │
 *  │  │ backend-1│  │ backend-2│  ...      │
 *  │  └──────────┘  └──────────┘           │
 *  │         所有AB节点的Netty连接         │
 *  └────────────────────────────────────────┘
 *
 * AB端（坐席后台）：
 * ┌────────────────────────────────────────┐
 * │            ConnectionPool              │
 * │  ┌──────────┐  ┌──────────┐           │
 *  │  │ router-1 │  │ router-2 │  ...      │
 *  │  └──────────┘  └──────────┘           │
 *  │         所有CF节点的Netty连接         │
 *  └────────────────────────────────────────┘
 * </pre>
 *
 * <h3>使用示例：</h3>
 * <pre>{@code
 * // 注册连接
 * connectionPool.registerConnection("router-1", connection);
 *
 * // 检查连接
 * if (connectionPool.hasConnection("router-1")) {
 *     // 发送消息
 *     connectionPool.sendMessage("router-1", message);
 * }
 *
 * // 广播消息
 * connectionPool.broadcast(notification);
 *
 * // 添加监听器
 * connectionPool.addListener(new ConnectionPool.ConnectionPoolListener() {
 *     @Override
 *     public void onConnectionCreated(String targetId, Connection connection) {
 *         System.out.println("新连接: " + targetId);
 *     }
 * });
 * }</pre>
 *
 * <h3>实现类：</h3>
 * <ul>
 *   <li>{@link com.example.transport.netty.NettyConnectionPool} - 基于Netty的实现</li>
 * </ul>
 *
 * @author Customer Service Platform Team
 * @version 1.0.0
 * @see Connection
 * @see ConnectionPoolListener
 */
public interface ConnectionPool {

    /**
     * 获取指定目标的连接
     *
     * @param targetId 目标ID（如：router-1, backend-1）
     * @return 连接实例，如果不存在或已关闭返回null
     */
    Connection getConnection(String targetId);

    /**
     * 异步获取连接
     *
     * <p>当前实现直接返回现有连接，未来可扩展为等待连接建立</p>
     *
     * @param targetId 目标ID
     * @return CompletableFuture，完成时包含连接实例
     */
    CompletableFuture<Connection> getConnectionAsync(String targetId);

    /**
     * 注册连接
     *
     * <p>将连接注册到连接池，与指定的目标ID关联。
     * 注册后会触发监听器的onConnectionCreated回调。</p>
     *
     * @param targetId   目标ID
     * @param connection 连接实例
     */
    void registerConnection(String targetId, Connection connection);

    /**
     * 注销连接
     *
     * <p>从连接池移除指定目标的所有连接。
     * 不会触发监听器回调，需要手动关闭连接。</p>
     *
     * @param targetId 目标ID
     */
    void unregisterConnection(String targetId);

    /**
     * 检查是否有指定目标的活跃连接
     *
     * @param targetId 目标ID
     * @return 如果存在活跃连接返回true
     */
    boolean hasConnection(String targetId);

    /**
     * 获取所有已连接的目标ID
     *
     * @return 目标ID集合
     */
    Set<String> getConnectedTargetIds();

    /**
     * 获取活跃连接数
     *
     * @return 活跃连接数量
     */
    int getActiveConnectionCount();

    /**
     * 发送消息到指定目标
     *
     * <p>便捷方法，自动获取连接并发送消息。</p>
     *
     * @param targetId 目标ID
     * @param message  消息对象
     * @return 如果发送成功返回true，连接不存在或已关闭返回false
     */
    boolean sendMessage(String targetId, Object message);

    /**
     * 广播消息到所有连接
     *
     * <p>向连接池中的所有活跃连接发送消息。
     * 通常用于发送通知、系统消息等。</p>
     *
     * @param message 消息对象
     */
    void broadcast(Object message);

    /**
     * 关闭所有连接
     *
     * <p>遍历关闭所有连接，清空连接池。
     * 通常在应用关闭时调用。</p>
     */
    void closeAll();

    /**
     * 添加连接池监听器
     *
     * @param listener 监听器实例
     */
    void addListener(ConnectionPoolListener listener);

    /**
     * 移除连接池监听器
     *
     * @param listener 监听器实例
     */
    void removeListener(ConnectionPoolListener listener);

    /**
     * 连接池监听器接口
     *
     * <p>用于监听连接池中的连接生命周期事件。</p>
     *
     * <h3>事件触发时机：</h3>
     * <ul>
     *   <li><b>onConnectionCreated</b> - 新连接注册时</li>
     *   <li><b>onConnectionClosed</b> - 连接主动关闭时</li>
     *   <li><b>onConnectionLost</b> - 连接意外丢失时（如网络断开）</li>
     * </ul>
     */
    interface ConnectionPoolListener {

        /**
         * 连接创建时调用
         *
         * @param targetId   目标ID
         * @param connection 连接实例
         */
        default void onConnectionCreated(String targetId, Connection connection) {}

        /**
         * 连接关闭时调用
         *
         * @param targetId   目标ID
         * @param connection 连接实例
         */
        default void onConnectionClosed(String targetId, Connection connection) {}

        /**
         * 连接丢失时调用
         *
         * <p>连接意外断开（非主动关闭）时触发，
         * 可以在此触发重连逻辑。</p>
         *
         * @param targetId   目标ID
         * @param connection 连接实例
         */
        default void onConnectionLost(String targetId, Connection connection) {}
    }
}
