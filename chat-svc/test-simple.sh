#!/bin/bash

# Simple test script for Star Connection Microservice (without ZooKeeper)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "Testing Star Connection Microservice"
echo "========================================"

# Check if services are already running
if lsof -ti:8888,8080,8081 > /dev/null 2>&1; then
    echo "Some ports are already in use:"
    lsof -ti:8888,8080,8081 | xargs ps -o pid,command -p 2>/dev/null || true
    read -p "Kill existing processes? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        lsof -ti:8888,8080,8081 | xargs kill -9 2>/dev/null || true
        sleep 2
    else
        echo "Aborting test"
        exit 1
    fi
fi

# Start Center Node
echo "Starting Center Node..."
cd center-node
java -jar target/center-node-1.0.0.jar > center.log 2>&1 &
CENTER_PID=$!
cd ..

echo "Waiting for Center Node to start (5 seconds)..."
sleep 5

# Check if Center Node started successfully
if ! curl -s http://localhost:8080/actuator/health > /dev/null 2>&1; then
    echo "ERROR: Center Node failed to start"
    echo "Center Node logs:"
    cat center-node/center.log 2>/dev/null || echo "No log file"
    kill $CENTER_PID 2>/dev/null || true
    exit 1
fi

echo "Center Node started successfully (PID: $CENTER_PID)"

# Start Client Node 1 (without ZooKeeper registration)
echo "Starting Client Node 1..."
cd client-node
java -jar target/client-node-1.0.0.jar \
    --server.port=8081 \
    --client.service-id= \
    > client1.log 2>&1 &
CLIENT1_PID=$!
cd ..

echo "Waiting for Client Node 1 to start (5 seconds)..."
sleep 5

# Check if Client Node 1 started successfully
if ! curl -s http://localhost:8081/api/messages/status > /dev/null 2>&1; then
    echo "ERROR: Client Node 1 failed to start"
    echo "Client Node 1 logs:"
    cat client-node/client1.log 2>/dev/null || echo "No log file"
    kill $CENTER_PID $CLIENT1_PID 2>/dev/null || true
    exit 1
fi

echo "Client Node 1 started successfully (PID: $CLIENT1_PID)"

# Start Client Node 2 (without ZooKeeper registration)
echo "Starting Client Node 2..."
cd client-node
java -jar target/client-node-1.0.0.jar \
    --server.port=8082 \
    --client.service-id=client-2 \
    > client2.log 2>&1 &
CLIENT2_PID=$!
cd ..

echo "Waiting for Client Node 2 to start (5 seconds)..."
sleep 5

# Check if Client Node 2 started successfully
if ! curl -s http://localhost:8082/api/messages/status > /dev/null 2>&1; then
    echo "ERROR: Client Node 2 failed to start"
    echo "Client Node 2 logs:"
    cat client-node/client2.log 2>/dev/null || echo "No log file"
    kill $CENTER_PID $CLIENT1_PID $CLIENT2_PID 2>/dev/null || true
    exit 1
fi

echo "Client Node 2 started successfully (PID: $CLIENT2_PID)"

echo ""
echo "========================================"
echo "Services started:"
echo "  Center Node:   http://localhost:8080"
echo "  Client Node 1: http://localhost:8081"
echo "  Client Node 2: http://localhost:8082"
echo "  Netty Server:  localhost:8888"
echo "========================================"
echo ""

# Test message sending
echo "Testing message sending..."
echo "Sending request from Client 1 to Client 2..."

RESPONSE=$(curl -s -X POST http://localhost:8081/api/messages/request \
  -H "Content-Type: application/json" \
  -d '{
    "target": "client-2",
    "payload": "Hello from client-1",
    "waitForResponse": true
  }' 2>/dev/null || echo "{}")

echo "Response: $RESPONSE"

# Check if response contains success
if echo "$RESPONSE" | grep -q '"status":"success"'; then
    echo "✅ Message sending test PASSED"
else
    echo "❌ Message sending test FAILED"
    echo "Full response: $RESPONSE"
fi

echo ""
echo "========================================"
echo "Test commands:"
echo ""
echo "1. Send request from Client 1 to Client 2:"
echo "   curl -X POST http://localhost:8081/api/messages/request \\"
echo "     -H \"Content-Type: application/json\" \\"
echo "     -d '{\"target\": \"client-2\", \"payload\": \"Test message\", \"waitForResponse\": true}'"
echo ""
echo "2. Send notification from Client 1 to Client 2:"
echo "   curl -X POST http://localhost:8081/api/messages/notify \\"
echo "     -H \"Content-Type: application/json\" \\"
echo "     -d '{\"target\": \"client-2\", \"payload\": \"Notification\"}'"
echo ""
echo "3. Check Center Node health:"
echo "   curl http://localhost:8080/actuator/health"
echo ""
echo "4. Check Client Node 1 status:"
echo "   curl http://localhost:8081/api/messages/status"
echo ""
echo "Press Ctrl+C to stop all services"
echo "========================================"

# Trap Ctrl+C to stop services
trap 'echo ""; echo "Stopping services..."; kill $CENTER_PID $CLIENT1_PID $CLIENT2_PID 2>/dev/null; exit' INT

# Wait for services
wait $CENTER_PID $CLIENT1_PID $CLIENT2_PID