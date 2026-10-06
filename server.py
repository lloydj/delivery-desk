#!/usr/bin/env python3
"""Local delivery desk. Start with: python3 server.py"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HOST = "127.0.0.1"
PORT = 8765
ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "app.db")
STATIC = os.path.realpath(os.path.join(ROOT, "static"))

# Legal next statuses. claimed → ready is the courier releasing the order.
NEXT = {
    "placed": {"accepted", "rejected"},
    "accepted": {"ready"},
    "ready": {"claimed"},
    "claimed": {"picked_up", "ready"},
    "picked_up": {"delivered"},
}

NOTES = {
    ("placed", "accepted"): "accepted",
    ("placed", "rejected"): "rejected",
    ("accepted", "ready"): "ready for pickup",
    ("ready", "claimed"): "claimed",
    ("claimed", "picked_up"): "picked up",
    ("claimed", "ready"): "released",
    ("picked_up", "delivered"): "cash on delivery",
}

SEED = [
    (
        "Harbor Grill",
        "Charcoal plates, served through the afternoon.",
        [
            ("Charcoal chicken", 1500),
            ("Fried plantain", 600),
            ("Jollof rice", 900),
        ],
    ),
    (
        "Day Bowl",
        "Breakfast, and a short lunch.",
        [
            ("Beans and plantain", 800),
            ("Fried eggs", 500),
            ("Coffee", 300),
        ],
    ),
    (
        "Folded Dough",
        "Pies and drinks to carry.",
        [
            ("Meat pie", 400),
            ("Puff puff", 250),
            ("Hibiscus drink", 350),
        ],
    ),
]


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    conn = connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS vendors (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                vendor_id INTEGER NOT NULL REFERENCES vendors(id),
                name TEXT NOT NULL,
                price_cents INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY,
                vendor_id INTEGER NOT NULL REFERENCES vendors(id),
                client_name TEXT NOT NULL,
                neighborhood TEXT NOT NULL,
                status TEXT NOT NULL,
                total_cents INTEGER NOT NULL,
                courier_name TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES orders(id),
                item_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                unit_price_cents INTEGER NOT NULL,
                qty INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES orders(id),
                status TEXT NOT NULL,
                actor TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(orders)")]
        if "address" in columns and "neighborhood" not in columns:
            conn.execute("ALTER TABLE orders RENAME COLUMN address TO neighborhood")
        count = conn.execute("SELECT COUNT(*) AS n FROM vendors").fetchone()["n"]
        if count == 0:
            for name, description, menu in SEED:
                cur = conn.execute(
                    "INSERT INTO vendors (name, description) VALUES (?, ?)",
                    (name, description),
                )
                vendor_id = cur.lastrowid
                conn.executemany(
                    "INSERT INTO items (vendor_id, name, price_cents) VALUES (?, ?, ?)",
                    [(vendor_id, item_name, price) for item_name, price in menu],
                )
        conn.commit()
    finally:
        conn.close()


def vendor_json(conn, vendor_id):
    vendor = conn.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
    if vendor is None:
        return None
    items = conn.execute(
        "SELECT id, name, price_cents FROM items WHERE vendor_id = ? ORDER BY id",
        (vendor_id,),
    ).fetchall()
    return {
        "id": vendor["id"],
        "name": vendor["name"],
        "description": vendor["description"],
        "items": [dict(item) for item in items],
    }


def order_json(conn, order_id):
    order = conn.execute(
        """
        SELECT o.*, v.name AS vendor_name
        FROM orders o
        JOIN vendors v ON v.id = o.vendor_id
        WHERE o.id = ?
        """,
        (order_id,),
    ).fetchone()
    if order is None:
        return None
    items = conn.execute(
        """
        SELECT item_id, name, unit_price_cents, qty
        FROM order_items WHERE order_id = ? ORDER BY id
        """,
        (order_id,),
    ).fetchall()
    events = conn.execute(
        """
        SELECT status, actor, note, created_at
        FROM events WHERE order_id = ? ORDER BY id
        """,
        (order_id,),
    ).fetchall()
    return {
        "id": order["id"],
        "vendor_id": order["vendor_id"],
        "vendor_name": order["vendor_name"],
        "client_name": order["client_name"],
        "neighborhood": order["neighborhood"],
        "status": order["status"],
        "total_cents": order["total_cents"],
        "courier_name": order["courier_name"],
        "created_at": order["created_at"],
        "settlement": "cash_on_delivery" if order["status"] == "delivered" else None,
        "items": [dict(item) for item in items],
        "events": [dict(event) for event in events],
    }


def create_order(conn, data):
    client = str(data.get("client_name", "")).strip()
    neighborhood = str(data.get("neighborhood", "")).strip()
    if not client or len(client) > 40:
        return 400, {"error": "client name is required"}
    if not neighborhood or len(neighborhood) > 80:
        return 400, {"error": "neighborhood is required"}
    try:
        vendor_id = int(data.get("vendor_id"))
    except (TypeError, ValueError):
        return 400, {"error": "vendor_id is required"}
    if conn.execute("SELECT id FROM vendors WHERE id = ?", (vendor_id,)).fetchone() is None:
        return 400, {"error": "unknown vendor"}

    lines = data.get("lines")
    if not isinstance(lines, list) or not lines or len(lines) > 30:
        return 400, {"error": "add at least one item"}

    merged = {}
    for line in lines:
        if not isinstance(line, dict):
            return 400, {"error": "bad line"}
        try:
            item_id = int(line.get("item_id"))
            qty = int(line.get("qty"))
        except (TypeError, ValueError):
            return 400, {"error": "bad line"}
        if qty < 1:
            return 400, {"error": "quantity must be from 1 to 20"}
        merged[item_id] = merged.get(item_id, 0) + qty
        if merged[item_id] > 20:
            return 400, {"error": "quantity must be from 1 to 20"}

    chosen = []
    total = 0
    for item_id, qty in merged.items():
        row = conn.execute(
            "SELECT id, name, price_cents, vendor_id FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None or row["vendor_id"] != vendor_id:
            return 400, {"error": "item is not on this stall's menu"}
        total += row["price_cents"] * qty
        chosen.append((row, qty))

    stamp = now()
    with conn:
        cur = conn.execute(
            """
            INSERT INTO orders (
                vendor_id, client_name, neighborhood, status, total_cents, courier_name, created_at
            ) VALUES (?, ?, ?, 'placed', ?, NULL, ?)
            """,
            (vendor_id, client, neighborhood, total, stamp),
        )
        order_id = cur.lastrowid
        conn.executemany(
            """
            INSERT INTO order_items (order_id, item_id, name, unit_price_cents, qty)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (order_id, row["id"], row["name"], row["price_cents"], qty)
                for row, qty in chosen
            ],
        )
        conn.execute(
            """
            INSERT INTO events (order_id, status, actor, note, created_at)
            VALUES (?, 'placed', ?, 'order placed', ?)
            """,
            (order_id, client, stamp),
        )
    return 201, order_json(conn, order_id)


