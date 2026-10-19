# Bitcoin Node Metrics Collector

A Prometheus exporter for Bitcoin Core and Bitcoin Knots metrics, one process for both nodes (label `node="core"` / `node="knots"`).

## What it needs

- Bitcoin Core and/or Bitcoin Knots on the same machine, with cookie authentication (the default).
- Read access to each node's `.cookie` file: run the collector as the user the nodes run as.
- `txindex=1` and `coinstatsindex=1` on Core for the transaction and UTXO metrics.

## Configuration

Environment variables (or a `.env` file next to `src/`, see `.env.example`):

| Variable | Default |
|----------|---------|
| `BITCOIN_CORE_RPC_HOST` / `BITCOIN_CORE_RPC_PORT` | `127.0.0.1` / `8332` |
| `BITCOIN_CORE_COOKIE_PATH` | `~/.bitcoin/.cookie` |
| `BITCOIN_KNOTS_RPC_HOST` / `BITCOIN_KNOTS_RPC_PORT` | `127.0.0.1` / `8335` |
| `BITCOIN_KNOTS_COOKIE_PATH` | `/mnt/bitcoin-knots/.cookie` |
| `METRICS_PORT` | `9332` |

## Running it with Docker (how the home server runs it)

```yaml
services:
  bitcoin-metrics:
    build: .
    network_mode: host          # reaches the nodes' RPC on 127.0.0.1, serves :9332
    user: "1004:1004"           # the uid:gid the nodes run as, so it can read the cookies
    environment:
      BITCOIN_CORE_COOKIE_PATH: /data/core/.cookie
      BITCOIN_KNOTS_COOKIE_PATH: /data/knots/.cookie
    volumes:
      - /mnt/bitcoin-node:/data/core:ro     # mount the data folders, not the cookie files:
      - /srv/bitcoin-knots:/data/knots:ro   # a node writes a new cookie at every start
    restart: unless-stopped
```

It prints a line for every reading; cap Docker's log size (`logging: options: max-size`).

## Running it without Docker

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python src/collector.py
```

Then `curl http://localhost:9332/metrics`.

## Metrics

The collector exposes the following metrics on port 9332:

### Blockchain Metrics
- `bitcoin_block_height`: Current block height
- `bitcoin_verification_progress`: Blockchain verification progress
- `bitcoin_difficulty`: Current mining difficulty

### Version Metrics
- `bitcoin_version_info`: Bitcoin Core version information (with version as a label)
- `bitcoin_version_major`: Major version number (e.g., 28 for v28.1.0)
- `bitcoin_version_minor`: Minor version number (e.g., 1 for v28.1.0)
- `bitcoin_version_patch`: Patch version number (e.g., 0 for v28.1.0)
- `bitcoin_version_text`: Full version text including build details (with text as a label)
- `bitcoin_version_number`: Version as a decimal number (e.g., 28.1 for v28.1.0)
- `bitcoin_full_version_string`: Metric with description containing the full version string

### Mempool Metrics
- `bitcoin_mempool_size`: Number of transactions in mempool
- `bitcoin_mempool_bytes`: Size of mempool in bytes

### Network Metrics
- `bitcoin_peer_count`: Number of connected peers
- `bitcoin_network_bytes_sent_total`: Total bytes sent
- `bitcoin_network_bytes_received_total`: Total bytes received
- `bitcoin_connections_inbound`: Number of inbound connections
- `bitcoin_connections_outbound`: Number of outbound connections

### UTXO Metrics (requires coinstatsindex)
- `bitcoin_utxo_count`: Total number of unspent transaction outputs
- `bitcoin_utxo_size_bytes`: Total size of UTXO set in bytes

Note: UTXO metrics are collected every 5 minutes to minimize system impact. The initial collection starts 15 seconds after service startup, and each collection typically takes about 1 minute to complete. Regular metrics continue to update every 15 seconds independently of UTXO collection.

### System Metrics
- `bitcoin_memory_usage_bytes`: Memory usage in bytes
- `bitcoin_size_on_disk_bytes`: Total blockchain size on disk in bytes

### Block Metrics
- `bitcoin_block_size_bytes_mean`: Average block size in bytes
- `bitcoin_block_transactions_mean`: Average transactions per block
- `bitcoin_block_interval_seconds`: Time between last two blocks

### Block Timestamp Metrics (Historical Analysis)
- `bitcoin_block_timestamp`: Unix timestamp of block (with block_height label)

