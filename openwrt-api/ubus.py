"""Client for ubus over HTTP (uhttpd-mod-ubus) of an OpenWrt router, directly or through an HTTP proxy.

One JSON-RPC call per request: {"method": "call", "params": [session, object, method, arguments]}.
The session comes from session.login and expires after a few minutes without calls; a call that is
refused with "permission denied" logs in again once and repeats.
"""
import json
import logging
import ssl
import urllib.error
import urllib.request

log = logging.getLogger("ubus")

ANON = "0" * 32
CODES = {1: "invalid command", 2: "invalid argument", 3: "method not found", 4: "not found", 5: "no data",
         6: "permission denied", 7: "timeout", 8: "not supported", 9: "unknown error", 10: "connection failed"}


class UbusError(OSError):
    def __init__(self, code, what):
        self.code = code
        super().__init__(f"{what}: {CODES.get(code, code)}")


class Ubus:
    def __init__(self, url, username, password, proxy=None, timeout=20):
        self.url, self.username, self.password, self.timeout = url.rstrip("/") + "/ubus", username, password, timeout
        self.proxy = proxy
        # Routers have self-signed certificates: with https the channel is encrypted, the peer is not verified.
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"} if proxy else {}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies), urllib.request.HTTPSHandler(context=ctx))
        self.sid = None
        self.n = 0

    def _post(self, sid, obj, method, args):
        self.n += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.n, "method": "call", "params": [sid, obj, method, args or {}]}).encode()
        req = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                reply = json.load(r)
        except urllib.error.HTTPError as e:
            raise OSError(f"{self.url}: HTTP {e.code}" + (f" from the proxy {self.proxy}" if self.proxy else "")) from None
        except ValueError:
            raise OSError(f"{self.url}: the answer is not JSON (is uhttpd-mod-ubus installed?)") from None
        if "error" in reply:
            err = reply["error"]
            code = 6 if err.get("code") == -32002 else 9   # -32002: access denied
            raise UbusError(code, f"{obj} {method}: {err.get('message', '')}".strip())
        result = reply.get("result") or [9]
        if result[0] != 0:
            raise UbusError(result[0], f"{obj} {method}")
        return result[1] if len(result) > 1 else {}

    def login(self):
        try:
            data = self._post(ANON, "session", "login", {"username": self.username, "password": self.password})
        except UbusError as e:
            if e.code == 6:
                raise OSError(f"login failed for {self.username}: wrong password or no such rpcd user") from None
            raise
        self.sid = data["ubus_rpc_session"]
        return data

    def call(self, obj, method, args=None):
        if not self.sid:
            self.login()
        try:
            return self._post(self.sid, obj, method, args)
        except UbusError as e:
            if e.code != 6:
                raise
        # The session expired, or the ACL really forbids the call: a fresh login tells which.
        self.login()
        return self._post(self.sid, obj, method, args)

    # ---------- shortcuts ----------
    def read(self, path):
        """A small file. rpcd reads /proc files one block at a time: about 4 KB is all it gives."""
        return self.call("file", "read", {"path": path}).get("data", "")

    def run(self, command, params=None):
        """Output of a command the ACL allows to run."""
        r = self.call("file", "exec", {"command": command, "params": params or []})
        return r.get("stdout", "")

    def close(self):
        self.sid = None
