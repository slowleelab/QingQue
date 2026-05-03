package com.example.agentserver.assist;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.util.Map;
import java.util.concurrent.CompletionStage;
import java.util.function.Consumer;

public class AssistClient {
    private static final Logger log = LoggerFactory.getLogger(AssistClient.class);
    private static final String ASSIST_WS_URL = "ws://localhost:8001/api/ws/";

    private final String sessionId;
    private WebSocket ws;
    private final ObjectMapper mapper = new ObjectMapper();
    private Consumer<String> onPushCallback;

    public AssistClient(String sessionId) {
        this.sessionId = sessionId;
    }

    public void setOnPushCallback(Consumer<String> callback) {
        this.onPushCallback = callback;
    }

    public void connect() throws Exception {
        HttpClient client = HttpClient.newHttpClient();
        URI uri = URI.create(ASSIST_WS_URL + sessionId);
        ws = client.newWebSocketBuilder()
            .buildAsync(uri, new WebSocket.Listener() {
                @Override
                public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
                    log.debug("Assist push for {}: {}", sessionId, data);
                    if (onPushCallback != null) {
                        onPushCallback.accept(data.toString());
                    }
                    return WebSocket.Listener.super.onText(webSocket, data, last);
                }

                @Override
                public void onOpen(WebSocket webSocket) {
                    log.info("AssistClient connected for session {}", sessionId);
                    WebSocket.Listener.super.onOpen(webSocket);
                }

                @Override
                public void onError(WebSocket webSocket, Throwable error) {
                    log.warn("AssistClient error for {}: {}", sessionId, error.getMessage());
                    WebSocket.Listener.super.onError(webSocket, error);
                }
            }).get();
    }

    @SuppressWarnings("unchecked")
    public void sendCustomerMessage(String message, String intent, String sentiment) throws Exception {
        if (ws == null) return;
        Map<String, Object> msg = Map.of(
            "type", "customer_message",
            "message", message,
            "intent", intent,
            "sentiment", sentiment
        );
        String json = mapper.writeValueAsString(msg);
        ws.sendText(json, true);
    }

    public void disconnect() {
        if (ws != null && !ws.isOutputClosed()) {
            ws.sendClose(1000, "session ended");
        }
    }
}
