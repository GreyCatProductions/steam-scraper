import random
import time
from pathlib import Path


class ProxyPool:
    """Rotates round-robin through proxies. Each proxy rests for a random cooldown after every use,
    and is benched longer after being blocked or unreachable. If every proxy is resting,
    get() waits until the first one is available again.
    A None entry means a direct connection from our own IP."""

    def __init__(self, proxies: list[str | None], cooldown: tuple[float, float]):
        if not proxies:
            raise ValueError("proxy list is empty")
        self._proxies = [p if p is None or p.startswith("http") else f"http://{p}" for p in proxies]
        self._benched_until = {p: 0.0 for p in self._proxies}
        self._cooldown = cooldown
        self._next = 0

    @classmethod
    def from_file(cls, path: Path, cooldown: tuple[float, float]) -> "ProxyPool":
        """One proxy per line (e.g. USER:PASSWORD@IP:HTTP_PORT). Blank lines and # comments are ignored."""
        lines = (line.split("#", 1)[0].strip() for line in path.read_text().splitlines())
        return cls([line for line in lines if line], cooldown)

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


def as_requests_proxies(proxy: str | None) -> dict[str, str] | None:
    return {"http": proxy, "https": proxy} if proxy else None


def label(proxy: str | None) -> str:
    """Proxy address without credentials, safe to log."""
    return proxy.split("://", 1)[-1].rsplit("@", 1)[-1] if proxy else "own IP"
