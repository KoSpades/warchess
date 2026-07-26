#!/usr/bin/env python3
"""Warchess — run `python game.py` and open the printed URLs.

Two seats, two browser tabs. Commitment is hidden, so each side needs its own
view; the server holds the authoritative state and filters what each seat sees.
"""

import argparse
import atexit
import json
import os
import shutil
import subprocess
import time
import urllib.request
import webbrowser

from server import serve

DOMAIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ngrok_domain.txt")


def configured_domain():
    """The fixed ngrok domain for --share, resolved WITHOUT depending on the
    shell environment: the WARCHESS_NGROK_DOMAIN env var if set, otherwise the
    contents of ngrok_domain.txt sitting next to this script."""
    env = os.environ.get("WARCHESS_NGROK_DOMAIN")
    if env and env.strip():
        return env.strip()
    try:
        with open(DOMAIN_FILE) as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def start_tunnel(port, domain=None):
    """Open a public ngrok tunnel to the local server and return its https URL.

    With `domain` set to a static domain you've claimed on your ngrok account
    (dashboard.ngrok.com/domains — free accounts get one), the link is the same
    every time. Otherwise ngrok assigns a fresh random URL each run.

    Returns None (and prints why) if ngrok is missing or the tunnel does not
    come up. ngrok is spawned as a child and killed when this process exits."""
    if not shutil.which("ngrok"):
        print("  --share needs ngrok, which isn't installed.")
        print("  Install it with `brew install ngrok`, then run `ngrok config")
        print("  add-authtoken <token>` once (free account at ngrok.com).")
        return None

    cmd = ["ngrok", "http", str(port), "--log", "stdout"]
    if domain:
        cmd += ["--url", domain]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    atexit.register(proc.terminate)

    # ngrok exposes a local API listing active tunnels once it has connected.
    # A claimed static domain can take a while to register, so wait generously.
    timeout = 45
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            print("  ngrok exited immediately. Check `ngrok http %d` by hand —" % port)
            print("  it usually means the authtoken isn't set up.")
            return None
        try:
            with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=1) as fh:
                tunnels = json.load(fh).get("tunnels", [])
            https = [t["public_url"] for t in tunnels if t.get("public_url", "").startswith("https")]
            if https:
                return https[0]
        except Exception:
            pass
        time.sleep(0.5)

    print(f"  Tunnel did not come up within {timeout}s. Is ngrok configured?")
    return None


def main():
    ap = argparse.ArgumentParser(description="Run the Warchess plotting table locally.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1",
                    help="Interface to bind. Defaults to localhost.")
    ap.add_argument("--share", action="store_true",
                    help="Open a public ngrok link so a friend on any network "
                         "(not just your Wi-Fi) can join. Requires ngrok.")
    ap.add_argument("--domain", default=None,
                    help="Your claimed ngrok static domain (e.g. foo.ngrok-free.app), "
                         "so --share gives the same link every time. Defaults to "
                         "WARCHESS_NGROK_DOMAIN or the ngrok_domain.txt file next to "
                         "this script.")
    ap.add_argument("--self", dest="solo", action="store_true",
                    help="Solo mode: you drive both seats yourself, and normal "
                         "attacks auto-aim at random grids to keep it quick.")
    ap.add_argument("--test", action="store_true",
                    help="Test mode: an auto-set-up 2v2 with your newest champions "
                         "(plus dummies) vs two dummies, so you can try a new hero fast.")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    mode = "test" if args.test else "self" if args.solo else "pvp"
    try:
        httpd = serve(args.host, args.port, mode)
    except OSError as e:
        if e.errno in (48, 98):  # EADDRINUSE — 48 on macOS, 98 on Linux
            holder = ""
            try:
                out = subprocess.run(
                    ["lsof", "-tiTCP:%d" % args.port, "-sTCP:LISTEN"],
                    capture_output=True, text=True,
                )
                holder = out.stdout.strip().replace("\n", " ")
            except Exception:
                pass
            print(f"Port {args.port} is already in use — a previous Warchess is")
            print("probably still running. Free it and try again:\n")
            if holder:
                print(f"  kill -9 {holder}")
            else:
                print(f"  kill -9 $(lsof -tiTCP:{args.port} -sTCP:LISTEN)")
            print(f"\nOr use another port:  python game.py"
                  f"{' --share' if args.share else ''} --port {args.port + 1}")
            return
        raise

    local = f"http://127.0.0.1:{args.port}"
    print("Warchess is running.\n")
    if args.test:
        print("  Test mode — your newest champions (+ dummies) vs two dummies,")
        print("  already deployed. You drive both sides.\n")
        print(f"    Your side   {local}/?side=L")
        print(f"    Dummies     {local}/?side=R")
        open_url = f"{local}/?side=L"
    elif args.solo:
        print("  Solo mode — you play both sides. Normal attacks auto-aim at")
        print("  random grids, so just move and hit Enter.\n")
        print(f"    Left seat   {local}/?side=L")
        print(f"    Right seat  {local}/?side=R")
        print("\n  Open both in separate tabs and drive each in turn.")
        open_url = f"{local}/?side=L"
    elif args.share:
        public = start_tunnel(args.port, args.domain or configured_domain())
        if public:
            print("  Anyone, anywhere — no shared Wi-Fi needed.\n")
            print("  Send your friend this link:")
            print(f"    Right seat   {public}/?side=R")
            print("\n  Your own seat:")
            print(f"    Left seat    {public}/?side=L")
            print("\n  There is no password: anyone with the link can open either")
            print("  seat, and the link changes each time you restart --share.")
            print("  Your friend may see an ngrok 'Visit Site' page once — that's normal.")
            open_url = f"{public}/?side=L"
        else:
            print("\n  Falling back to local only:")
            print(f"    Left seat   {local}/?side=L")
            print(f"    Right seat  {local}/?side=R")
            open_url = f"{local}/?side=L"
    else:
        print(f"  Left seat   {local}/?side=L")
        print(f"  Right seat  {local}/?side=R")
        print("\n  Both seats are on this machine only.")
        print("  For a friend on another network, restart with --share.")
        open_url = f"{local}/?side=L"
    print("\nCtrl-C to stop.")

    if not args.no_browser:
        try:
            webbrowser.open(open_url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
