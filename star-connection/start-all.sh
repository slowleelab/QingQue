#!/bin/bash

# Start script for Star Connection Microservice
# Usage: ./start-all.sh [clean]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check if Java 17+ is available
if ! command -v java &> /dev/null; then
    echo "Error: Java is not installed"
    exit 1
fi

JAVA_VERSION=$(java -version 2>&1 | head -1 | cut -d'"' -f2 | cut -d'.' -f1)
if [ "$JAVA_VERSION" -lt 17 ]; then
    echo "Error: Java 17 or higher is required (found Java $JAVA_VERSION)"
    exit 1
fi

# Check if Maven is available
if ! command -v mvn &> /dev/null; then
    echo "Error: Maven is not installed"
    exit 1
fi

# Clean if requested
if [ "$1" = "clean" ]; then
    echo "Cleaning project..."
    mvn clean
fi

# Build project
echo "Building project..."
mvn clean package -DskipTests

# Check if ZooKeeper is running
echo "Checking ZooKeeper..."
if ! nc -z localhost 2181 2>/dev/null; then
    echo "Warning: ZooKeeper is not running on localhost:2181"
    echo "Please start ZooKeeper before continuing:"
    echo "  Option 1: zkServer start"
    echo "  Option 2: docker run --name zookeeper -p 2181:2181 -d zookeeper:3.8"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "Starting Center Node..."
cd center-node
java -jar target/center-node-1.0.0.jar &
CENTER_PID=$!
cd ..

echo "Waiting for Center Node to start..."
sleep 10

echo "Starting Client Node 1..."
cd client-node
java -jar target/client-node-1.0.0.jar --server.port=8081 &
CLIENT1_PID=$!
cd ..

echo "Starting Client Node 2..."
cd client-node
java -jar target/client-node-1.0.0.jar --server.port=8082 --client.service-id=client-2 &
CLIENT2_PID=$!
cd ..

echo "========================================"
echo "Services started:"
echo "  Center Node: PID $CENTER_PID, http://localhost:8080"
echo "  Client Node 1: PID $CLIENT1_PID, http://localhost:8081"
echo "  Client Node 2: PID $CLIENT2_PID, http://localhost:8082"
echo "  ZooKeeper: localhost:2181"
echo ""
echo "To test message sending:"
echo "  curl -X POST http://localhost:8081/api/messages/request \\"
echo "    -H \"Content-Type: application/json\" \\"
echo "    -d '{\"target\": \"client-2\", \"payload\": \"Hello\", \"waitForResponse\": true}'"
echo ""
echo "Press Ctrl+C to stop all services"
echo "========================================"

# Trap Ctrl+C to stop services
trap 'echo "Stopping services..."; kill $CENTER_PID $CLIENT1_PID $CLIENT2_PID 2>/dev/null; exit' INT

# Wait for services
wait $CENTER_PID $CLIENT1_PID $CLIENT2_PID