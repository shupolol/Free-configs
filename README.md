# Free configs

Automatically tested V2Ray configs. A GitHub Actions workflow pulls links from public sources, runs each one through Xray, and commits the ones that connect to `output/`.

## Subscription link

Add this to your V2Ray client as a subscription:

```
https://raw.githubusercontent.com/shupolol/Free-configs/refs/heads/main/output/working_configs.txt
```

## Files

- `output/working_configs.txt`: working links, one per line
- `output/working_configs_base64.txt`: the same list, base64-encoded
- `output/stats.json`: how many configs were tested and how many worked

## How it works

Every 20 minutes the workflow:

1. Downloads links from 18 public subscription files.
2. Shuffles them and tests as many as it can in 15 minutes. Each config gets its own Xray process and must return a 204 or 200 from `https://www.gstatic.com/generate_204` through the proxy.
3. Looks up each working server's country and drops the ones it can't place.
4. Ranks the rest by country distance from Iran, then by transport type (Reality first, then TLS with WebSocket or gRPC, then plain TLS, then no TLS), then by latency.
5. Keeps the top 200 and names them like `🇩🇪 Germany 001`. The number is the rank.
6. Commits the results.

Testing runs on GitHub's servers, not inside Iran. A config that works here can still be blocked on your connection, so if one fails, try the next one.

## Running it yourself

Requires Python 3.10+ and `xray` on your PATH:

```
python scripts/test_configs.py
```

Settings such as `TOP_N`, `TIME_BUDGET`, `WORKERS`, and `TEST_TIMEOUT` are read from environment variables. The defaults are in `scripts/test_configs.py`.

## Sources

The list is in `SOURCES` in `scripts/test_configs.py`.
