#!/usr/bin/env python3

from __future__ import annotations

import os
import signal
import socket
import threading


LISTEN_HOST = os.environ.get("SUPPORTPORTAL_LOCAL_DB_RELAY_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("SUPPORTPORTAL_LOCAL_DB_RELAY_PORT", "15433"))
TARGET_HOST = os.environ["SUPPORTPORTAL_LOCAL_DB_RELAY_TARGET_HOST"]
TARGET_PORT = int(os.environ.get("SUPPORTPORTAL_LOCAL_DB_RELAY_TARGET_PORT", "5432"))

_stop = False


def _forward(src: socket.socket, dst: socket.socket, label: str) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                return
            dst.sendall(data)
    except OSError as exc:
        print(f"{label} error: {exc}", flush=True)
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _handle_client(client: socket.socket, addr: tuple[str, int]) -> None:
    try:
        upstream = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=10)
        upstream.settimeout(None)
        client.settimeout(None)
    except OSError as exc:
        print(f"upstream_connect_fail {addr} {exc}", flush=True)
        client.close()
        return

    print(f"accepted {addr} -> {TARGET_HOST}:{TARGET_PORT}", flush=True)
    threading.Thread(target=_forward, args=(client, upstream, "c2u"), daemon=True).start()
    threading.Thread(target=_forward, args=(upstream, client, "u2c"), daemon=True).start()


def _sig_handler(*_: object) -> None:
    global _stop
    _stop = True


def main() -> None:
    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(128)
    server.settimeout(1.0)

    print(
        f"listening {LISTEN_HOST}:{LISTEN_PORT} -> {TARGET_HOST}:{TARGET_PORT}",
        flush=True,
    )

    try:
        while not _stop:
            try:
                client, addr = server.accept()
            except TimeoutError:
                continue
            except KeyboardInterrupt:
                break
            threading.Thread(target=_handle_client, args=(client, addr), daemon=True).start()
    finally:
        server.close()


if __name__ == "__main__":
    main()
