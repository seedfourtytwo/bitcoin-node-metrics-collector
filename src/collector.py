#!/usr/bin/env python3
import os
import time
import json
from pathlib import Path
from prometheus_client import start_http_server, Gauge, Counter
from bitcoinrpc.authproxy import AuthServiceProxy
from dotenv import load_dotenv
import aiohttp
import asyncio
from decimal import Decimal
import sys
import math

# Load environment variables
load_dotenv()

# Configuration for dual nodes
NODES = {
    'core': {
        'rpc_host': os.getenv('BITCOIN_CORE_RPC_HOST', '127.0.0.1'),
        'rpc_port': int(os.getenv('BITCOIN_CORE_RPC_PORT', '8332')),
        'auth_type': 'cookie',
        'cookie_path': os.getenv('BITCOIN_CORE_COOKIE_PATH', '~/.bitcoin/.cookie'),
        'supports_utxo_stats': True,
        'supports_block_stats': True
    },
    'knots': {
        'rpc_host': os.getenv('BITCOIN_KNOTS_RPC_HOST', '127.0.0.1'),
        'rpc_port': int(os.getenv('BITCOIN_KNOTS_RPC_PORT', '8335')),
        'auth_type': 'cookie',
        'cookie_path': os.getenv('BITCOIN_KNOTS_COOKIE_PATH', '/mnt/bitcoin-knots/.cookie'),
        'supports_utxo_stats': True,  # Enable UTXO collection for Knots
        'supports_block_stats': False
    }
}

# Global RPC connections cache
RPC_CONNECTIONS = {}

# Prometheus port for metrics
METRICS_PORT = int(os.getenv('METRICS_PORT', '9332'))

# Prometheus metrics with node labels
BITCOIN_BLOCK_HEIGHT = Gauge('bitcoin_block_height', 'Current block height', ['node'])
BITCOIN_VERIFICATION_PROGRESS = Gauge('bitcoin_verification_progress', 'Blockchain verification progress', ['node'])
BITCOIN_DIFFICULTY = Gauge('bitcoin_difficulty', 'Current mining difficulty', ['node'])
BITCOIN_MEMPOOL_SIZE = Gauge('bitcoin_mempool_size', 'Number of transactions in mempool', ['node'])
BITCOIN_MEMPOOL_BYTES = Gauge('bitcoin_mempool_bytes', 'Size of mempool in bytes', ['node'])
BITCOIN_MEMPOOL_USAGE = Gauge('bitcoin_mempool_usage', 'Memory usage of mempool in bytes', ['node'])
BITCOIN_PEER_COUNT = Gauge('bitcoin_peer_count', 'Number of connected peers', ['node'])
BITCOIN_MEMORY_USAGE = Gauge('bitcoin_memory_usage_bytes', 'Memory usage in bytes', ['node'])
BITCOIN_PRICE_USD = Gauge('bitcoin_price_usd', 'Current Bitcoin price in USD')
BITCOIN_TIME_SINCE_LAST_BLOCK = Gauge('bitcoin_time_since_last_block_seconds', 'Time since last block in seconds', ['node'])
BITCOIN_FEE_HIGH = Gauge('bitcoin_fee_high', 'Estimated fee rate for high priority - next block (sat/vB)', ['node'])
BITCOIN_FEE_MEDIUM = Gauge('bitcoin_fee_medium', 'Estimated fee rate for medium priority - 3 blocks (sat/vB)', ['node'])
BITCOIN_FEE_LOW = Gauge('bitcoin_fee_low', 'Estimated fee rate for low priority - 6 blocks (sat/vB)', ['node'])
BITCOIN_SIZE_ON_DISK = Gauge('bitcoin_size_on_disk_bytes', 'Total blockchain size on disk in bytes', ['node'])
BITCOIN_VERSION = Gauge('bitcoin_version_info', 'Bitcoin version info', ['node', 'version'])
# Additional version metrics for easier display in Grafana
BITCOIN_VERSION_MAJOR = Gauge('bitcoin_version_major', 'Bitcoin major version', ['node'])
BITCOIN_VERSION_MINOR = Gauge('bitcoin_version_minor', 'Bitcoin minor version', ['node'])
BITCOIN_VERSION_PATCH = Gauge('bitcoin_version_patch', 'Bitcoin patch version', ['node'])
BITCOIN_VERSION_TEXT = Gauge('bitcoin_version_text', 'Bitcoin version as text', ['node', 'text'])

# Network metrics
BITCOIN_NET_BYTES_SENT = Gauge('bitcoin_network_bytes_sent_total', 'Total bytes sent', ['node'])
BITCOIN_NET_BYTES_RECV = Gauge('bitcoin_network_bytes_received_total', 'Total bytes received', ['node'])
BITCOIN_CONN_INBOUND = Gauge('bitcoin_connections_inbound', 'Number of inbound connections', ['node'])
BITCOIN_CONN_OUTBOUND = Gauge('bitcoin_connections_outbound', 'Number of outbound connections', ['node'])

