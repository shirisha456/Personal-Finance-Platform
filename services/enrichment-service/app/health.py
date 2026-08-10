import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

logger = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        elif self.path == "/metrics":
            body = generate_latest()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args) -> None:
        pass  # don't spam service logs with health-check/scrape hits


def start_health_server(port: int) -> HTTPServer:
    """One minimal HTTP server on its own thread serving both `/health`
    (so `docker compose`/Kubernetes can tell this process is alive) and
    `/metrics` (Prometheus scrape target, Phase 12) — one port for both
    rather than two separate mechanisms (a bare
    prometheus_client.start_http_server alongside no health endpoint at
    all)."""
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health check server listening on :%d/health and /metrics", port)
    return server
