import json
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# === HARDCODED CONNECTIONS ===
# Challenge: these must become dynamic via service registry
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "postgres")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.environ.get("POSTGRES_DB", "testbed")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "testbed")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "testbed")

# Simulated connections (in real app these would be real connections)
class RedisClient:
    """Simulated Redis client — stores data in-memory for testing."""
    def __init__(self):
        self._store = {}
        self._status = "healthy"

    def get(self, key):
        if self._status != "healthy":
            raise ConnectionError(f"Redis unavailable: {self._status}")
        time.sleep(0.001)  # simulate network
        return self._store.get(key)

    def set(self, key, value, ttl=None):
        if self._status != "healthy":
            raise ConnectionError(f"Redis unavailable: {self._status}")
        time.sleep(0.001)
        self._store[key] = value

    def set_status(self, status):
        self._status = status

class PostgresClient:
    """Simulated Postgres client — stores data in-memory for testing."""
    def __init__(self):
        self._items = {}
        self._next_id = 1
        self._status = "healthy"

    def create_item(self, name, value):
        if self._status != "healthy":
            raise ConnectionError(f"Postgres unavailable: {self._status}")
        item_id = self._next_id
        self._items[item_id] = {"id": item_id, "name": name, "value": value}
        self._next_id += 1
        return self._items[item_id]

    def get_item(self, item_id):
        if self._status != "healthy":
            raise ConnectionError(f"Postgres unavailable: {self._status}")
        return self._items.get(item_id)

    def list_items(self):
        if self._status != "healthy":
            raise ConnectionError(f"Postgres unavailable: {self._status}")
        return list(self._items.values())

    def set_status(self, status):
        self._status = status

# === APPLICATION CODE ===
class ItemAPI(BaseHTTPRequestHandler):
    redis = RedisClient()
    postgres = PostgresClient()

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json({"status": "ok"})
        elif path == "/items":
            # Check cache first
            cached = self.redis.get("items:all")
            if cached:
                items = json.loads(cached)
            else:
                items = self.postgres.list_items()
                self.redis.set("items:all", json.dumps(items))
            self._send_json(items)
        elif path.startswith("/items/"):
            try:
                item_id = int(path.split("/")[-1])
            except ValueError:
                self._send_json({"error": "invalid id"}, 400)
                return
            item = self.postgres.get_item(item_id)
            if item:
                self._send_json(item)
            else:
                self._send_json({"error": "not found"}, 404)
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/items":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            try:
                item = self.postgres.create_item(body["name"], body["value"])
                # Invalidate cache
                self.redis.set("items:all", json.dumps(self.postgres.list_items()))
                self._send_json(item, 201)
            except KeyError:
                self._send_json({"error": "name and value required"}, 400)
        else:
            self._send_json({"error": "not found"}, 404)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    server = HTTPServer(("0.0.0.0", port), ItemAPI)
    print(f"Item API listening on 0.0.0.0:{port}")
    server.serve_forever()