# Block metrics
BITCOIN_BLOCK_SIZE_MEAN = Gauge('bitcoin_block_size_bytes_mean', 'Average block size in bytes', ['node'])
BITCOIN_BLOCK_TXS_MEAN = Gauge('bitcoin_block_transactions_mean', 'Average transactions per block', ['node'])
BITCOIN_BLOCK_INTERVAL = Gauge('bitcoin_block_interval_seconds', 'Time between last two blocks', ['node'])

# Block timestamp metrics for historical analysis (up to 10 blocks)
BITCOIN_BLOCK_TIMESTAMP = Gauge('bitcoin_block_timestamp', 'Unix timestamp of block', ['node', 'block_height'])

# UTXO metrics
BITCOIN_UTXO_COUNT = Gauge('bitcoin_utxo_count', 'Total number of unspent transaction outputs', ['node'])
BITCOIN_UTXO_SIZE = Gauge('bitcoin_utxo_size_bytes', 'Total size of UTXO set in bytes', ['node'])

# Pruning metrics
BITCOIN_PRUNING_ENABLED = Gauge('bitcoin_pruning_enabled', 'Whether pruning is enabled', ['node'])
BITCOIN_PRUNING_HEIGHT = Gauge('bitcoin_pruning_height', 'First pruned block height', ['node'])
BITCOIN_PRUNING_TARGET = Gauge('bitcoin_pruning_target_bytes', 'Target size for pruning in bytes', ['node'])
BITCOIN_BLOCKCHAIN_SIZE_PRUNED = Gauge('bitcoin_blockchain_size_pruned_bytes', 'Actual pruned blockchain size in bytes', ['node'])
BITCOIN_BLOCKCHAIN_SIZE_FULL = Gauge('bitcoin_blockchain_size_full_bytes', 'Full blockchain size in bytes', ['node'])

# Legacy RPC connection (will be replaced)
RPC_CONNECTION = None

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

def get_rpc_connection(node_name):
    """Get or create RPC connection for a specific node with improved error handling"""
    global RPC_CONNECTIONS
    
    # Return cached connection if it exists and is valid
    if node_name in RPC_CONNECTIONS and RPC_CONNECTIONS[node_name] is not None:
        try:
            # Test the connection with a simple call
            RPC_CONNECTIONS[node_name].getblockchaininfo()
            return RPC_CONNECTIONS[node_name]
        except Exception as e:
            print(f"[RPC] Cached connection for {node_name} is stale, creating new one: {str(e)}", flush=True)
            RPC_CONNECTIONS[node_name] = None
    
    node_config = NODES.get(node_name)
    if not node_config:
        print(f"[RPC] Node '{node_name}' not configured.", flush=True)
        RPC_CONNECTIONS[node_name] = None
        return None

    try:
        if node_config['auth_type'] == 'cookie':
            # Try cookie authentication
            cookie_path = os.path.expanduser(node_config['cookie_path'])
            if os.path.exists(cookie_path):
                with open(cookie_path, 'r') as f:
                    cookie_content = f.read().strip()
                    # Parse cookie content
                    if ':' in cookie_content:
                        username, password = cookie_content.split(':', 1)
                        # Create connection with appropriate timeouts
                        rpc_url = f"http://{username}:{password}@{node_config['rpc_host']}:{node_config['rpc_port']}"
                        print(f"[RPC] Creating new connection for {node_name}: {rpc_url}", flush=True)
                        
                        # Set different timeouts for different operations
                        RPC_CONNECTIONS[node_name] = AuthServiceProxy(
                            rpc_url, 
                            timeout=60  # 60 seconds for regular operations
                        )
                        
                        # Test the connection immediately
                        try:
                            test_result = RPC_CONNECTIONS[node_name].getblockchaininfo()
                            print(f"[RPC] Successfully established and tested connection for {node_name}", flush=True)
                            return RPC_CONNECTIONS[node_name]
                        except Exception as test_e:
                            print(f"[RPC] Connection test failed for {node_name}: {str(test_e)}", flush=True)
                            RPC_CONNECTIONS[node_name] = None
                            raise Exception(f"Connection test failed: {str(test_e)}")
                    else:
                        print(f"[RPC] Invalid cookie format for {node_name}", flush=True)
                        raise Exception("Invalid cookie format")
            else:
                print(f"[RPC] Cookie file not found for {node_name}: {cookie_path}", flush=True)
                raise Exception("Cookie file not found")
        elif node_config['auth_type'] == 'userpass':
            # Use username/password authentication
            username = node_config['username']
            password = node_config['password']
            if username and password:
                print(f"[RPC] Using username/password authentication for {node_name}", flush=True)
                rpc_url = f"http://{username}:{password}@{node_config['rpc_host']}:{node_config['rpc_port']}"
                
                RPC_CONNECTIONS[node_name] = AuthServiceProxy(
                    rpc_url, 
                    timeout=60
                )
                
                # Test the connection
                try:
                    test_result = RPC_CONNECTIONS[node_name].getblockchaininfo()
                    print(f"[RPC] Successfully established and tested connection for {node_name}", flush=True)
                    return RPC_CONNECTIONS[node_name]
                except Exception as test_e:
                    print(f"[RPC] Connection test failed for {node_name}: {str(test_e)}", flush=True)
                    RPC_CONNECTIONS[node_name] = None
                    raise Exception(f"Connection test failed: {str(test_e)}")
            else:
                raise Exception("Username or password not configured")
        else:
            raise Exception(f"Unsupported authentication type: {node_config['auth_type']}")
            
    except Exception as e:
        print(f"[RPC] Authentication failed for {node_name}: {str(e)}", flush=True)
        RPC_CONNECTIONS[node_name] = None
        raise Exception(f"No valid authentication method available for {node_name}")

