"""Fixed-block JSON-RPC with strict result/error checks."""
import json
import urllib.request
import urllib.error


class RpcError(RuntimeError):
    pass


class RPC:
    def __init__(self, url: str, timeout: float = 18):
        if not (url.startswith("https://") or url.startswith("http://127.0.0.1:")
                or url.startswith("http://localhost:")):
            raise ValueError("RPC URL must use HTTPS, except localhost forks")
        self.url, self.timeout, self.seq = url, timeout, 0

    def call(self, method: str, params: list):
        self.seq += 1
        payload = json.dumps({"jsonrpc": "2.0", "id": self.seq,
                              "method": method, "params": params}).encode()
        req = urllib.request.Request(self.url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "flash-arb-research/0.2"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.load(resp)
        except (OSError, TimeoutError, ValueError) as e:
            raise RpcError(f"{method} HTTP/RPC failed: {e}") from e
        if not isinstance(result, dict) or result.get("error") is not None:
            raise RpcError(f"{method} RPC error: {result!r}")
        if "result" not in result:
            raise RpcError(f"{method} missing result")
        return result["result"]

    def eth_call(self, to: str, data: str, block: str, sender: str | None = None) -> str:
        tx = {"to": to, "data": data}
        if sender is not None:
            tx["from"] = sender
        return self.call("eth_call", [tx, block])

    def chain_id(self) -> int:
        return int(self.call("eth_chainId", []), 16)

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def gas_price(self) -> int:
        return int(self.call("eth_gasPrice", []), 16)

    def estimate_gas(self, tx: dict, block: str = "latest") -> int:
        return int(self.call("eth_estimateGas", [tx, block]), 16)

    def batch_eth_call(self, calls: list[tuple[str, str, str]], chunk_size: int = 50) -> list[str | None]:
        """Batch static eth_calls at individually specified block tags.

        None means a single call reverted/errored; a broken batch transport fails closed.
        """
        if not 1 <= chunk_size <= 100:
            raise ValueError("chunk_size must be 1..100")
        output: list[str | None] = []
        for start in range(0, len(calls), chunk_size):
            chunk = calls[start:start + chunk_size]
            requests = []
            for target, data, block in chunk:
                self.seq += 1
                requests.append({"jsonrpc":"2.0", "id":self.seq, "method":"eth_call",
                                 "params":[{"to":target, "data":data},block]})
            req = urllib.request.Request(self.url, data=json.dumps(requests).encode(),
                method="POST", headers={"Content-Type":"application/json",
                                        "User-Agent":"flash-arb-research/0.2"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout * 2) as response:
                    replies = json.load(response)
            except (OSError, TimeoutError, ValueError) as e:
                raise RpcError(f"Batch RPC failed: {e}") from e
            if not isinstance(replies, list) or len(replies) != len(requests):
                raise RpcError("RPC endpoint does not support complete batch responses")
            by_id = {r.get("id"):r for r in replies if isinstance(r,dict)}
            if len(by_id) != len(requests):
                raise RpcError("Duplicate or missing batch response IDs")
            for call in requests:
                ans = by_id.get(call["id"])
                if ans is None:
                    raise RpcError("Missing batch response")
                val = ans.get("result")
                if ans.get("error") is not None:
                    output.append(None)
                elif not isinstance(val, str) or not val.startswith("0x"):
                    raise RpcError("Malformed batch result")
                else:
                    output.append(val)
        return output
