import random
import time
from pathlib import Path
from urllib.parse import quote


class ProxyPool:
    """Rotates round-robin through proxies. Each proxy rests for a random cooldown after every use,
    and is benched longer after being blocked or unreachable. If every proxy is resting,
    get() waits until the first one is available again.
    A None entry means a direct connection from our own IP."""

    def __init__(self, proxies: list[str | None], cooldown: tuple[float, float]):
        if not proxies:
            raise ValueError("proxy list is empty")
        self._proxies = proxies
        self._benched_until = {p: 0.0 for p in self._proxies}
        self._cooldown = cooldown
        self._next = 0

    @classmethod
    def from_file(cls, path: Path, cooldown: tuple[float, float]) -> "ProxyPool":
        """One proxy per line, see parse_proxy for the formats. Blank lines and # comments are ignored."""
        proxies = []
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            line = line.split("#", 1)[0].strip()
            if line:
                try:
                    proxies.append(parse_proxy(line))
                except ValueError as e:
                    raise ValueError(f"line {number}: {e}") from None
        return cls(proxies, cooldown)

    @classmethod
    def direct(cls, cooldown: tuple[float, float]) -> "ProxyPool":
        """A pool with only our own IP, so the cooldown applies the same way without proxies."""
        return cls([None], cooldown)

    def __len__(self) -> int:
        return len(self._proxies)

    def get(self) -> str | None:
        """Returns the next available proxy and starts its cooldown."""
        while True:
            now = time.monotonic()
            for i in range(len(self._proxies)):
                proxy = self._proxies[(self._next + i) % len(self._proxies)]
                if self._benched_until[proxy] <= now:
                    self._next = (self._next + i + 1) % len(self._proxies)
                    self.bench(proxy, random.uniform(*self._cooldown))
                    return proxy
            time.sleep(min(self._benched_until.values()) - now)

    def bench(self, proxy: str | None, seconds: float) -> None:
        """Keeps the proxy unused for at least the given seconds (never shortens an existing bench)."""
        self._benched_until[proxy] = max(self._benched_until[proxy], time.monotonic() + seconds)


def _is_host_port(part: str) -> bool:
    host, _, port = part.rpartition(":")
    return bool(host) and port.isdigit()


def _proxy_url(host_port: str, user: str, password: str) -> str:
    # credentials are percent-encoded so characters like @ or : in them can't break the URL; requests decodes them
    return f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host_port}"


def parse_proxy(line: str) -> str:
    """Normalizes the common proxy list formats to SCHEME://USER:PASSWORD@HOST:PORT:
    HOST:PORT, USER:PASSWORD@HOST:PORT, HOST:PORT@USER:PASSWORD, HOST:PORT@USER@PASSWORD,
    HOST:PORT:USER:PASSWORD, or a full URL like socks5://USER:PASSWORD@HOST:PORT (kept as is)."""
    if "://" in line:
        return line
    if "@" in line:
        creds, host_port = line.rsplit("@", 1)
        if _is_host_port(host_port):
            user, _, password = creds.partition(":")
            return _proxy_url(host_port, user, password)
        host_port, creds = line.split("@", 1)
        if _is_host_port(host_port):
            # USER:PASSWORD, or USER@PASSWORD when there is no colon
            user, _, password = creds.partition(":" if ":" in creds else "@")
            return _proxy_url(host_port, user, password)
    else:
        parts = line.split(":")
        if len(parts) == 2 and _is_host_port(line):
            return f"http://{line}"
        if len(parts) >= 4 and parts[1].isdigit():
            return _proxy_url(f"{parts[0]}:{parts[1]}", parts[2], ":".join(parts[3:]))
    # the line holds credentials, so don't echo it
    raise ValueError("unrecognized proxy format, expected e.g. USER:PASSWORD@HOST:PORT or HOST:PORT@USER@PASSWORD")


def as_requests_proxies(proxy: str | None) -> dict[str, str] | None:
    return {"http": proxy, "https": proxy} if proxy else None


def label(proxy: str | None) -> str:
    """Proxy address without credentials, safe to log."""
    return proxy.split("://", 1)[-1].rsplit("@", 1)[-1] if proxy else "own IP"
