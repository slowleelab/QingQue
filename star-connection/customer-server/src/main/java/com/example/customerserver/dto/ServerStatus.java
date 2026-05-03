package com.example.customerserver.dto;

/**
 * 服务器状态信息
 */
public class ServerStatus {
    private int port;
    private String status;
    private long startTime;
    private long uptimeMillis;
    private String uptime;

    public ServerStatus() {
    }

    public ServerStatus(int port, String status, long startTime) {
        this.port = port;
        this.status = status;
        this.startTime = startTime;
    }

    public int getPort() {
        return port;
    }

    public void setPort(int port) {
        this.port = port;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public long getStartTime() {
        return startTime;
    }

    public void setStartTime(long startTime) {
        this.startTime = startTime;
    }

    public long getUptimeMillis() {
        return uptimeMillis;
    }

    public void setUptimeMillis(long uptimeMillis) {
        this.uptimeMillis = uptimeMillis;
        this.uptime = formatUptime(uptimeMillis);
    }

    public String getUptime() {
        return uptime;
    }

    private String formatUptime(long millis) {
        long seconds = millis / 1000;
        long minutes = seconds / 60;
        long hours = minutes / 60;
        long days = hours / 24;

        if (days > 0) {
            return String.format("%d天 %d小时 %d分钟", days, hours % 24, minutes % 60);
        } else if (hours > 0) {
            return String.format("%d小时 %d分钟 %d秒", hours % 24, minutes % 60, seconds % 60);
        } else if (minutes > 0) {
            return String.format("%d分钟 %d秒", minutes, seconds % 60);
        } else {
            return String.format("%d秒", seconds);
        }
    }
}
