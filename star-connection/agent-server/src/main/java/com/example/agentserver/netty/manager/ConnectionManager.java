package com.example.agentserver.netty.manager;

import io.netty.channel.Channel;

import java.util.Set;

/**
 * 连接管理器接口
 * 支持单路由和多路由两种模式
 */
public interface ConnectionManager {

    /**
     * 连接到路由节点
     */
    void connect();

    /**
     * 断开连接
     */
    void disconnect();

    /**
     * 检查是否已连接
     */
    boolean isConnected();

    /**
     * 获取当前通道（单路由模式）
     * @return 通道，多路由模式可能返回 null
     */
    Channel getChannel();

    /**
     * 发送消息
     * @param message 消息对象
     */
    void sendMessage(Object message);

    /**
     * 发送消息到指定目标
     * @param targetServiceId 目标服务ID
     * @param message 消息对象
     * @return 是否发送成功
     */
    default boolean sendMessage(String targetServiceId, Object message) {
        sendMessage(message);
        return isConnected();
    }

    /**
     * 获取当前连接的路由节点ID
     * @return 路由节点ID
     */
    String getCurrentRouterId();

    /**
     * 获取当前连接的路由节点地址
     * @return 路由节点地址 (host:port)
     */
    String getCurrentRouterAddress();

    /**
     * 获取已连接的路由节点数量
     */
    default int getConnectedRouterCount() {
        return isConnected() ? 1 : 0;
    }

    /**
     * 获取已连接的路由节点ID列表
     */
    default Set<String> getConnectedRouterIds() {
        String routerId = getCurrentRouterId();
        if (routerId != null) {
            return Set.of(routerId);
        }
        return Set.of();
    }
}
