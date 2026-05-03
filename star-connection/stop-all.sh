#!/bin/bash

# Stop script for Star Connection Microservice

echo "Stopping all Star Connection services..."

# Find and kill Java processes for our services
pkill -f "center-node-1.0.0.jar" 2>/dev/null
pkill -f "client-node-1.0.0.jar" 2>/dev/null

# Check if services were stopped
if pgrep -f "center-node-1.0.0.jar" > /dev/null; then
    echo "Warning: Some services may still be running"
else
    echo "All services stopped"
fi