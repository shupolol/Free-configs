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


## FlClash / Clash Meta

- `output/flclash.yaml`: a ready-to-import profile for FlClash, Clash.Meta, Clash Verge, Mihomo Party and Stash

In FlClash: **Profiles → New → URL**, then paste:

`` https://raw.githubusercontent.com/shupolol/Free-configs/refs/heads/main/output/flclash.yaml ``

The profile has a `PROXY` group with every node, an `AUTO` group that picks the lowest-latency one, and private-address rules that stay direct.

This file is built from `all_configs.txt`, so every node in it already passed the Xray test in the same run. Two kinds of config get dropped on the way, and both are counted in `output/stats.json`:

- The same server published more than once. One server fronted by a CDN often appears dozens of times under different names, so those collapse into a single node.
- Transports Clash Meta has no equivalent for, such as XHTTP and HTTPUpgrade.

## Transport security ranking

Configs are sorted by the first matching tier:

1. Reality
2. TLS with WebSocket or gRPC, certificate verified
3. TLS, certificate verified
4. TLS that skips certificate verification (only when the config's own link sets `allowInsecure`)
5. No TLS

A config counts as skipping certificate verification only if its own link says so. Tests never turn it on by default.

## Channels

The configs come from public Telegram channels, listed in `scripts/channels.txt`, one channel name per line. Every channel is read on each run.

- Only the public preview page (`t.me/s/<channel>`) is read, because `t.me/<channel>` shows a "sign in to Telegram" wall and returns no posts. Older pages are followed up to `TELEGRAM_PAGES`. Only channels with public previews work.
- `scripts/refresh_channels.py` checks every channel by reading its preview and looking for config links. The checks run in parallel and stay inside a time budget of five minutes, so a channel that stopped posting costs one request instead of a full scrape. With `--update` it rewrites `scripts/channels.txt` with the channels that still work and adds any new working channel it finds. The result of each check is written to `output/channel_status.json`.
- `scripts/check_channels.py` goes one step further: it runs the first configs of every channel through xray and reports how many answered, so a channel that is online but serves dead configs is visible. The result is written to `output/channel_working_check.json`.

Duplicates are removed by server identity, so the same config under two different names is only tested once.

The channels are scraped in parallel, and the links of all of them form one pool. A run only tests what it reaches within `TIME_BUDGET`, so the channels that post the most are not the only ones that get through.

## How a run works

1. Load the previous output list. These configs go first in the test queue.
2. Read the Telegram channels and the Mahsa configs, and add any new links.
3. Start a separate Xray process for each config and send a request through it to `https://www.gstatic.com/generate_204`. A config passes if it gets a 204 or 200 response. Each passing config is probed `PROBES` times, and the lowest delay is kept.
4. Look up each working server's country by its hostname or IP. Servers with unknown countries are dropped.
5. Sort, cap, rename (for example `🇩🇪 Germany 001`, where the number is the rank in that file), and write the list.
6. Write the counts to `output/stats.json`.
7. Convert the finished list to `output/flclash.yaml`. This reads the tested list, so it never tests anything again.

Previous configs that no longer work are removed. Previous configs that still work are kept, and new working configs are added.

The builders run one after the other. Each one after the first reuses the earlier test results from a cache file (`CACHE_PATH`, default `/tmp/config_test_cache.json`), so it doesn't retest the same configs.

## Mahsa configs

`scripts/build_mahsa.py` downloads the [MahsaFreeConfig](https://github.com/mahsanet/MahsaFreeConfig) repository and turns every config in it into a readable form. The repository keeps its subscriptions as encoded blobs, so this decodes them and writes:

- `output/mahsa_configs.txt`: the share links, one per line
- `output/mahsa_readable.txt`: the same configs as readable text, field by field (protocol, server, transport, security, sni, credential)
- `output/mahsa_xray_configs.json`: xray configurations, one object per config, ready to import
- `output/mahsa_stats.json`: how many configs were found, and which files hold no share links

The Mahsa configs are also written to `output/mahsa_configs.txt`, and the builders read that file, so they take part in the regular test and land in `output/all_configs.txt` and the Clash profile.

## Files

- `scripts/channels.txt`: the Telegram channel list, kept up to date by `scripts/refresh_channels.py`
- `scripts/configlib.py`: shared code for collecting, converting links, testing, geolocation, and output
- `scripts/clashlib.py`: converts a share link into a Clash Meta proxy and writes YAML
- `scripts/build_all_configs.py`: builds `output/all_configs.txt`
- `scripts/build_preferred.py`: builds `output/preferred_secure.txt` and `output/working_configs.txt`
- `scripts/build_clash.py`: builds `output/flclash.yaml` from `output/all_configs.txt`
- `scripts/build_mahsa.py`: builds the readable Mahsa configs
- `scripts/refresh_channels.py`: checks every channel and updates `scripts/channels.txt`
- `scripts/check_channels.py`: tests a few configs of every channel through xray
- `scripts/check_existing.py`: retests the current output files without the cache and writes `output/existing_check.json`
- `.github/workflows/test-configs.yml`: installs Xray, runs the builders on a schedule, and commits `output/`
- `.github/workflows/refresh-channels.yml`: checks the channels daily and commits the updated list

## Running it yourself

Requirements: Python 3.10 or newer, `curl`, and `xray` on your PATH (or set `XRAY_BIN`). Nothing else: the Clash profile is written without a YAML library.

From the repository root:

    python scripts/build_all_configs.py
    python scripts/build_preferred.py
    python scripts/build_clash.py
    python scripts/build_mahsa.py
    python scripts/check_existing.py
    python scripts/refresh_channels.py --update
    python scripts/check_channels.py

`build_clash.py` only converts, so it runs on its own in a second or two once a list exists.

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
| `TELEGRAM_WORKERS` | `16` | Channels scraped in parallel |
| `TELEGRAM_TIMEOUT` | `20` | Seconds allowed for one Telegram page |
| `CHANNEL_TIME_BUDGET` | `300` | Seconds the channel check and the channel test may take |
| `CHANNEL_FETCH_TIMEOUT` | `20` | Seconds allowed for one channel fetch |
| `CHANNEL_WORKERS` | `16` | Channels checked in parallel |
| `CHANNEL_SAMPLE` | `3` | Configs tested per channel by `check_channels.py` |
| `WORKERS` | `48` | Configs tested in parallel |
| `TEST_URL` | `https://www.gstatic.com/generate_204` | URL requested through each config |
| `TEST_TIMEOUT` | `5` | Seconds allowed per request |
| `PROBES` | `2` | Requests per config; the lowest delay is kept |
| `DEBUG_FAILURES` | `20` | Failure messages to print |
| `CACHE_PATH` | `/tmp/config_test_cache.json` | Test result cache shared by the builders |
| `CACHE_TTL` | `3600` | Seconds a cached result is reused |
| `CLASH_SOURCE` | `all_configs.txt` | Tested list that `build_clash.py` converts |
| `CLASH_AUTO_TOP_N` | `60` | Nodes put in the profile's automatic latency group |
| `CLASH_AUTO_URL` | value of `TEST_URL` | URL the automatic group measures latency against |

In GitHub Actions, `PREFERRED_COUNTRIES` is read from the repository variable with the same name (Settings → Secrets and variables → Actions → Variables).

## Output

`output/stats.json` records, for each list, how many configs were tested, how many were working, how many were written, and how many carried over from the previous run. Under `flclash` it also records how many duplicate servers were collapsed and why any config was skipped. `output/existing_check.json` lists each config checked by `check_existing.py` with its name, host, tier, pass or fail, and delay. `output/channel_status.json` records the result of every channel check, and `output/channel_working_check.json` how many configs of each channel answered the xray test.

## Limitations

- These configs come from third parties. They aren't audited, and nothing here checks whether a server is trustworthy or logs traffic.
- A config that passes the test may still fail on your network or get blocked later.
- Country lookups use the free tier of ip-api.com, which is rate limited and licensed for non-commercial use. Countries can be wrong for servers behind CDNs or shared hostnames.
- Telegram scraping depends on public preview pages, which Telegram can change at any time.
- Check your local laws before using a proxy or circumvention tool.

## Note

these configs come from telegram, and other subscriptions, there is a possibillity that these aren't safe, use at your own risk.
