#!/bin/bash

# JIRA Triage & Knowledge Agent - Startup Script

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
VENV_DIR="$PROJECT_DIR/venv"
LOG_FILE="/tmp/fastapi_jira_triage.log"
PID_FILE="/tmp/fastapi_jira_triage.pid"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
    echo "Usage: $0 {start|stop|restart|status|ingest|logs}"
    exit 1
}

start_server() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
        echo -e "${YELLOW}Server already running (PID: $(cat $PID_FILE))${NC}"
        return
    fi

    echo -e "${GREEN}Starting JIRA Triage API server...${NC}"
    source "$VENV_DIR/bin/activate"
    cd "$BACKEND_DIR"
    nohup uvicorn main:app --host 0.0.0.0 --port 8000 > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 2

    if kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
        echo -e "${GREEN}Server started successfully (PID: $(cat $PID_FILE))${NC}"
        echo -e "${GREEN}Access the app at: http://localhost:8000${NC}"
    else
        echo -e "${RED}Failed to start server. Check logs: $LOG_FILE${NC}"
        exit 1
    fi
}

stop_server() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo -e "${YELLOW}Stopping server (PID: $PID)...${NC}"
            kill "$PID"
            sleep 2
            echo -e "${GREEN}Server stopped.${NC}"
        fi
        rm -f "$PID_FILE"
    else
        echo -e "${YELLOW}No running server found.${NC}"
    fi
}

show_status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
        echo -e "${GREEN}Server is running (PID: $(cat $PID_FILE))${NC}"
        echo -e "${GREEN}URL: http://localhost:8000${NC}"
    else
        echo -e "${YELLOW}Server is not running.${NC}"
    fi
}

ingest_data() {
    echo -e "${GREEN}Ingesting JIRA tickets into AWS RDS PostgreSQL + pgvector...${NC}"
    source "$VENV_DIR/bin/activate"
    cd "$PROJECT_DIR"
    python ingest_pipeline.py
}

show_logs() {
    tail -f "$LOG_FILE"
}

case "$1" in
    start)   start_server ;;
    stop)    stop_server ;;
    restart) stop_server; start_server ;;
    status)  show_status ;;
    ingest)  ingest_data ;;
    logs)    show_logs ;;
    *)       usage ;;
esac
