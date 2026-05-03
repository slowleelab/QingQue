package com.example.customerserver.netty.manager;

import com.example.customerserver.dto.ConnectionStatus;
import com.example.customerserver.registry.ConnectionRegistry;
import io.netty.channel.Channel;
import io.netty.channel.ChannelHandlerContext;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.net.SocketAddress;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 客户端连接管理器
 * 管理本地连接，并同步到 ZooKeeper 连接注册表
 */
@Component
public class ConnectionManager {
    private static final Logger LOGGER = LoggerFactory.getLogger(ConnectionManager.class);

    // 服务ID -> 通道映射
    private final Map<String, Channel> serviceChannels = new ConcurrentHashMap<>();
    // 通道ID -> 服务ID映射
    private final Map<String, String> channelServices = new ConcurrentHashMap<>();
    // 后台节点ID集合
    private final Set<String> backendNodes = ConcurrentHashMap.newKeySet();

    private final ConnectionRegistry connectionRegistry;

    @Autowired
    public ConnectionManager(ConnectionRegistry connectionRegistry) {
        this.connectionRegistry = connectionRegistry;
    }

    /**
     * 注册服务连接
     */
    public void registerConnection(String serviceId, ChannelHandlerContext ctx) {
        Channel channel = ctx.channel();
        String channelId = channel.id().asShortText();

        serviceChannels.put(serviceId, channel);
        channelServices.put(channelId, serviceId);

        // 注册到 ZooKeeper 连接注册表
        connectionRegistry.registerConnection(serviceId);

        LOGGER.info("服务 {} 已注册通道 {}", serviceId, channelId);
        LOGGER.debug("当前连接数: {}", serviceChannels.size());
    }

    /**
     * 移除连接
     */
    public void removeConnection(ChannelHandlerContext ctx) {
        String channelId = ctx.channel().id().asShortText();
        String serviceId = channelServices.remove(channelId);

        if (serviceId != null) {
            serviceChannels.remove(serviceId);

            // 从 ZooKeeper 连接注册表移除
            connectionRegistry.unregisterConnection(serviceId);

            LOGGER.info("服务 {} 已断开连接（通道 {}）", serviceId, channelId);
        } else {
            LOGGER.debug("未知通道 {} 已断开连接", channelId);
        }

        LOGGER.debug("当前连接数: {}", serviceChannels.size());
    }

    /**
     * 根据服务ID移除连接
     */
    public void removeConnection(String serviceId) {
        Channel channel = serviceChannels.remove(serviceId);
        if (channel != null) {
            String channelId = channel.id().asShortText();
            channelServices.remove(channelId);

            // 从 ZooKeeper 连接注册表移除
            connectionRegistry.unregisterConnection(serviceId);

            LOGGER.info("服务 {} 连接已移除", serviceId);
        }
    }

    /**
     * 获取服务的通道
     */
    public Channel getChannel(String serviceId) {
        return serviceChannels.get(serviceId);
    }

    /**
     * 获取通道的服务ID
     */
    public String getServiceId(Channel channel) {
        String channelId = channel.id().asShortText();
        return channelServices.get(channelId);
    }

    /**
     * 根据通道ID获取服务ID
     */
    public String getServiceId(String channelId) {
        return channelServices.get(channelId);
    }

    /**
     * 检查服务是否已连接（本地）
     */
    public boolean isConnected(String serviceId) {
        Channel channel = serviceChannels.get(serviceId);
        return channel != null && channel.isActive();
    }

    /**
     * 检查服务是否已连接（本地或远程）
     * 如果本地未连接，查询 ZooKeeper 获取该客户端连接的路由节点
     *
     * @param serviceId 服务ID
     * @return 如果本地连接返回 true，否则返回 false
     */
    public boolean isConnectedLocally(String serviceId) {
        return isConnected(serviceId);
    }

    /**
     * 获取客户端连接所在的路由节点
     * @param serviceId 服务ID
     * @return 路由节点ID，如果本地连接返回当前路由ID，如果未找到返回 null
     */
    public String getConnectionRouterId(String serviceId) {
        // 先检查本地连接
        if (isConnected(serviceId)) {
            // 本地连接，返回 null 表示本地
            return null;
        }
        // 查询 ZooKeeper
        return connectionRegistry.getConnectionRouter(serviceId);
    }

    /**
     * 获取所有已连接的服务ID
     */
    public java.util.Set<String> getConnectedServices() {
        return java.util.Collections.unmodifiableSet(serviceChannels.keySet());
    }

    /**
     * 获取连接数
     */
    public int getConnectionCount() {
        return serviceChannels.size();
    }

    /**
     * 向服务发送消息
     */
    public boolean sendMessage(String serviceId, Object message) {
        Channel channel = getChannel(serviceId);
        if (channel != null && channel.isActive()) {
            channel.writeAndFlush(message);
            return true;
        }
        return false;
    }

    /**
     * 获取连接详情列表
     */
    public List<ConnectionStatus> getConnectionDetails() {
        List<ConnectionStatus> details = new ArrayList<>();
        for (Map.Entry<String, Channel> entry : serviceChannels.entrySet()) {
            String serviceId = entry.getKey();
            Channel channel = entry.getValue();
            String channelId = channel.id().asShortText();
            String status = channel.isActive() ? "ACTIVE" : "INACTIVE";
            String remoteAddress = getRemoteAddress(channel);
            ConnectionStatus cs = new ConnectionStatus(serviceId, channelId, status, remoteAddress, true);
            details.add(cs);
        }
        return details;
    }

    private String getRemoteAddress(Channel channel) {
        try {
            SocketAddress remoteAddress = channel.remoteAddress();
            return remoteAddress != null ? remoteAddress.toString() : "unknown";
        } catch (Exception e) {
            return "unknown";
        }
    }

    // ========== 坐席后台节点管理方法 ==========

    /**
     * 注册后台节点连接
     */
    public void registerBackendNode(String backendId) {
        backendNodes.add(backendId);
        LOGGER.info("后台节点注册: {}", backendId);
    }

    /**
     * 注销后台节点连接
     */
    public void unregisterBackendNode(String backendId) {
        backendNodes.remove(backendId);
        LOGGER.info("后台节点注销: {}", backendId);
    }

    /**
     * 获取所有后台节点ID
     */
    public Set<String> getBackendNodes() {
        return java.util.Collections.unmodifiableSet(backendNodes);
    }

    /**
     * 检查后台节点是否在线
     */
    public boolean isBackendNodeOnline(String backendId) {
        return backendNodes.contains(backendId) && isConnected(backendId);
    }

    /**
     * 获取后台节点数量
     */
    public int getBackendNodeCount() {
        return backendNodes.size();
    }
}
