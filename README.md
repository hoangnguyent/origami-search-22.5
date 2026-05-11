# SEARCH-22.5
Spectral Embedding Archive for Rational Crease pattern Hyperspace - 22.5

To setup:

Open a powershell at the root directory and run:
`./setup.bat`

For mac:
`uv venv -p /opt/homebrew/bin/python3.13 .venv`
`uv pip install -r requirements.txt`
Remote access via a tunnel
-------------------------
To expose the local interface to remote users without opening ports, use Cloudflare Tunnel (or an SSH/Tailscale tunnel). Start the server locally and create a tunnel pointing to the local address/port (default 127.0.0.1:8000). Keep `SEARCH22_INTERFACE_TOKEN` set to a strong shared secret when enabling remote access.

Basic example (Cloudflare Tunnel):

```bash
# start the local interface
python -m interface.server
# then in another shell run cloudflared to expose it (see Cloudflare docs):
cloudflared tunnel --url http://127.0.0.1:8000
```

Logs
----
The interface writes a compact per-query audit log at `interface/query.log` by default. The log contains non-sensitive metadata (timestamp, remote IP, query id, db choices, counts, latency, and a compact result summary). The full fold/CP payload and the base64 pickle are not logged.

