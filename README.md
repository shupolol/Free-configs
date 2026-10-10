# Free configs

Automatically tested V2Ray configs (VLESS, VMess, Trojan, Shadowsocks). A GitHub Actions workflow collects links from public subscription files and Telegram channels, runs each one through Xray, and commits the ones that connect to `output/`. It runs every 20 minutes.

Testing runs on GitHub's servers, so a config that works there may still fail on your network. If one fails, try the next one in the list.

## Subscription links

Add one of these to your V2Ray client as a subscription URL:

- `output/all_configs.txt`: up to 2000 working configs, most secure transport first
- `output/preferred_secure.txt`: up to 200 working configs, ranked by the country order in `PREFERRED_COUNTRIES`, then by transport security
- `output/working_configs.txt`: same content as `preferred_secure.txt`, kept so existing subscriptions keep working

For example:

`` https://raw.githubusercontent.com/shupolol/Free-configs/refs/heads/main/output/all_configs.txt ``


Each list is also published base64-encoded, as `*_base64.txt`, for clients that expect that format.

## Transport security ranking

Configs are sorted by the first matching tier:

1. Reality
2. TLS with WebSocket or gRPC, certificate verified
3. TLS, certificate verified
4. TLS that skips certificate verification (only when the config's own link sets `allowInsecure`)
5. No TLS

A config counts as skipping certificate verification only if its own link says so. Tests never turn it on by default.

## Sources

- Subscription files are listed in `SOURCES` in `scripts/configlib.py`. They come from several public GitHub repositories. Each one is fetched on every run, decoded from base64 if needed, and its links are added to the candidate pool.
- Telegram channels are listed in `TELEGRAM_CHANNELS` in the same file. The public preview page (`t.me/s/<channel>`) is read, and older pages are followed up to `TELEGRAM_PAGES`. Only channels with public previews work.

Duplicates are removed by server identity, so the same config under two different names is only tested once.

## How a run works

1. Load the previous output list. These configs go first in the test queue.
2. Fetch the subscription files and Telegram channels, and add any new links.
3. Start a separate Xray process for each config and send a request through it to `https://www.gstatic.com/generate_204`. A config passes if it gets a 204 or 200 response. Each passing config is probed `PROBES` times, and the lowest delay is kept.
4. Look up each working server's country by its hostname or IP. Servers with unknown countries are dropped.
5. Sort, cap, rename (for example `🇩🇪 Germany 001`, where the number is the rank in that file), and write the list.
6. Write the counts to `output/stats.json`.

Previous configs that no longer work are removed. Previous configs that still work are kept, and new working configs are added.

The two builders run one after the other. The second one reuses the first one's test results from a cache file (`CACHE_PATH`, default `/tmp/config_test_cache.json`), so it doesn't retest the same configs.

## Files

- `scripts/configlib.py`: shared code for collecting, converting links, testing, geolocation, and output
- `scripts/build_all_configs.py`: builds `output/all_configs.txt`
- `scripts/build_preferred.py`: builds `output/preferred_secure.txt` and `output/working_configs.txt`
- `scripts/check_existing.py`: retests the current output files without the cache and writes `output/existing_check.json`
- `.github/workflows/test-configs.yml`: installs Xray, runs both builders on a schedule, and commits `output/`

## Running it yourself

Requirements: Python 3.10 or newer, `curl`, and `xray` on your PATH (or set `XRAY_BIN`).

From the repository root:

python scripts/build_all_configs.py
python scripts/build_preferred.py
python scripts/check_existing.py


To check only one file: `python scripts/check_existing.py all_configs.txt`

## Settings

Set these as environment variables. Defaults are in `scripts/configlib.py` and in each builder.

| Variable | Default | Meaning |
|---|---|---|
| `OUTPUT_DIR` | `output` | Where lists and stats are written |
| `XRAY_BIN` | `xray` | Path to the Xray binary |
| `ALL_TOP_N` | `2000` | Maximum size of `all_configs.txt` |
| `PREFERRED_TOP_N` | `200` | Maximum size of `preferred_secure.txt` |
| `PREFERRED_COUNTRIES` | empty | Country codes in priority order, for example `DE,NL,FR`. Empty means no country preference |
| `TELEGRAM_PAGES` | `5` | Pages of each Telegram channel to read |
| `TIME_BUDGET` | `600` | Seconds each builder may spend testing |
| `WORKERS` | `48` | Configs tested in parallel |
| `TEST_URL` | `https://www.gstatic.com/generate_204` | URL requested through each config |
| `TEST_TIMEOUT` | `5` | Seconds allowed per request |
| `PROBES` | `2` | Requests per config; the lowest delay is kept |
| `DEBUG_FAILURES` | `20` | Failure messages to print |
| `CACHE_PATH` | `/tmp/config_test_cache.json` | Test result cache shared by the builders |
| `CACHE_TTL` | `3600` | Seconds a cached result is reused |

In GitHub Actions, `PREFERRED_COUNTRIES` is read from the repository variable with the same name (Settings → Secrets and variables → Actions → Variables).

## Output

`output/stats.json` records, for each list, how many configs were tested, how many were working, how many were written, and how many carried over from the previous run. `output/existing_check.json` lists each config checked by `check_existing.py` with its name, host, tier, pass or fail, and delay.

## Limitations

- These configs come from third parties. They aren't audited, and nothing here checks whether a server is trustworthy or logs traffic.
- A config that passes the test may still fail on your network or get blocked later.
- Country lookups use the free tier of ip-api.com, which is rate limited and licensed for non-commercial use. Countries can be wrong for servers behind CDNs or shared hostnames.
- Telegram scraping depends on public preview pages, which Telegram can change at any time.
- Check your local laws before using a proxy or circumvention tool.

## License

No license is set. Add one before reusing the code.
