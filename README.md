# Desk

A local delivery loop for one machine. A client orders from a stall and gives a neighborhood. The vendor prepares it, and a moto guy carries it. He calls the client to find the exact spot. The delivered order records the cash total. Nothing is charged.

## Start

Python 3.9 or newer. No extra packages.

```bash
python3 server.py
```

Open http://127.0.0.1:8765

Type a name at the top. Use the role switcher to play client, vendor, and moto guy in the same browser. Orders stay in `app.db`.

## Click-through

1. Stay on Client. Open Harbor Grill. Add two items. Type a neighborhood. Place the order.
2. Switch to Vendor. Choose Harbor Grill. Accept, then Mark ready.
3. Switch to Moto. Claim the order, then Picked up, then Delivered. The picked-up step is when the moto guy calls the client.
4. Switch to Client and open the order. The status is Delivered, with the cash total.

The page refreshes an open order every few seconds.

## API check

With the server running:

```bash
python3 check.py
```

That walks one order from the menu to delivered, then checks a rejected order, a released claim, and a handoff by the wrong moto guy.

## Contributing

Issues and pull requests are welcome. Start the server, run `python3 check.py`, and include what you tried.
