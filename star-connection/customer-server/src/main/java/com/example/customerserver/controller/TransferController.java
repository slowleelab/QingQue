package com.example.customerserver.controller;

import com.example.common.model.Session;
import com.example.customerserver.dto.CustomerInfo;
import com.example.customerserver.dto.TransferSessionRequest;
import com.example.customerserver.dto.TransferSessionResponse;
import com.example.customerserver.session.SessionManager;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Base64;

/**
 * 转接控制器 — 接受 SmartCS Bot 的会话转接请求
 *
 * <p>SmartCS Bot 在与客户对话后判断需要转人工时，
 * 调用此接口将会话转交给 customer-server 进入人工坐席等待队列。</p>
 */
@RestController
@RequestMapping("/api")
public class TransferController {

    private static final Logger LOGGER = LoggerFactory.getLogger(TransferController.class);
    private final SessionManager sessionManager;

    @Autowired
    public TransferController(SessionManager sessionManager) {
        this.sessionManager = sessionManager;
    }

    /**
     * 创建转接会话
     *
     * <p>接收 SmartCS Bot 的转接请求，在 customer-server 中创建 WAITING 状态的会话，
     * 返回长轮询所需的 poll/send URL 和认证 token。</p>
     *
     * @param request 转接请求，包含机器人侧会话上下文
     * @return 转接会话响应，包含 pollUrl、sendUrl、token
     */
    @PostMapping("/sessions")
    public ResponseEntity<TransferSessionResponse> createSession(
            @RequestBody TransferSessionRequest request
    ) {
        String botSessionId = request.getSessionId();
        LOGGER.info("收到转接请求: botSessionId={}, customerId={}, reason={}",
                botSessionId, request.getCustomerId(), request.getTransferReason());

        // 构造 CustomerInfo 用于 SessionManager 创建会话
        CustomerInfo customerInfo = new CustomerInfo();
        customerInfo.setCustomerId(request.getCustomerId());
        customerInfo.setSource("SMARTCS_BOT");

        // 通过 SessionManager 创建会话（管理完整生命周期）
        Session session = sessionManager.createSession(customerInfo);
        String actualSessionId = session.getSessionId();

        // 生成认证 token（Base64 编码 sessionId:timestamp）
        String token = Base64.getUrlEncoder().encodeToString(
                (actualSessionId + ":" + System.currentTimeMillis()).getBytes()
        );

        // 构建长轮询 URL
        String pollUrl = "http://localhost:8080/customer/poll?session_id=" + actualSessionId + "&token=" + token;
        String sendUrl = "http://localhost:8080/customer/send";

        LOGGER.info("转接会话已创建: sessionId={}, customerId={}, status={}",
                actualSessionId, request.getCustomerId(), session.getStatus());

        return ResponseEntity.ok(new TransferSessionResponse(actualSessionId, pollUrl, sendUrl, token));
    }
}
