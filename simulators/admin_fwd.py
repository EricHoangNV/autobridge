"""Admin-port forwarder for the FIAS PABX simulator (REQ-034).

Docker publishes a dual-homed container's host ports through one endpoint
only (in practice the admin-net one), so the FIAS container cannot join
admin-net itself: binding :8000 to the edge interface would orphan the
host-published docs page, and binding it to all interfaces would expose
the data plane to control. Instead this dumb TCP pipe runs as a sidecar
(``sim-fias-pabx-admin``) on admin-net + edge-net, carries the
``sim-fias-pabx`` DNS alias on admin-net only, and forwards :8001 to the
FIAS container's admin listener over edge-net.

Trust boundary: this module never imports ``autobridge`` or ``verifier``.
It inspects no bytes — authorization (``X-Admin-Network`` /
``X-Admin-Token``) is enforced end to end by the FIAS admin app.
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading

_CHUNK = 65536
_IDLE_TIMEOUT_S = 120.0


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    """Copy bytes until EOF or error, then shut the direction down."""
    with contextlib.suppress(OSError):
        while True:
            data = src.recv(_CHUNK)
            if not data:
                break
            dst.sendall(data)
    with contextlib.suppress(OSError):
        dst.shutdown(socket.SHUT_WR)


def _self_dial_target(target_host: str, target_port: int, listen_port: int) -> bool:
    """Return True when forwarding would dial this process's own listener.

    Compares the route's source address against every address the target
    name resolves to, for the SAME port we listen on: when both overlap,
    connecting loops back into our own accept loop (a forwarding
    fork-bomb — seen once as Thread-37404 when the target shared the
    sidecar's own admin-net alias). Same IP on a different port is fine
    (a peer process). Any lookup failure is "not self" so a broken DNS
    fails loudly at connect time instead of here.
    """
    if target_port != listen_port:
        return False
    try:
        resolved = {r[4][0] for r in socket.getaddrinfo(target_host, target_port, type=socket.SOCK_STREAM)}
    except OSError:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((target_host, target_port))
            source = probe.getsockname()[0]
    except OSError:
        return False
    return source in resolved


def _handle(client: socket.socket, target_host: str, target_port: int, listen_port: int) -> None:
    if _self_dial_target(target_host, target_port, listen_port):
        # Fail closed and LOUD: forwarding into ourselves would fork-bomb
        # the sidecar (every accepted connection spawns another). Refuse
        # the connection and say why; the control proxy then surfaces the
        # error instead of hanging, and `make smoke` goes red.
        print(
            f"admin_fwd: REFUSING self-dial to {target_host}:{target_port} "
            "(target resolves to this container)",
            flush=True,
        )
        client.close()
        return
    try:
        upstream = socket.create_connection((target_host, target_port), timeout=10)
    except OSError as exc:
        print(f"admin_fwd: target {target_host}:{target_port} unreachable: {exc}", flush=True)
        client.close()
        return
    upstream.settimeout(_IDLE_TIMEOUT_S)
    client.settimeout(_IDLE_TIMEOUT_S)
    fwd = threading.Thread(target=_pipe, args=(client, upstream), daemon=True)
    back = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
    fwd.start()
    back.start()
    fwd.join()
    back.join()
    client.close()
    upstream.close()


def serve(listen_port: int, target_host: str, target_port: int) -> None:
    """Accept loop: ``:listen_port`` -> ``target_host:target_port``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", listen_port))
        srv.listen(64)
        print(
            f"admin_fwd: :{listen_port} -> {target_host}:{target_port}",
            flush=True,
        )
        while True:
            client, _peer = srv.accept()
            t = threading.Thread(
                target=_handle, args=(client, target_host, target_port, listen_port), daemon=True
            )
            t.start()


def main() -> None:
    listen_port = int(os.getenv("FORWARD_LISTEN_PORT", "8001"))
    target_host = os.getenv("FORWARD_TARGET_HOST", "sim-fias-pabx")
    target_port = int(os.getenv("FORWARD_TARGET_PORT", "8001"))
    serve(listen_port, target_host, target_port)


if __name__ == "__main__":
    main()
