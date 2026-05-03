package com.example.transport.netty;

import com.example.transport.connection.Connection;
import com.example.transport.connection.ConnectionPool;
import io.netty.channel.Channel;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Netty 连接池实现
 */
public class NettyConnectionPool implements ConnectionPool {

    private static final Logger LOGGER = LoggerFactory.getLogger(NettyConnectionPool.class);

    /**
     * 目标ID -> 连接列表（支持多连接）
     */
    private final Map<String, List<Connection>> connections = new ConcurrentHashMap<>();

    /**
     * 连接ID -> 目标ID（反向映射）
     */
    private final Map<String, String> connectionToTarget = new ConcurrentHashMap<>();

    /**
     * 监听器列表
     */
    private final List<ConnectionPool.ConnectionPoolListener> listeners = new CopyOnWriteArrayList<>();

    @Override
    public Connection getConnection(String targetId) {
        List<Connection> targetConnections = connections.get(targetId);
        if (targetConnections != null && !targetConnections.isEmpty()) {
            // 返回第一个活跃连接
            for (Connection conn : targetConnections) {
                if (conn.isActive()) {
                    return conn;
                }
            }
        }
        return null;
    }

    @Override
    public CompletableFuture<Connection> getConnectionAsync(String targetId) {
        return CompletableFuture.completedFuture(getConnection(targetId));
    }

    @Override
    public void registerConnection(String targetId, Connection connection) {
        connections.computeIfAbsent(targetId, k -> new CopyOnWriteArrayList<>()).add(connection);
        connectionToTarget.put(connection.getId(), targetId);
        LOGGER.info("连接注册: targetId={}, connectionId={}", targetId, connection.getId());

        // 通知监听器
        for (ConnectionPool.ConnectionPoolListener listener : listeners) {
            listener.onConnectionCreated(targetId, connection);
        }
    }

    @Override
    public void unregisterConnection(String targetId) {
        List<Connection> removed = connections.remove(targetId);
        if (removed != null) {
            for (Connection conn : removed) {
                connectionToTarget.remove(conn.getId());
                LOGGER.info("连接注销: targetId={}, connectionId={}", targetId, conn.getId());
            }
        }
    }

    @Override
    public boolean hasConnection(String targetId) {
        Connection conn = getConnection(targetId);
        return conn != null && conn.isActive();
    }

    @Override
    public Set<String> getConnectedTargetIds() {
        return connections.keySet();
    }

    @Override
    public int getActiveConnectionCount() {
        int count = 0;
        for (List<Connection> connList : connections.values()) {
            for (Connection conn : connList) {
                if (conn.isActive()) {
                    count++;
                }
            }
        }
        return count;
    }

    @Override
    public boolean sendMessage(String targetId, Object message) {
        Connection connection = getConnection(targetId);
        if (connection != null && connection.isActive()) {
            connection.send(message);
            return true;
        }
        return false;
    }

    @Override
    public void broadcast(Object message) {
        for (List<Connection> connList : connections.values()) {
            for (Connection conn : connList) {
                if (conn.isActive()) {
                    conn.send(message);
                }
            }
        }
    }

    @Override
    public void closeAll() {
        for (List<Connection> connList : connections.values()) {
            for (Connection conn : connList) {
                conn.close();
            }
        }
        connections.clear();
        connectionToTarget.clear();
        LOGGER.info("所有连接已关闭");
    }

    @Override
    public void addListener(ConnectionPool.ConnectionPoolListener listener) {
        listeners.add(listener);
    }

    @Override
    public void removeListener(ConnectionPool.ConnectionPoolListener listener) {
        listeners.remove(listener);
    }

    /**
     * 根据连接ID移除连接
     */
    public void removeConnectionById(String connectionId) {
        String targetId = connectionToTarget.remove(connectionId);
        if (targetId != null) {
            List<Connection> targetConnections = connections.get(targetId);
            if (targetConnections != null) {
                targetConnections.removeIf(conn -> conn.getId().equals(connectionId));
                if (targetConnections.isEmpty()) {
                    connections.remove(targetId);
                }
            }
            LOGGER.info("连接已移除: connectionId={}, targetId={}", connectionId, targetId);
        }
    }

    /**
     * 从 Netty Channel 创建并注册连接
     */
    public Connection registerChannel(String targetId, Channel channel) {
        NettyConnection connection = new NettyConnection(channel);
        registerConnection(targetId, connection);

        // 监听连接关闭事件
        channel.closeFuture().addListener(future -> {
            removeConnectionById(connection.getId());
            for (ConnectionPool.ConnectionPoolListener listener : listeners) {
                listener.onConnectionClosed(targetId, connection);
            }
        });

        return connection;
    }
}
