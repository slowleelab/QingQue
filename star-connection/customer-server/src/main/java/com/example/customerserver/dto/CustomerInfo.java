package com.example.customerserver.dto;

/**
 * 客户信息
 */
public class CustomerInfo {
    private String customerId;
    private String customerName;
    private String source;
    private String metadata;

    public CustomerInfo() {
    }

    public CustomerInfo(String customerId, String customerName) {
        this.customerId = customerId;
        this.customerName = customerName;
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

    @Override
    public String toString() {
        return "CustomerInfo{" +
                "customerId='" + customerId + '\'' +
                ", customerName='" + customerName + '\'' +
                ", source='" + source + '\'' +
                '}';
    }
}
