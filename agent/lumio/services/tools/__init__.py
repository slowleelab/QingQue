"""参考工具层（reference tools）

本包提供一个**参考 MCP Server**，用可运行的示例信用卡工具演示 P0 工具层的端到端能力：
在没有 Java Spring AI Alibaba MCP Server / Higress 网关的情况下，也能让 Python 编排侧
的 ``MCPToolClient`` / ``ToolCallingExecutor`` 真实跑通「LLM ↔ MCP 工具」多轮循环与
敏感操作确认。

注意：这里的工具返回的是**mock 数据**，仅用于本地联调与集成测试，不接真实银行核心系统。
"""

from __future__ import annotations
