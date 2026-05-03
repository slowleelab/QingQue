package com.example.transport.netty;

import com.example.transport.connection.Connection;
import com.example.transport.connection.ConnectionListener;
import io.netty.channel.Channel;
import io.netty.channel.ChannelFuture;
import io.netty.channel.ChannelFutureListener;

import java.net.InetSocketAddress;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Netty 连接实现
 */
public class NettyConnection implements Connection {

    private final Channel channel;
    private final String id;
    private final long createTime;
    private volatile long lastActiveTime;
    private final Map<String, Object> attributes = new ConcurrentHashMap<>();
    private final List<ConnectionListener> listeners = new CopyOnWriteArrayList<>();

    public NettyConnection(Channel channel) {
        this.channel = channel;
        this.id = channel.id().asShortText();
        this.createTime = System.currentTimeMillis();
        this.lastActiveTime = this.createTime;
    }

    @Override
    public String getId() {
        return id;
    }

    @Override
    public boolean isActive() {
        return channel != null && channel.isActive();
    }

    @Override
    public void send(Object message) {
        if (!isActive()) {
            throw new IllegalStateException("Connection is not active");
        }
        channel.writeAndFlush(message).addListener((ChannelFutureListener) future -> {
            lastActiveTime = System.currentTimeMillis();
            if (future.isSuccess()) {
                notifyMessageSent(message);
            } else {
                notifyMessageFailed(message, future.cause());
            }
        });
    }

    @Override
    public CompletableFuture<Void> sendAsync(Object message) {
        CompletableFuture<Void> future = new CompletableFuture<>();
        if (!isActive()) {
            future.completeExceptionally(new IllegalStateException("Connection is not active"));
            return future;
        }
        channel.writeAndFlush(message).addListener((ChannelFutureListener) channelFuture -> {
            lastActiveTime = System.currentTimeMillis();
            if (channelFuture.isSuccess()) {
                notifyMessageSent(message);
                future.complete(null);
            } else {
                notifyMessageFailed(message, channelFuture.cause());
                future.completeExceptionally(channelFuture.cause());
            }
        });
        return future;
    }

    @Override
    public void close() {
        if (channel != null && channel.isOpen()) {
            channel.close();
            notifyDisconnected();
        }
    }

    @Override
    public void addListener(ConnectionListener listener) {
        listeners.add(listener);
    }

    @Override
    public void removeListener(ConnectionListener listener) {
        listeners.remove(listener);
    }

    @Override
    public InetSocketAddress getRemoteAddress() {
        if (channel != null && channel.remoteAddress() != null) {
            return (InetSocketAddress) channel.remoteAddress();
        }
        return null;
    }

    @Override
    public Map<String, Object> getAttributes() {
        return new ConcurrentHashMap<>(attributes);
    }

    @Override
    public void setAttribute(String key, Object value) {
        attributes.put(key, value);
    }

    @Override
    public Object getAttribute(String key) {
        return attributes.get(key);
    }

    @Override
    public long getCreateTime() {
        return createTime;
    }

    @Override
    public long getLastActiveTime() {
        return lastActiveTime;
    }

    /**
     * 获取底层 Netty Channel
     */
    public Channel getChannel() {
        return channel;
    }

    /**
     * 通知连接建立
     */
    public void notifyConnected() {
        for (ConnectionListener listener : listeners) {
            listener.onConnected(this);
        }
    }

    /**
     * 通知连接断开
     */
    public void notifyDisconnected() {
        for (ConnectionListener listener : listeners) {
            listener.onDisconnected(this);
        }
    }

    /**
     * 通知连接错误
     */
    public void notifyError(Throwable cause) {
        for (ConnectionListener listener : listeners) {
            listener.onError(this, cause);
        }
    }

    /**
     * 通知收到消息
     */
    public void notifyMessageReceived(Object message) {
        lastActiveTime = System.currentTimeMillis();
        for (ConnectionListener listener : listeners) {
            listener.onMessageReceived(this, message);
        }
    }

    private void notifyMessageSent(Object message) {
        for (ConnectionListener listener : listeners) {
            listener.onMessageSent(this, message);
        }
    }

    private void notifyMessageFailed(Object message, Throwable cause) {
        for (ConnectionListener listener : listeners) {
            listener.onMessageFailed(this, message, cause);
        }
    }

    @Override
    public String toString() {
        return "NettyConnection{" +
                "id='" + id + '\'' +
                ", active=" + isActive() +
                ", remoteAddress=" + getRemoteAddress() +
                '}';
    }
}
