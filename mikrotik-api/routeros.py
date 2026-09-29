"""Minimal RouterOS API client (the binary protocol on port 8728).

Supports the plaintext login of RouterOS 6.43+ and an optional HTTP CONNECT proxy in front
of the router. Standard library only. The collector uses it for read-only commands.
"""
import socket


class RouterOSError(Exception):
    pass


def _enc_len(n):
    if n < 0x80:
        return bytes([n])
    if n < 0x4000:
        return (n | 0x8000).to_bytes(2, "big")
    if n < 0x200000:
        return (n | 0xC00000).to_bytes(3, "big")
    if n < 0x10000000:
        return (n | 0xE0000000).to_bytes(4, "big")
    return b"\xf0" + n.to_bytes(4, "big")


class RouterOS:
    def __init__(self, host, port=8728, user="", password="", proxy=None, timeout=20):
        self.host, self.port, self.user, self.password = host, port, user, password
        self.proxy, self.timeout = proxy, timeout
        self.sock = None
        self.buf = b""

    # ---- connection ----
    def connect(self):
        self.close()
        if self.proxy:
            ph, pp = self.proxy.rsplit(":", 1)
            s = socket.create_connection((ph, int(pp)), timeout=self.timeout)
            s.sendall(f"CONNECT {self.host}:{self.port} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\n\r\n".encode())
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = s.recv(4096)
                if not chunk:
                    raise RouterOSError("proxy closed the connection")
                head += chunk
            status = head.split(b"\r\n", 1)[0].decode(errors="replace")
            if " 200" not in status:
                s.close()
                raise RouterOSError(f"proxy refused CONNECT: {status}")
            self.buf = head.split(b"\r\n\r\n", 1)[1]
        else:
            s = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self.buf = b""
        self.sock = s
        replies = self._talk(["/login", f"=name={self.user}", f"=password={self.password}"])
        if any(r[0] in ("!trap", "!fatal") for r in replies):
            self.close()
            raise RouterOSError("login failed: check username_api and password_api in .env")

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    # ---- protocol ----
    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                self.close()  # the next call reconnects
                raise RouterOSError("router closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _read_len(self):
        c = self._read(1)[0]
        if c < 0x80:
            return c
        if c < 0xC0:
            return ((c & 0x3F) << 8) | self._read(1)[0]
        if c < 0xE0:
            return int.from_bytes(bytes([c & 0x1F]) + self._read(2), "big")
        if c < 0xF0:
            return int.from_bytes(bytes([c & 0x0F]) + self._read(3), "big")
        return int.from_bytes(self._read(4), "big")

    def _sentence(self):
        words = []
        while True:
            n = self._read_len()
            if n == 0:
                return words
            words.append(self._read(n).decode("utf-8", "replace"))

    def _talk(self, words):
        data = b"".join(_enc_len(len(w.encode())) + w.encode() for w in words) + b"\x00"
        self.sock.sendall(data)
        replies = []
        while True:
            s = self._sentence()
            if not s:
                continue
            replies.append(s)
            if s[0] in ("!done", "!fatal"):
                return replies

    # ---- public ----
    def print(self, path, proplist=None, **query):
        """Run `<path>/print` and return a list of dicts. Raises RouterOSError on !trap."""
        if self.sock is None:
            self.connect()
        words = [path.rstrip("/") + "/print"]
        if proplist:
            words.append("=.proplist=" + ",".join(proplist))
        words += [f"?{k.replace('_', '-')}={v}" for k, v in query.items()]
        rows = []
        for s in self._talk(words):
            if s[0] == "!re":
                row = {}
                for w in s[1:]:
                    if w.startswith("="):
                        k, _, v = w[1:].partition("=")
                        row[k] = v
                rows.append(row)
            elif s[0] in ("!trap", "!fatal"):
                msg = next((w.split("=", 2)[2] for w in s if w.startswith("=message=")), " ".join(s))
                raise RouterOSError(f"{path}: {msg}")
        return rows

    def command(self, words):
        """Run an arbitrary read-only command (for example a monitor with =once=)."""
        if self.sock is None:
            self.connect()
        rows = []
        for s in self._talk(words):
            if s[0] == "!re":
                rows.append({w[1:].partition("=")[0]: w[1:].partition("=")[2] for w in s[1:] if w.startswith("=")})
            elif s[0] in ("!trap", "!fatal"):
                raise RouterOSError(" ".join(s))
        return rows