def transition(conn, order_id, data):
    actor = str(data.get("actor", "")).strip()
    to_status = str(data.get("to", "")).strip()
    if not actor or len(actor) > 40:
        return 400, {"error": "actor is required"}
    if not to_status:
        return 400, {"error": "to is required"}

    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        return 404, {"error": "order not found"}

    current = row["status"]
    if to_status not in NEXT.get(current, ()):
        return 409, {"error": "cannot go from %s to %s" % (current, to_status)}

    courier_name = row["courier_name"]
    if current in ("claimed", "picked_up"):
        if actor.casefold() != (courier_name or "").casefold():
            return 409, {"error": "courier does not match"}

    if to_status == "claimed":
        courier_name = actor
    elif current == "claimed" and to_status == "ready":
        courier_name = None

    note = NOTES[(current, to_status)]
    with conn:
        conn.execute(
            "UPDATE orders SET status = ?, courier_name = ? WHERE id = ?",
            (to_status, courier_name, order_id),
        )
        conn.execute(
            """
            INSERT INTO events (order_id, status, actor, note, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (order_id, to_status, actor, note, now()),
        )
    return 200, order_json(conn, order_id)


def list_orders(conn, query):
    clauses = []
    args = []

    def one(name):
        values = query.get(name) or []
        return values[0].strip() if values and values[0].strip() else ""

    vendor_id = one("vendor_id")
    status = one("status")
    client_name = one("client_name")
    courier_name = one("courier_name")

    if vendor_id:
        try:
            args.append(int(vendor_id))
        except ValueError:
            return 400, {"error": "bad vendor_id"}
        clauses.append("o.vendor_id = ?")
    if status:
        clauses.append("o.status = ?")
        args.append(status)
    if client_name:
        clauses.append("LOWER(o.client_name) = LOWER(?)")
        args.append(client_name)
    if courier_name:
        clauses.append("LOWER(o.courier_name) = LOWER(?)")
        args.append(courier_name)

    sql = """
        SELECT o.id
        FROM orders o
        JOIN vendors v ON v.id = o.vendor_id
    """
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY o.id DESC"
    rows = conn.execute(sql, args).fetchall()
    return 200, [order_json(conn, row["id"]) for row in rows]


def summary(conn):
    open_count = conn.execute(
        "SELECT COUNT(*) AS n FROM orders WHERE status NOT IN ('delivered', 'rejected')"
    ).fetchone()["n"]
    delivered = conn.execute(
        "SELECT COUNT(*) AS n FROM orders WHERE status = 'delivered'"
    ).fetchone()["n"]
    return {"open": open_count, "delivered": delivered}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parts, query = self._parts()
        if parts == ["api", "vendors"]:
            conn = connect()
            try:
                ids = conn.execute("SELECT id FROM vendors ORDER BY id").fetchall()
                self._json(200, [vendor_json(conn, row["id"]) for row in ids])
            finally:
                conn.close()
            return
        if len(parts) == 3 and parts[:2] == ["api", "vendors"]:
            self._vendor(parts[2])
            return
        if parts == ["api", "orders"]:
            conn = connect()
            try:
                code, payload = list_orders(conn, query)
                self._json(code, payload)
            finally:
                conn.close()
            return
        if len(parts) == 3 and parts[:2] == ["api", "orders"]:
            self._order(parts[2])
            return
        if parts == ["api", "summary"]:
            conn = connect()
            try:
                self._json(200, summary(conn))
            finally:
                conn.close()
            return
        if not parts:
            self._file("index.html")
            return
        if parts[0] == "static":
            self._file("/".join(parts[1:]))
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        parts, _query = self._parts()
        try:
            data = self._body()
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return

        if parts == ["api", "orders"]:
            conn = connect()
            try:
                code, payload = create_order(conn, data)
                self._json(code, payload)
            finally:
                conn.close()
            return
        if len(parts) == 4 and parts[:2] == ["api", "orders"] and parts[3] == "transition":
            try:
                order_id = int(parts[2])
            except ValueError:
                self._json(404, {"error": "order not found"})
                return
            conn = connect()
            try:
                code, payload = transition(conn, order_id, data)
                self._json(code, payload)
            finally:
                conn.close()
            return
        self._json(404, {"error": "not found"})

    def _vendor(self, raw_id):
        try:
            vendor_id = int(raw_id)
        except ValueError:
            self._json(404, {"error": "vendor not found"})
            return
        conn = connect()
        try:
            payload = vendor_json(conn, vendor_id)
        finally:
            conn.close()
        if payload is None:
            self._json(404, {"error": "vendor not found"})
            return
        self._json(200, payload)

    def _order(self, raw_id):
        try:
            order_id = int(raw_id)
        except ValueError:
            self._json(404, {"error": "order not found"})
            return
        conn = connect()
        try:
            payload = order_json(conn, order_id)
        finally:
            conn.close()
        if payload is None:
            self._json(404, {"error": "order not found"})
            return
        self._json(200, payload)

    def _parts(self):
        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        return parts, parse_qs(parsed.query)

    def _body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length < 0 or length > 100_000:
            raise ValueError("body too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("invalid json")
        if not isinstance(data, dict):
            raise ValueError("expected an object")
        return data

    def _json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, relative):
        if not relative:
            self._json(404, {"error": "not found"})
            return
        full = os.path.realpath(os.path.join(STATIC, relative))
        try:
            inside = os.path.commonpath([STATIC, full]) == STATIC
        except ValueError:
            inside = False
        if not inside or not os.path.isfile(full):
            self._json(404, {"error": "not found"})
            return
        if full.endswith(".css"):
            content_type = "text/css; charset=utf-8"
        elif full.endswith(".js"):
            content_type = "text/javascript; charset=utf-8"
        elif full.endswith(".html"):
            content_type = "text/html; charset=utf-8"
        else:
            content_type = "application/octet-stream"
        with open(full, "rb") as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Quiet the default stderr access log. Failures still raise.
        return


class Server(ThreadingHTTPServer):
    allow_reuse_address = True


def main():
    init_db()
    server = Server((HOST, PORT), Handler)
    print("Desk is at http://%s:%s" % (HOST, PORT))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
