#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  Rex & Lou Studio — Start Script
#  Runs the Python server + ngrok tunnel for remote access
# ═══════════════════════════════════════════════════════════════════

cd "$(dirname "$0")"

# ── Cleanup handler ──
cleanup() {
  echo ""
  echo "Shutting down…"
  kill $SERVER_PID 2>/dev/null
  wait $SERVER_PID 2>/dev/null
  # Kill ngrok (find by port)
  lsof -ti:8080 | grep -v $PPID | xargs kill 2>/dev/null
  echo "Stopped."
  exit 0
}
trap cleanup SIGINT SIGTERM

# Kill any existing server on port 8080
echo "Cleaning up old processes…"
lsof -ti:8080 | xargs kill -9 2>/dev/null
sleep 0.5

# Start Python server in background
echo "Starting Python server…"
python3 tts_proxy.py &
SERVER_PID=$!
sleep 1

# Check if server started
if ! kill -0 $SERVER_PID 2>/dev/null; then
  echo "ERROR: Server failed to start"
  exit 1
fi

# ── Get network IP (try en0 Wi-Fi first, then en1, then fallback) ──
NET_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "YOUR_IP")

# Start ngrok tunnel
echo ""
echo "Starting ngrok tunnel for remote access…"

# Try installed ngrok first, then home bin
NGROK=$(which ngrok 2>/dev/null || echo "$HOME/bin/ngrok")

"$NGROK" http 8080 --log=stdout --log-format=json 2>&1 | while IFS= read -r line; do
  echo "$line"
  # Parse JSON log for the tunnel URL
  if echo "$line" | grep -q '"url".*"https://.*ngrok'; then
    PUBLIC_URL=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('url',''))" 2>/dev/null)
    if [ -n "$PUBLIC_URL" ]; then
      echo ""
      echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
      echo "  ✅ Server running — access from anywhere!"
      echo "  Local:    http://localhost:8080"
      echo "  Network:  http://${NET_IP}:8080"
      echo "  Public:   ${PUBLIC_URL}"
      echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
      echo ""
    fi
  fi
done
