package com.example.agentserver;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * 坐席服务节点应用程序
 */
@SpringBootApplication
@EnableScheduling
public class AgentServerApplication {
    public static void main(String[] args) {
        SpringApplication.run(AgentServerApplication.class, args);
    }
}