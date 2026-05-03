package com.example.common.codec;

import com.example.common.model.Message;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.netty.buffer.ByteBuf;
import io.netty.channel.ChannelHandlerContext;
import io.netty.handler.codec.ByteToMessageDecoder;
import io.netty.util.CharsetUtil;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;

/**
 * Netty JSON消息解码器
 */
public class JsonMessageDecoder extends ByteToMessageDecoder {
    private static final Logger LOGGER = LoggerFactory.getLogger(JsonMessageDecoder.class);
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private static final int MAX_FRAME_LENGTH = 10 * 1024 * 1024; // 10MB

    @Override
    protected void decode(ChannelHandlerContext ctx, ByteBuf in, List<Object> out) throws Exception {
        // 等待直到有足够的字节读取长度前缀
        if (in.readableBytes() < 4) {
            return;
        }

        in.markReaderIndex();
        int length = in.readInt();

        // 检查帧长度
        if (length <= 0 || length > MAX_FRAME_LENGTH) {
            LOGGER.error("无效的帧长度: {}", length);
            ctx.close();
            return;
        }

        // 检查是否有足够的字节读取完整消息
        if (in.readableBytes() < length) {
            in.resetReaderIndex();
            return;
        }

        // 读取JSON字符串
        ByteBuf frame = in.readSlice(length);
        String json = frame.toString(CharsetUtil.UTF_8);

        try {
            Message message = OBJECT_MAPPER.readValue(json, Message.class);
            out.add(message);
            LOGGER.debug("解码消息: {}", message);
        } catch (Exception e) {
            LOGGER.error("JSON消息解码失败: {}", json, e);
            ctx.fireExceptionCaught(e);
        }
    }

    @Override
    public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) throws Exception {
        LOGGER.error("解码器异常", cause);
        ctx.fireExceptionCaught(cause);
    }
}