**Labels:**
- `node`: The Bitcoin node (core/knots)
- `block_height`: The block height (e.g., "850000", "849999", "849998")

**Example metrics:**
```
bitcoin_block_timestamp{node="core", block_height="850000"} 1703123456
bitcoin_block_timestamp{node="core", block_height="849999"} 1703123000
bitcoin_block_timestamp{node="core", block_height="849998"} 1703122544
```

**Note**: These timestamps can be used to calculate block intervals on your website or in Grafana. For example:
- Interval between blocks 850000 and 849999: `bitcoin_block_timestamp{node="core", block_height="850000"} - bitcoin_block_timestamp{node="core", block_height="849999"}`
- Query for specific block: `bitcoin_block_timestamp{node="core", block_height="850000"}`
- Get all recent blocks: `bitcoin_block_timestamp{node="core"}`

### Fee Metrics
- `bitcoin_fee_high`: Estimated fee rate for high priority - next block (sat/vB)
- `bitcoin_fee_medium`: Estimated fee rate for medium priority - 3 blocks (sat/vB)
- `bitcoin_fee_low`: Estimated fee rate for low priority - 6 blocks (sat/vB)

### Market Metrics
- `bitcoin_price_usd`: Current Bitcoin price in USD

## Security Notes

- The collector runs under the bitcoin user without sudo privileges
- Cookie authentication is used by default for enhanced security
- RPC connection is cached to minimize authentication overhead
- The service is configured to restart automatically on failure
- The service runs as a system service with proper user/group permissions

## Collection Intervals

The collector operates with different intervals for different metric types:
- Regular metrics (blockchain, mempool, network, etc.): Every 15 seconds
- UTXO metrics: Every 5 minutes
- Bitcoin price: Every 15 seconds with regular metrics

The collection processes run in parallel, ensuring that long-running UTXO collection does not block other metrics updates.

## Troubleshooting

1. Log prefixes: `[RPC]` connection, `[Metrics]` regular collection, `[UTXO]` UTXO statistics, `[Startup]` start-up.
2. `Cookie file not found`: the path is wrong, or the collector runs as a user that cannot read it.
3. Is the endpoint up? `curl http://localhost:9332/metrics`

## RPC Commands Used

Currently, the collector uses these Bitcoin Core RPC endpoints:

### Blockchain Information
- `getblockchaininfo`: General blockchain state
  - Used for: block height, verification progress, difficulty, disk size
- `getbestblockhash`: Latest block hash
- `getblock`: Block details
  - Used for: block time calculations

### Mempool & Fees
- `getmempoolinfo`: Mempool statistics
  - Used for: transaction count, mempool size in bytes
- `estimatesmartfee`: Fee estimates
  - Used for: high (1 block), medium (3 blocks), low (6 blocks) priority fees

### Network
- `getnetworkinfo`: Network state
  - Used for: connection count, version information
- `getnettotals`: Network traffic
  - Used for: bytes sent/received
- `getpeerinfo`: Peer details
  - Used for: inbound/outbound connection counts

### Block Statistics
- `getblockstats`: Detailed block metrics
  - Used for: average block size, transactions per block

### Memory & UTXO
- `getmemoryinfo`: Memory usage statistics
- `gettxoutsetinfo`: UTXO set information
  - Used with "muhash" mode for faster UTXO stats

### Version Information
- `getnetworkinfo`: Bitcoin Core version details
  - Used for: version number, user agent string

## Available but Unused RPC Commands

These RPC commands could be useful for additional metrics:

### Mining
- `getmininginfo`: Mining statistics
- `getnetworkhashps`: Network hash rate
- `getblocktemplate`: Current block template

### Transaction Pool
- `getrawmempool`: Detailed mempool transactions
- `getmempoolancestors`: Transaction dependencies
- `getmempooldescendants`: Child transactions
- `getmempoolentry`: Single transaction details

### Network
- `getnodeaddresses`: Known network nodes
- `getnetworkinfo`: More network details
- `getpeerinfo`: Additional peer metrics

### Blockchain
- `getchaintips`: Chain reorganization info
- `getchaintxstats`: Chain transaction statistics
- `getblockstats`: Additional block metrics
  - total_weight
  - total_size
  - subsidy
  - totalfee

### Wallet & UTXO
- `getwalletinfo`: Wallet statistics
- `listunspent`: Detailed UTXO information

## Adding New Metrics

To add new metrics from these endpoints:

1. Define a new Prometheus metric in the collector
2. Add the RPC call in the `collect_regular_metrics()` function
3. Update tests and documentation