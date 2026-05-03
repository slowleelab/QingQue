package com.example.customerserver.session;

import com.example.common.model.Session;

import java.util.List;
import java.util.Optional;

/**
 * 会话存储接口
 */
public interface SessionStore {

    /**
     * 保存会话
     */
    void save(Session session);

    /**
     * 根据会话ID获取会话
     */
    Optional<Session> findById(String sessionId);

    /**
     * 根据客户ID获取会话
     */
    Optional<Session> findByCustomerId(String customerId);

    /**
     * 根据坐席ID获取会话列表
     */
    List<Session> findByAgentId(String agentId);

    /**
     * 根据状态获取会话列表
     */
    List<Session> findByStatus(com.example.common.model.SessionStatus status);

    /**
     * 删除会话
     */
    void delete(String sessionId);

    /**
     * 获取所有会话
     */
    List<Session> findAll();

    /**
     * 获取会话数量
     */
    int count();

    /**
     * 根据坐席ID获取活跃会话数量
     */
    int countByAgentId(String agentId);

    /**
     * 根据时间范围获取会话列表
     */
    List<Session> findByTimeRange(long startTime, long endTime);

    /**
     * 根据条件查询会话（支持模糊匹配会话ID和客户ID）
     */
    List<Session> query(String sessionId, String customerId, String agentId,
                        String status, Long startTime, Long endTime);
}
