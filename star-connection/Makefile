# Makefile for Customer Service Platform

.PHONY: all clean build test run-frontend run-backend doc

all: build

clean:
	@echo "Cleaning project..."
	@mvn clean

build:
	@echo "Building project..."
	@mvn clean package -DskipTests

test:
	@echo "Running tests..."
	@mvn test

run-frontend:
	@echo "Starting Customer Frontend..."
	@cd customer-frontend && java -jar target/customer-frontend-1.0.0.jar

run-backend1:
	@echo "Starting Agent Backend 1 (port 8081)..."
	@cd agent-backend && java -jar target/agent-backend-1.0.0.jar --server.port=8081

run-backend2:
	@echo "Starting Agent Backend 2 (port 8082)..."
	@cd agent-backend && java -jar target/agent-backend-1.0.0.jar --server.port=8082 --agent-backend.service-id=agent-backend-2

run-all: build
	@echo "Starting all services..."
	@./start-all.sh

stop-all:
	@echo "Stopping all services..."
	@./stop-all.sh

doc:
	@echo "View design documentation..."
	@echo "Open DESIGN.md or CLAUDE.md in your favorite markdown editor"

help:
	@echo "Customer Service Platform Build System"
	@echo ""
	@echo "Targets:"
	@echo "  all           - Build project (default)"
	@echo "  clean         - Clean project"
	@echo "  build         - Build project with tests skipped"
	@echo "  test          - Run tests"
	@echo "  run-frontend  - Start customer frontend (port 8080)"
	@echo "  run-backend1  - Start agent backend 1 (port 8081)"
	@echo "  run-backend2  - Start agent backend 2 (port 8082)"
	@echo "  run-all       - Build and start all services"
	@echo "  stop-all      - Stop all services"
	@echo "  doc           - View design documentation"
	@echo "  help          - Show this help message"
	@echo ""
	@echo "Examples:"
	@echo "  make build test    # Build and test"
	@echo "  make run-all       # Start complete system"
