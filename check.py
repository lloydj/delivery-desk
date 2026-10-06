#!/usr/bin/env python3
"""Walk the delivery loop against a running Desk server."""

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"


def call(method, path, payload=None, as_json=True):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            body = response.read().decode("utf-8")
            if not as_json:
                return response.status, body
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            parsed = json.loads(body) if body else {"error": error.reason}
        except json.JSONDecodeError:
            parsed = {"error": body}
        return error.code, parsed


def expect(status, actual, label):
    if actual != status:
        raise SystemExit("%s: expected %s, got %s" % (label, status, actual))


def main():
    status, vendors = call("GET", "/api/vendors")
    expect(200, status, "list vendors")
    if len(vendors) < 2:
        raise SystemExit("expected seeded stalls")

    stall = vendors[0]
    other = vendors[1]
    first, second = stall["items"][0], stall["items"][1]
    expected_total = first["price_cents"] * 2 + second["price_cents"]

    status, order = call(
        "POST",
        "/api/orders",
        {
            "client_name": "Ada",
            "neighborhood": "Bastos",
            "vendor_id": stall["id"],
            "lines": [
                {"item_id": first["id"], "qty": 2},
                {"item_id": second["id"], "qty": 1},
            ],
        },
    )
    expect(201, status, "place order")
    if order["total_cents"] != expected_total:
        raise SystemExit(
            "total %s != %s" % (order["total_cents"], expected_total)
        )
    if order["status"] != "placed":
        raise SystemExit("new order should be placed")
    if order["neighborhood"] != "Bastos":
        raise SystemExit("neighborhood was not saved")

    status, missing = call(
        "POST",
        "/api/orders",
        {
            "client_name": "Ada",
            "vendor_id": stall["id"],
            "lines": [{"item_id": first["id"], "qty": 1}],
        },
    )
    expect(400, status, "neighborhood required")

    status, rejected = call(
        "POST",
        "/api/orders",
        {
            "client_name": "Ada",
            "neighborhood": "Bastos",
            "vendor_id": stall["id"],
            "lines": [
                {"item_id": first["id"], "qty": 1},
                {"item_id": other["items"][0]["id"], "qty": 1},
            ],
        },
    )
    expect(400, status, "item from another stall")

    for to_status, actor in (
        ("accepted", "Harbor"),
        ("ready", "Harbor"),
        ("claimed", "Bo"),
        ("picked_up", "Bo"),
        ("delivered", "Bo"),
    ):
        status, order = call(
            "POST",
            "/api/orders/%s/transition" % order["id"],
            {"to": to_status, "actor": actor},
        )
        expect(200, status, "move to " + to_status)
        if order["status"] != to_status:
            raise SystemExit("status did not become " + to_status)

    if order["settlement"] != "cash_on_delivery":
        raise SystemExit("delivered order should record cash on delivery")
    if order["total_cents"] != expected_total:
        raise SystemExit("total changed during the handoff")
    notes = [event["note"] for event in order["events"]]
    if notes[-1] != "cash on delivery":
        raise SystemExit("missing cash event: %s" % notes)

    status, blocked = call(
        "POST",
        "/api/orders/%s/transition" % order["id"],
        {"to": "accepted", "actor": "Harbor"},
    )
    expect(409, status, "illegal transition")

    status, second_order = call(
        "POST",
        "/api/orders",
        {
            "client_name": "Ada",
            "neighborhood": "Mokolo",
            "vendor_id": other["id"],
            "lines": [{"item_id": other["items"][0]["id"], "qty": 1}],
        },
    )
    expect(201, status, "second order")
    status, second_order = call(
        "POST",
        "/api/orders/%s/transition" % second_order["id"],
        {"to": "rejected", "actor": "Day"},
    )
    expect(200, status, "reject")
    if second_order["status"] != "rejected":
        raise SystemExit("reject did not stick")

    status, third = call(
        "POST",
        "/api/orders",
        {
            "client_name": "Ivo",
            "neighborhood": "Bonapriso",
            "vendor_id": stall["id"],
            "lines": [{"item_id": first["id"], "qty": 1}],
        },
    )
    expect(201, status, "third order")
    for to_status, actor in (("accepted", "Harbor"), ("ready", "Harbor"), ("claimed", "Bo")):
        status, third = call(
            "POST",
            "/api/orders/%s/transition" % third["id"],
            {"to": to_status, "actor": actor},
        )
        expect(200, status, "setup " + to_status)
    status, wrong = call(
        "POST",
        "/api/orders/%s/transition" % third["id"],
        {"to": "picked_up", "actor": "Someone else"},
    )
    expect(409, status, "wrong courier")
    status, third = call(
        "POST",
        "/api/orders/%s/transition" % third["id"],
        {"to": "ready", "actor": "Bo"},
    )
    expect(200, status, "release")
    if third["status"] != "ready" or third["courier_name"] is not None:
        raise SystemExit("release did not clear the courier")

    status, page = call("GET", "/", as_json=False)
    expect(200, status, "home page")
    if "Desk" not in page:
        raise SystemExit("home page is missing the app")
    status, script = call("GET", "/static/app.js", as_json=False)
    expect(200, status, "app script")
    if "Place order" not in script:
        raise SystemExit("order button is missing from the page script")

    print(
        "ok: order #%s delivered for %s; reject, release, and a bad handoff were refused"
        % (order["id"], money(order["total_cents"]))
    )


def money(cents):
    return "%.2f" % (cents / 100.0)


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as error:
        raise SystemExit("server is not up at %s (%s)" % (BASE, error.reason))