def safe_rpc_call(node_name, rpc_method, *args, **kwargs):
    """Make a safe RPC call with automatic reconnection on failure"""
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            rpc = get_rpc_connection(node_name)
            if not rpc:
                raise Exception("No RPC connection available")
            
            # Make the RPC call
            if args and kwargs:
                result = getattr(rpc, rpc_method)(*args, **kwargs)
            elif args:
                result = getattr(rpc, rpc_method)(*args)
            elif kwargs:
                result = getattr(rpc, rpc_method)(**kwargs)
            else:
                result = getattr(rpc, rpc_method)()
            
            return result
            
        except Exception as e:
            error_msg = str(e)
            print(f"[RPC] Attempt {attempt + 1}/{max_retries} failed for {node_name}.{rpc_method}: {error_msg}", flush=True)
            
            if attempt < max_retries - 1:
                # Clear the failed connection and retry
                if node_name in RPC_CONNECTIONS:
                    RPC_CONNECTIONS[node_name] = None
                print(f"[RPC] Retrying in {retry_delay} seconds...", flush=True)
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                # Final attempt failed
                print(f"[RPC] All {max_retries} attempts failed for {node_name}.{rpc_method}", flush=True)
                raise Exception(f"RPC call failed after {max_retries} attempts: {error_msg}")

async def collect_pruning_metrics(node_name, node_config, labels):
    """Collect pruning-specific metrics for each node"""
    try:
        print(f"[Pruning] Starting pruning metrics collection for {node_name}...", flush=True)
        
        blockchain_info = safe_rpc_call(node_name, 'getblockchaininfo')
        
        # Pruning status
        pruning_enabled = blockchain_info.get('pruned', False)
        BITCOIN_PRUNING_ENABLED.labels(node=node_name).set(1 if pruning_enabled else 0)
        
        if pruning_enabled:
            # Prune height (first pruned block)
            prune_height = blockchain_info.get('pruneheight', 0)
            BITCOIN_PRUNING_HEIGHT.labels(node=node_name).set(prune_height)
            
            # Prune target size
            prune_target = blockchain_info.get('prune_target', 0)
            BITCOIN_PRUNING_TARGET.labels(node=node_name).set(prune_target)
            
            # Actual pruned size
            actual_size = blockchain_info.get('size_on_disk', 0)
            BITCOIN_BLOCKCHAIN_SIZE_PRUNED.labels(node=node_name).set(actual_size)
            
            print(f"[Pruning] {node_name} is pruned - Height: {prune_height}, Target: {prune_target}, Size: {actual_size}", flush=True)
        else:
            # Full blockchain size
            full_size = blockchain_info.get('size_on_disk', 0)
            BITCOIN_BLOCKCHAIN_SIZE_FULL.labels(node=node_name).set(full_size)
            
            print(f"[Pruning] {node_name} is not pruned - Full size: {full_size}", flush=True)
            
    except Exception as e:
        print(f"[Pruning] Failed to collect pruning metrics for {node_name}: {str(e)}", flush=True)

