package com.example.common.codec;

import com.example.common.model.Message;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import io.netty.channel.ChannelHandlerContext;
import io.netty.handler.codec.MessageToByteEncoder;
import io.netty.util.CharsetUtil;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Netty JSON消息编码器
 */
public class JsonMessageEncoder extends MessageToByteEncoder<Message> {
    private static final Logger LOGGER = LoggerFactory.getLogger(JsonMessageEncoder.class);
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    @Override
    protected void encode(ChannelHandlerContext ctx, Message msg, ByteBuf out) throws Exception {
        try {
            // 将消息转换为JSON
            String json = OBJECT_MAPPER.writeValueAsString(msg);
            byte[] bytes = json.getBytes(CharsetUtil.UTF_8);

            // 写入长度前缀（4字节）后跟JSON数据
            out.writeInt(bytes.length);
            out.writeBytes(bytes);

            LOGGER.debug("编码消息: {}", msg);
        } catch (Exception e) {
            LOGGER.error("消息编码失败: {}", msg, e);
            ctx.fireExceptionCaught(e);
        }
    }

    @Override
    public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) throws Exception {
        LOGGER.error("编码器异常", cause);
        ctx.fireExceptionCaught(cause);
    }
}