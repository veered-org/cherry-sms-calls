"""baresip ctrl_tcp client (netstring-framed JSON).

IMPORTANT: baresip's ctrl_tcp serves ONE client at a time - a new connection
silently drops the previous one. cosmic-phoned holds the long-lived event
connection; every cosmic-phone-ctl command briefly steals it, and phoned
reconnects. Never add another persistent ctrl_tcp client.
"""
import json
import socket


def netstring(payload: bytes) -> bytes:
    return str(len(payload)).encode() + b":" + payload + b","


def command_bytes(cmd, params="", token="cosmic-phone"):
    return netstring(json.dumps({"command": cmd, "params": params,
                                 "token": token}).encode())


def send(host, port, cmd, params="", timeout=4):
    with socket.create_connection((host, int(port)), timeout=timeout) as s:
        s.sendall(command_bytes(cmd, params))
        s.settimeout(3)
        buf = b""
        try:
            while len(buf) < 65536:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"," in buf and b"}" in buf:
                    break
        except socket.timeout:
            pass
    return buf.decode("utf-8", "replace")


def read_netstrings(sock):
    """Yield payloads from a netstring stream; yield None on read timeout."""
    buf = b""
    while True:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            yield None
            continue
        if not chunk:
            return
        buf += chunk
        while b":" in buf:
            head, rest = buf.split(b":", 1)
            if not head.isdigit():
                buf = rest
                break
            n = int(head)
            if len(rest) < n + 1:
                break
            yield rest[:n]
            buf = rest[n + 1:]


def read_state(path):
    """-> (kind, number) from the state file; IDLE if absent."""
    try:
        with open(path) as f:
            state = f.read().strip()
    except Exception:
        return "IDLE", ""
    kind, _, num = state.partition("|")
    return kind or "IDLE", num