async def collect_utxo_stats(node_name):
    """Collect UTXO statistics independently for a specific node"""
    try:
        print(f"[UTXO] Starting UTXO stats collection for {node_name}...", flush=True)
        
        # First check if indexes are ready
        try:
            index_info = safe_rpc_call(node_name, 'getindexinfo')
            print(f"[UTXO] Index info: {json.dumps(index_info, indent=2)}", flush=True)
            
            if not isinstance(index_info, dict):
                print("[UTXO] Failed to get index info - not a dictionary", flush=True)
                return
            
            # Check if coinstatsindex is ready
            coinstats = index_info.get('coinstatsindex', {})
            if not coinstats.get('synced', False):
                print(f"[UTXO] Coinstatsindex not ready. Status: {json.dumps(coinstats, indent=2)}", flush=True)
                return
            
            print("[UTXO] Coinstatsindex is ready, fetching UTXO stats (this may take a few minutes)...", flush=True)
            
            # Get UTXO stats using gettxoutsetinfo
            try:
                start_time = time.time()
                utxo_info = safe_rpc_call(node_name, 'gettxoutsetinfo')
                collection_time = time.time() - start_time
                print(f"[UTXO] Raw UTXO info: {json.dumps(utxo_info, indent=2, cls=DecimalEncoder)}", flush=True)
                
                if isinstance(utxo_info, dict):
                    # Extract and set metrics
                    txouts = utxo_info.get('txouts')
                    if txouts is not None:
                        print(f"[UTXO] Setting UTXO count for {node_name}: {txouts}", flush=True)
                        BITCOIN_UTXO_COUNT.labels(node=node_name).set(float(txouts) if isinstance(txouts, Decimal) else txouts)
                    else:
                        print(f"[UTXO] No txouts found in UTXO info for {node_name}", flush=True)
                    
                    disk_size = utxo_info.get('disk_size')
                    if disk_size is not None:
                        print(f"[UTXO] Setting UTXO size for {node_name}: {disk_size}", flush=True)
                        BITCOIN_UTXO_SIZE.labels(node=node_name).set(float(disk_size) if isinstance(disk_size, Decimal) else disk_size)
                    else:
                        print(f"[UTXO] No disk_size found in UTXO info for {node_name}", flush=True)
                    
                    print(f"[UTXO] Collection completed in {collection_time:.2f} seconds", flush=True)
                    print(f"[UTXO] Final values - count: {txouts}, size: {disk_size}", flush=True)
                else:
                    print(f"[UTXO] Unexpected UTXO info type: {type(utxo_info)}", flush=True)
            except Exception as e:
                print(f"[UTXO] Error getting UTXO stats: {str(e)}", flush=True)
                
        except Exception as e:
            print(f"[UTXO] Error checking index info: {str(e)}", flush=True)
            
    except Exception as e:
        print(f"[UTXO] Error in UTXO stats collection: {str(e)}", flush=True)

def get_safe_fee_estimate(node_name, blocks, priority_level='low'):
    """
    Get a safe fee estimate using multiple estimation methods.
    
    Args:
        rpc: RPC connection object
        blocks: Number of blocks target for confirmation
        priority_level: 'high', 'medium', or 'low'
    
    Returns:
        int: Estimated fee rate in sat/vB, or None if estimation fails
    """
    estimates = []
    
    try:
        # Get conservative estimate (tends to be higher/safer)
        conservative = safe_rpc_call(node_name, 'estimatesmartfee', blocks, "CONSERVATIVE")
        if 'feerate' in conservative:
            estimates.append(Decimal(str(conservative['feerate'])))
            print(f"[Fees] {priority_level} conservative ({blocks} blocks): {conservative['feerate']:.8f} BTC/kB", flush=True)
        
        # Get economical estimate
        economical = safe_rpc_call(node_name, 'estimatesmartfee', blocks)
        if 'feerate' in economical:
            estimates.append(Decimal(str(economical['feerate'])))
            print(f"[Fees] {priority_level} economical ({blocks} blocks): {economical['feerate']:.8f} BTC/kB", flush=True)
        
        # For high priority, check adjacent block targets
        if priority_level == 'high':
            # Check 1-2 blocks for high priority
            for adj_blocks in [1, 2]:
                adj_estimate = safe_rpc_call(node_name, 'estimatesmartfee', adj_blocks, "CONSERVATIVE")
                if 'feerate' in adj_estimate:
                    estimates.append(Decimal(str(adj_estimate['feerate'])))
                    print(f"[Fees] {priority_level} adjacent ({adj_blocks} blocks): {adj_estimate['feerate']:.8f} BTC/kB", flush=True)
        
        if not estimates:
            print(f"[Fees] Warning: No valid fee estimates for {priority_level} priority", flush=True)
            return None
        
        # Convert to sat/vB with safety margins
        max_fee = max(estimates)
        margin = Decimal(str({
            'high': '1.2',    # 20% margin for high priority
            'medium': '1.1',  # 10% margin for medium priority
            'low': '1.0'      # No margin for low priority
        }.get(priority_level, '1.0')))
        
        # Convert BTC/kB to sat/vB: multiply by 100000
        # 1 BTC = 100000000 sats, 1 kB = 1000 bytes
        # So BTC/kB * 100000 = sat/vB
        final_fee = int(math.ceil(float(max_fee * Decimal('100000') * margin)))  # Convert to sat/vB and apply margin
        print(f"[Fees] {priority_level} final estimate: {final_fee} sat/vB (margin: {margin}x)", flush=True)
        return final_fee
        
    except Exception as e:
        print(f"[Fees] Error estimating {priority_level} priority fee: {str(e)}", flush=True)
        return None

