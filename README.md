# Bitcoin Node Metrics Collector

A Prometheus exporter for Bitcoin Core metrics.

## Prerequisites

- Python 3.8+
- Bitcoin Core running with cookie authentication enabled
- The `bitcoin` user must have access to the Bitcoin Core cookie file
- Root/sudo access for systemd service setup
- Bitcoin Core indexes enabled:
  - `txindex=1`: Required for transaction lookups
  - `coinstatsindex=1`: Required for UTXO metrics collection

## Installation

### 1. Setup Python Environment (as bitcoin user)
```bash
cd /opt/bitcoin/metrics-collector
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment (as bitcoin user)
Create a `.env` file with the following configuration:
```bash
cat > .env << EOL
# Bitcoin RPC Configuration
BITCOIN_RPC_HOST=127.0.0.1
BITCOIN_RPC_PORT=8332

# Cookie Authentication
BITCOIN_COOKIE_PATH=/mnt/bitcoin-node/.cookie

# Metrics Configuration
METRICS_PORT=9332
METRICS_HOST=0.0.0.0
EOL
```

### 3. Create Systemd Service (as root/sudo user)
Create the service file:
```bash
echo '[Unit]
Description=Bitcoin Metrics Collector
After=bitcoind.service
Requires=bitcoind.service

[Service]
Type=simple
User=bitcoin
Group=bitcoin
WorkingDirectory=/opt/bitcoin/metrics-collector
Environment=PATH=/opt/bitcoin/metrics-collector/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/opt/bitcoin/metrics-collector/venv/bin/python src/collector.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target' | sudo tee /etc/systemd/system/bitcoin-metrics.service
```

### 4. Enable and Start Service (as root/sudo user)
```bash
sudo systemctl daemon-reload
sudo systemctl enable bitcoin-metrics.service
sudo systemctl start bitcoin-metrics.service
```

## Service Management

The metrics collector runs as a systemd service under the bitcoin user. To manage the service:

```bash
# Check service status
sudo systemctl status bitcoin-metrics.service

# Stop the service
sudo systemctl stop bitcoin-metrics.service

# Start the service
sudo systemctl start bitcoin-metrics.service

# Restart the service
sudo systemctl restart bitcoin-metrics.service

# View logs
sudo journalctl -u bitcoin-metrics.service
```

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

1. If the service fails to start, check the logs:
```bash
# View all logs
sudo journalctl -u bitcoin-metrics.service

# Follow logs in real-time
sudo journalctl -u bitcoin-metrics.service -f
```

2. Understanding the log prefixes:
- `[RPC]`: RPC connection related messages
- `[Metrics]`: Regular metrics collection
- `[UTXO]`: UTXO statistics collection
- `[Startup]`: Service startup messages

3. Verify the cookie file permissions:
```bash
ls -l /mnt/bitcoin-node/.cookie
```

4. Ensure Bitcoin Core is running:
```bash
sudo systemctl status bitcoind
```

5. Check if the metrics endpoint is accessible:
```bash
curl http://localhost:9332/metrics
```

6. Verify Python environment:
```bash
cd /opt/bitcoin/metrics-collector
source venv/bin/activate
python3 -c "import bitcoinrpc, prometheus_client, dotenv, aiohttp"
```

7. Test the collector directly:
```bash
cd /opt/bitcoin/metrics-collector
source venv/bin/activate
python3 src/collector.py
```

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

## Grafana Dashboard

The collector exposes metrics that can be visualized in Grafana. A sample dashboard is provided in `../home-server/services/grafana/bitcoin-node-dashboard.json`.

### Version Metrics in Grafana

To display Bitcoin Core version in Grafana:

1. Use `bitcoin_version_number` for a simple numeric version display (e.g., 28.1)
2. To display as "v28.1", use value mapping or transformations:
   
   **Method 1: Value Mapping**
   - Add a new panel using the `bitcoin_version_number` metric
   - Add value mapping with regex `/.*/` and display text `v${__value}`
   
   **Method 2: Transformation**
   - Add a transformation to add a field using binary operation
   - Select "Concat" operation with parameter "v"

3. For full version information, use `bitcoin_version_text` or `bitcoin_version_info` with label support enabled

## Adding New Metrics

To add new metrics from these endpoints:

1. Define a new Prometheus metric in the collector
2. Add the RPC call in the `collect_regular_metrics()` function
3. Update tests and documentation