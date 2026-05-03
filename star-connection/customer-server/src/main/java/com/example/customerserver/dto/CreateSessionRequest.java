package com.example.customerserver.dto;

/**
 * 创建会话请求
 */
public class CreateSessionRequest {
    private String customerId;
    private String customerName;
    private String source;
    private String metadata;

    public CreateSessionRequest() {
    }

    public String getCustomerId() {
        return customerId;
    }

    public void setCustomerId(String customerId) {
        this.customerId = customerId;
    }

    public String getCustomerName() {
        return customerName;
    }

    public void setCustomerName(String customerName) {
        this.customerName = customerName;
    }

    public String getSource() {
        return source;
    }

    public void setSource(String source) {
        this.source = source;
    }

    public String getMetadata() {
        return metadata;
    }

    public void setMetadata(String metadata) {
        this.metadata = metadata;
    }

    public CustomerInfo toCustomerInfo() {
        CustomerInfo info = new CustomerInfo(customerId, customerName);
        info.setSource(source);
        info.setMetadata(metadata);
        return info;
    }

    @Override
    public String toString() {
        return "CreateSessionRequest{" +
                "customerId='" + customerId + '\'' +
                ", customerName='" + customerName + '\'' +
                ", source='" + source + '\'' +
                '}';
    }
}