async def collect_regular_metrics(node_name):
    """Collect all metrics except UTXO stats for a specific node"""
    try:
        # Get blockchain info
        try:
            blockchain_info = safe_rpc_call(node_name, 'getblockchaininfo')
            current_height = blockchain_info['blocks']
            BITCOIN_BLOCK_HEIGHT.labels(node=node_name).set(current_height)
            BITCOIN_VERIFICATION_PROGRESS.labels(node=node_name).set(blockchain_info['verificationprogress'])
            BITCOIN_DIFFICULTY.labels(node=node_name).set(blockchain_info['difficulty'])
            BITCOIN_SIZE_ON_DISK.labels(node=node_name).set(blockchain_info['size_on_disk'])
            
            # Simplified block time calculation with consistent UTC/UNIX timestamps
            try:
                # Get latest block info - force fresh data by clearing cache first
                if node_name in RPC_CONNECTIONS:
                    RPC_CONNECTIONS[node_name] = None  # Clear cache to get fresh data
                
                latest_block_hash = safe_rpc_call(node_name, 'getbestblockhash')
                latest_block = safe_rpc_call(node_name, 'getblock', latest_block_hash)
                
                # Verify we got the actual latest block
                current_height = blockchain_info['blocks']
                if latest_block['height'] != current_height:
                    print(f"[Metrics] WARNING: Block height mismatch! Latest block: {latest_block['height']}, Current height: {current_height}", flush=True)
                    # Try to get the block by height instead
                    latest_block = safe_rpc_call(node_name, 'getblock', current_height)
                    latest_block_hash = latest_block['hash']
                
                # Get last 10 blocks for timestamp history
                block_data = []  # Store both height and timestamp
                current_hash = latest_block_hash
                for _ in range(10):  # Collect up to 10 blocks for historical analysis
                    if current_hash:
                        block = safe_rpc_call(node_name, 'getblock', current_hash)
                        # Store both height and timestamp
                        block_data.append({
                            'height': block['height'],
                            'timestamp': block['time'],
                            'hash': current_hash
                        })
                        current_hash = block.get('previousblockhash')
                
                # Get current time in UNIX timestamp (UTC)
                current_time = int(time.time())
                
                # Calculate metrics using pure UNIX timestamps
                if len(block_data) >= 2:
                    # Time since last block (in seconds)
                    time_since_last = current_time - block_data[0]['timestamp']  # block_data[0] is the latest block
                    BITCOIN_TIME_SINCE_LAST_BLOCK.labels(node=node_name).set(time_since_last)
                    
                    # Store block timestamps with height labels
                    for block_info in block_data:
                        height = str(block_info['height'])
                        timestamp = block_info['timestamp']
                        BITCOIN_BLOCK_TIMESTAMP.labels(node=node_name, block_height=height).set(timestamp)
                        
                        block_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(timestamp))
                        print(f"[Metrics] Block {height}: {timestamp} ({block_time_str} UTC)", flush=True)
                    
                    # Log the latest block info for debugging
                    latest_height = block_data[0]['height']
                    latest_timestamp = block_data[0]['timestamp']
                    latest_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(latest_timestamp))
                    current_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(current_time))
                    print(f"[Metrics] Latest block for {node_name}: {latest_height} at {latest_time_str} UTC (current: {current_time_str} UTC, {time_since_last:.1f}s ago)", flush=True)
                    
                    # Calculate and log intervals for debugging (but don't store as metrics)
                    log_msg = f"[Metrics] Block times - Current UTC: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(current_time))}, "
                    log_msg += f"Last Block UTC: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(block_data[0]['timestamp']))}, "
                    log_msg += f"Time Since: {time_since_last:.1f}s\n"
                    log_msg += "Block intervals (for reference):\n"
                    for i in range(len(block_data)-1):
                        interval = block_data[i]['timestamp'] - block_data[i+1]['timestamp']
                        height1 = block_data[i]['height']
                        height2 = block_data[i+1]['height']
                        log_msg += f"Block {height1} to {height2}: {interval:.1f}s ({interval/60:.1f}min)\n"
                    print(log_msg, flush=True)
                else:
                    print(f"[Metrics] Warning: Not enough blocks to collect timestamps", flush=True)
            except Exception as e:
                print(f"Error calculating block time: {str(e)}", flush=True)
                
            print("[Metrics] Successfully collected blockchain metrics", flush=True)
        except Exception as e:
            print(f"[Metrics] Error collecting blockchain metrics: {str(e)}", flush=True)
        
        # Get mempool info and fee estimates
        try:
            mempool_info = safe_rpc_call(node_name, 'getmempoolinfo')
            BITCOIN_MEMPOOL_SIZE.labels(node=node_name).set(mempool_info['size'])
            BITCOIN_MEMPOOL_BYTES.labels(node=node_name).set(mempool_info['bytes'])
            BITCOIN_MEMPOOL_USAGE.labels(node=node_name).set(mempool_info['usage'])
            
            # Get fee estimates for different priorities
            try:
                # High priority (next 1-2 blocks)
                fee_high = get_safe_fee_estimate(node_name, 1, 'high')
                if fee_high is not None:
                    BITCOIN_FEE_HIGH.labels(node=node_name).set(fee_high)
                
                # Medium priority (next 3 blocks)
                fee_medium = get_safe_fee_estimate(node_name, 3, 'medium')
                if fee_medium is not None:
                    BITCOIN_FEE_MEDIUM.labels(node=node_name).set(fee_medium)
                
                # Low priority (next 6 blocks)
                fee_low = get_safe_fee_estimate(node_name, 6, 'low')
                if fee_low is not None:
                    BITCOIN_FEE_LOW.labels(node=node_name).set(fee_low)
                
                print(f"[Metrics] Final fee estimates - High: {fee_high} sat/vB, Medium: {fee_medium} sat/vB, Low: {fee_low} sat/vB", flush=True)
            
            except Exception as e:
                print(f"[Metrics] Error estimating fees: {str(e)}", flush=True)

            print("[Metrics] Successfully collected mempool metrics", flush=True)
        except Exception as e:
            print(f"[Metrics] Error collecting mempool metrics: {str(e)}", flush=True)
        
        # Get network info
        try:
            network_info = safe_rpc_call(node_name, 'getnetworkinfo')
            BITCOIN_PEER_COUNT.labels(node=node_name).set(network_info['connections'])
            
            net_totals = safe_rpc_call(node_name, 'getnettotals')
            BITCOIN_NET_BYTES_SENT.labels(node=node_name).set(net_totals.get('totalbytessent', 0))
            BITCOIN_NET_BYTES_RECV.labels(node=node_name).set(net_totals.get('totalbytesrecv', 0))
            
            peers_info = safe_rpc_call(node_name, 'getpeerinfo')
            inbound = 0
            outbound = 0
            
            for peer in peers_info:
                connection_type = peer.get('connection_type', '')
                if connection_type in ['outbound-full-relay', 'block-relay-only']:
                    outbound += 1
                elif connection_type == 'inbound':
                    inbound += 1
            
            BITCOIN_CONN_INBOUND.labels(node=node_name).set(inbound)
            BITCOIN_CONN_OUTBOUND.labels(node=node_name).set(outbound)
            print(f"[Metrics] Network stats: {inbound} inbound, {outbound} outbound connections", flush=True)
        except Exception as e:
            print(f"[Metrics] Error collecting network metrics: {str(e)}", flush=True)
            
        # Get block stats
        try:
            height = blockchain_info['blocks']
            block_stats = []
            for i in range(max(0, height - 100), height):
                stats = safe_rpc_call(node_name, 'getblockstats', i)
                block_stats.append(stats)
            
            if block_stats:
                avg_size = sum(stat['total_size'] for stat in block_stats) / len(block_stats)
                avg_txs = sum(stat['txs'] for stat in block_stats) / len(block_stats)
                BITCOIN_BLOCK_SIZE_MEAN.labels(node=node_name).set(avg_size)
                BITCOIN_BLOCK_TXS_MEAN.labels(node=node_name).set(avg_txs)
                
            print("[Metrics] Successfully collected block stats", flush=True)
        except Exception as e:
            print(f"[Metrics] Error collecting block stats: {str(e)}", flush=True)
        
        # Get memory info
        try:
            memory_info = safe_rpc_call(node_name, 'getmemoryinfo')
            if isinstance(memory_info, dict) and 'locked' in memory_info:
                locked_info = memory_info['locked']
                if isinstance(locked_info, dict) and 'used' in locked_info:
                    BITCOIN_MEMORY_USAGE.labels(node=node_name).set(locked_info['used'])
                    print(f"[Metrics] Successfully collected memory metrics for {node_name}", flush=True)
        except Exception as e:
            print(f"[Metrics] Error collecting memory metrics for {node_name}: {str(e)}", flush=True)
            
        # Get node info including version
        try:
            # Get network info including version details
            network_info = safe_rpc_call(node_name, 'getnetworkinfo')
            # Clean up version string - remove parentheses and quotes
            version_string = network_info['subversion'].replace('/', '').replace(':', '').strip("()'")
            
            # Parse numeric version
            version = network_info['version']
            major_version = version // 10000
            minor_version = (version // 100) % 100
            patch_version = version % 100
            
            # Set numeric version components
            BITCOIN_VERSION_MAJOR.labels(node=node_name).set(major_version)
            BITCOIN_VERSION_MINOR.labels(node=node_name).set(minor_version)
            BITCOIN_VERSION_PATCH.labels(node=node_name).set(patch_version)
            
            # Set the current version with cleaned string
            BITCOIN_VERSION.labels(node=node_name, version=version_string).set(1)
            
            # Set text version for easy display
            version_text = f"v{major_version}.{minor_version}.{patch_version} ({version_string})"
            BITCOIN_VERSION_TEXT.labels(node=node_name, text=version_text).set(1)
            
            # Version as decimal number
            version_num = float(f"{major_version}.{minor_version}{patch_version/100:.2f}".replace('.0', ''))
            
            # Set version number metric
            if not hasattr(sys.modules[__name__], 'BITCOIN_VERSION_NUMBER'):
                setattr(sys.modules[__name__], 'BITCOIN_VERSION_NUMBER', 
                      Gauge('bitcoin_version_number', 'Bitcoin version as a decimal number', ['node']))
            getattr(sys.modules[__name__], 'BITCOIN_VERSION_NUMBER').labels(node=node_name).set(version_num)
            
            # Set full version string metric
            if not hasattr(sys.modules[__name__], 'BITCOIN_FULL_VERSION_STRING'):
                setattr(sys.modules[__name__], 'BITCOIN_FULL_VERSION_STRING', 
                      Gauge('bitcoin_full_version_string', f'Running Bitcoin {version_string}', ['node']))
            getattr(sys.modules[__name__], 'BITCOIN_FULL_VERSION_STRING').labels(node=node_name).set(1)
            
            # Add Knots-specific subversion information
            if 'knots' in version_string.lower():
                # Extract Knots-specific information
                knots_info = version_string
                if 'Knots' in knots_info:
                    # Handle format like "Satoshi28.1.0Knots20250305"
                    knots_version = knots_info.split('Knots')[1] if 'Knots' in knots_info else 'unknown'
                else:
                    knots_version = 'unknown'
                
                # Set Knots-specific metrics
                if not hasattr(sys.modules[__name__], 'BITCOIN_KNOTS_VERSION'):
                    setattr(sys.modules[__name__], 'BITCOIN_KNOTS_VERSION', 
                          Gauge('bitcoin_knots_version', 'Bitcoin Knots specific version information', ['node', 'knots_version']))
                getattr(sys.modules[__name__], 'BITCOIN_KNOTS_VERSION').labels(node=node_name, knots_version=knots_version).set(1)
                
                print(f"[Metrics] Bitcoin Knots version for {node_name}: {knots_version}", flush=True)
            
            print(f"[Metrics] Bitcoin version for {node_name}: {version_string} (v{major_version}.{minor_version}.{patch_version}) = {version_num}", flush=True)
        except Exception as e:
            print(f"[Metrics] Error collecting version info for {node_name}: {str(e)}", flush=True)
            
    except Exception as e:
        print(f"[Metrics] Error in collect_metrics for {node_name}: {str(e)}", flush=True)

async def collect_metrics_loop():
    """Main metrics collection loop for all nodes"""
    while True:
        try:
            # Collect regular metrics for all configured nodes
            for node_name, node_config in NODES.items():
                try:
                    print(f"[Metrics] Collecting metrics for {node_name}...", flush=True)
                    await collect_regular_metrics(node_name)
                    
                    # Collect pruning metrics for each node
                    await collect_pruning_metrics(node_name, node_config, {'node': node_name})
                    
                except Exception as e:
                    print(f"[Metrics] Error collecting metrics for {node_name}: {str(e)}", flush=True)
                    continue
            
            # Collect Bitcoin price (only once)
            await get_bitcoin_price()
            
            # Wait 15 seconds before next collection
            await asyncio.sleep(15)
        except Exception as e:
            print(f"[Metrics] Error in collection loop: {str(e)}", flush=True)
            await asyncio.sleep(5)  # Wait 5 seconds on error

async def collect_utxo_loop():
    """UTXO collection loop for nodes that support it"""
    # Add initial delay to let regular metrics start first
    print("[UTXO] Waiting 15 seconds before starting initial UTXO collection...", flush=True)
    await asyncio.sleep(15)
    
    while True:
        try:
            # Collect UTXO stats for nodes that support it
            for node_name, node_config in NODES.items():
                if node_config.get('supports_utxo_stats', False):
                    try:
                        print(f"[UTXO] Collecting UTXO stats for {node_name}...", flush=True)
                        await collect_utxo_stats(node_name)
                    except Exception as e:
                        print(f"[UTXO] Error collecting UTXO stats for {node_name}: {str(e)}", flush=True)
                        continue
                else:
                    print(f"[UTXO] Skipping UTXO collection for {node_name} (not supported)", flush=True)
            
            print("[UTXO] Waiting 5 minutes before next collection...", flush=True)
            # Wait 5 minutes before next collection
            await asyncio.sleep(300)
        except Exception as e:
            print(f"[UTXO] Error in collection loop: {str(e)}", flush=True)
            await asyncio.sleep(60)  # Wait 1 minute on error

def run_metrics_server():
    """Run the metrics server with single HTTP server and path-based filtering"""
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, REGISTRY
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import threading
    
    class MetricsHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/metrics':
                # All metrics endpoint (for debugging)
                self.send_response(200)
                self.send_header('Content-Type', CONTENT_TYPE_LATEST)
                self.end_headers()
                metrics_data = generate_latest()
                self.wfile.write(metrics_data)
                print(f"[HTTP] All metrics endpoint: exported all metrics")
                
            elif self.path == '/metrics/core':
                # Core node metrics only
                self.send_response(200)
                self.send_header('Content-Type', CONTENT_TYPE_LATEST)
                self.end_headers()
                
                # Get all metrics and filter for Core node
                all_metrics = generate_latest().decode('utf-8')
                core_metrics = []
                
                for line in all_metrics.split('\n'):
                    if line.strip() == '':
                        continue
                    # Include HELP and TYPE lines
                    if line.startswith('#') or line.startswith('bitcoin_price_usd'):
                        core_metrics.append(line)
                    # Filter Bitcoin metrics for Core node only
                    elif line.startswith('bitcoin_') and 'node="core"' in line:
                        core_metrics.append(line)
                
                # Write filtered metrics
                print(f"[HTTP] Core endpoint: {len(core_metrics)} lines exported")
                self.wfile.write('\n'.join(core_metrics).encode())
                
            elif self.path == '/metrics/knots':
                # Knots node metrics only
                self.send_response(200)
                self.send_header('Content-Type', CONTENT_TYPE_LATEST)
                self.end_headers()
                
                # Get all metrics and filter for Knots node
                all_metrics = generate_latest().decode('utf-8')
                knots_metrics = []
                
                for line in all_metrics.split('\n'):
                    if line.strip() == '':
                        continue
                    # Include HELP and TYPE lines
                    if line.startswith('#') or line.startswith('bitcoin_price_usd'):
                        knots_metrics.append(line)
                    # Filter Bitcoin metrics for Knots node only
                    elif line.startswith('bitcoin_') and 'node="knots"' in line:
                        knots_metrics.append(line)
                
                # Write filtered metrics
                print(f"[HTTP] Knots endpoint: {len(knots_metrics)} lines exported")
                self.wfile.write('\n'.join(knots_metrics).encode())
                
            else:
                self.send_response(404)
                self.end_headers()
        
        def log_message(self, format, *args):
            # Suppress access logs for cleaner output
            pass
    
    # Start single HTTP server
    def start_metrics_server():
        server = HTTPServer(('0.0.0.0', 9332), MetricsHandler)
        print(f"[HTTP] Metrics server started on port 9332")
        print(f"[HTTP] Available endpoints:")
        print(f"[HTTP]   - /metrics      -> All metrics (debugging)")
        print(f"[HTTP]   - /metrics/core -> Core node metrics only")
        print(f"[HTTP]   - /metrics/knots -> Knots node metrics only")
        server.serve_forever()
    
    # Start server in separate thread
    metrics_thread = threading.Thread(target=start_metrics_server, daemon=True)
    metrics_thread.start()
    
    print(f"Starting Bitcoin dual-node metrics collector")
    print(f"[HTTP] Single server: http://0.0.0.0:9332")
    
    # Display node configuration
    print(f"[Startup] Configured nodes:")
    for node_name, node_config in NODES.items():
        auth_type = node_config['auth_type']
        rpc_endpoint = f"{node_config['rpc_host']}:{node_config['rpc_port']}"
        utxo_support = "Yes" if node_config.get('supports_utxo_stats', False) else "No"
        block_stats_support = "Yes" if node_config.get('supports_block_stats', False) else "No"
        
        print(f"[Startup]   {node_name}: {rpc_endpoint} ({auth_type}) - UTXO: {utxo_support}, Block Stats: {block_stats_support}")
    
    print("[Startup] Regular metrics will start immediately, updating every 15 seconds")
    print("[Startup] UTXO metrics will start in 15 seconds, updating every 5 minutes")
    print("[Startup] Pruning metrics will be collected for all nodes")
    
    async def run_forever():
        # Create both collection tasks
        metrics_task = asyncio.create_task(collect_metrics_loop())
        utxo_task = asyncio.create_task(collect_utxo_loop())
        # Run both concurrently
        await asyncio.gather(metrics_task, utxo_task)

    asyncio.run(run_forever())

async def get_bitcoin_price():
    """Get Bitcoin price from Binance API"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.binance.us/api/v3/ticker/price?symbol=BTCUSD') as response:
                if response.status == 200:
                    data = await response.json()
                    price = float(data['price'])
                    BITCOIN_PRICE_USD.set(price)
                    print(f"Successfully collected Bitcoin price: ${price:,.2f}")
                else:
                    print(f"Error getting Bitcoin price: HTTP {response.status}")
    except Exception as e:
        print(f"Error collecting Bitcoin price: {str(e)}")

if __name__ == '__main__':
    run_metrics_server() 