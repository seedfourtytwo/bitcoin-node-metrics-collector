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

# Configuration
BITCOIN_RPC_HOST = os.getenv('BITCOIN_RPC_HOST', '127.0.0.1')
BITCOIN_RPC_PORT = os.getenv('BITCOIN_RPC_PORT', '8332')
BITCOIN_COOKIE_PATH = os.getenv('BITCOIN_COOKIE_PATH', '~/.bitcoin/.cookie')
METRICS_PORT = int(os.getenv('METRICS_PORT', '9332'))

# Prometheus metrics
BITCOIN_BLOCK_HEIGHT = Gauge('bitcoin_block_height', 'Current block height')
BITCOIN_VERIFICATION_PROGRESS = Gauge('bitcoin_verification_progress', 'Blockchain verification progress')
BITCOIN_DIFFICULTY = Gauge('bitcoin_difficulty', 'Current mining difficulty')
BITCOIN_MEMPOOL_SIZE = Gauge('bitcoin_mempool_size', 'Number of transactions in mempool')
BITCOIN_MEMPOOL_BYTES = Gauge('bitcoin_mempool_bytes', 'Size of mempool in bytes')
BITCOIN_MEMPOOL_USAGE = Gauge('bitcoin_mempool_usage', 'Memory usage of mempool in bytes')
BITCOIN_PEER_COUNT = Gauge('bitcoin_peer_count', 'Number of connected peers')
BITCOIN_MEMORY_USAGE = Gauge('bitcoin_memory_usage_bytes', 'Memory usage in bytes')
BITCOIN_PRICE_USD = Gauge('bitcoin_price_usd', 'Current Bitcoin price in USD')
BITCOIN_TIME_SINCE_LAST_BLOCK = Gauge('bitcoin_time_since_last_block_seconds', 'Time since last block in seconds')
BITCOIN_FEE_HIGH = Gauge('bitcoin_fee_high', 'Estimated fee rate for high priority - next block (sat/vB)')
BITCOIN_FEE_MEDIUM = Gauge('bitcoin_fee_medium', 'Estimated fee rate for medium priority - 3 blocks (sat/vB)')
BITCOIN_FEE_LOW = Gauge('bitcoin_fee_low', 'Estimated fee rate for low priority - 6 blocks (sat/vB)')
BITCOIN_SIZE_ON_DISK = Gauge('bitcoin_size_on_disk_bytes', 'Total blockchain size on disk in bytes')
BITCOIN_VERSION = Gauge('bitcoin_version_info', 'Bitcoin Core version info', ['version'])
# Additional version metrics for easier display in Grafana
BITCOIN_VERSION_MAJOR = Gauge('bitcoin_version_major', 'Bitcoin Core major version')
BITCOIN_VERSION_MINOR = Gauge('bitcoin_version_minor', 'Bitcoin Core minor version')
BITCOIN_VERSION_PATCH = Gauge('bitcoin_version_patch', 'Bitcoin Core patch version')
BITCOIN_VERSION_TEXT = Gauge('bitcoin_version_text', 'Bitcoin Core version as text', ['text'])

# Network metrics
BITCOIN_NET_BYTES_SENT = Gauge('bitcoin_network_bytes_sent_total', 'Total bytes sent')
BITCOIN_NET_BYTES_RECV = Gauge('bitcoin_network_bytes_received_total', 'Total bytes received')
BITCOIN_CONN_INBOUND = Gauge('bitcoin_connections_inbound', 'Number of inbound connections')
BITCOIN_CONN_OUTBOUND = Gauge('bitcoin_connections_outbound', 'Number of outbound connections')

# Block metrics
BITCOIN_BLOCK_SIZE_MEAN = Gauge('bitcoin_block_size_bytes_mean', 'Average block size in bytes')
BITCOIN_BLOCK_TXS_MEAN = Gauge('bitcoin_block_transactions_mean', 'Average transactions per block')
BITCOIN_BLOCK_INTERVAL = Gauge('bitcoin_block_interval_seconds', 'Time between last two blocks')

# UTXO metrics
BITCOIN_UTXO_COUNT = Gauge('bitcoin_utxo_count', 'Total number of unspent transaction outputs')
BITCOIN_UTXO_SIZE = Gauge('bitcoin_utxo_size_bytes', 'Total size of UTXO set in bytes')

# Add at the top with other globals
RPC_CONNECTION = None

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

def get_rpc_connection():
    """Establish RPC connection using cookie authentication by default"""
    global RPC_CONNECTION
    
    # Return cached connection if it exists
    if RPC_CONNECTION is not None:
        return RPC_CONNECTION
        
    try:
        # Try cookie authentication first
        cookie_path = os.getenv('BITCOIN_COOKIE_PATH')
        if os.path.exists(cookie_path):
            with open(cookie_path, 'r') as f:
                cookie_content = f.read().strip()
                # Parse cookie content
                if ':' in cookie_content:
                    username, password = cookie_content.split(':', 1)
                    # Set timeout to 300 seconds (5 minutes) for UTXO operations
                    RPC_CONNECTION = AuthServiceProxy(f"http://{username}:{password}@{os.getenv('BITCOIN_RPC_HOST')}:{os.getenv('BITCOIN_RPC_PORT')}", timeout=300)
                    print("[RPC] Successfully established connection using cookie authentication", flush=True)
                    return RPC_CONNECTION
                else:
                    print("[RPC] Invalid cookie format", flush=True)
                    raise Exception("Invalid cookie format")
        else:
            print("[RPC] Cookie file not found", flush=True)
            raise Exception("Cookie file not found")
    except Exception as e:
        print(f"[RPC] Cookie authentication failed: {str(e)}", flush=True)
        # Fall back to username/password if available
        rpc_user = os.getenv('BITCOIN_RPC_USER')
        rpc_password = os.getenv('BITCOIN_RPC_PASSWORD')
        if rpc_user and rpc_password:
            print("[RPC] Using username/password authentication", flush=True)
            # Set timeout to 300 seconds (5 minutes) for UTXO operations
            RPC_CONNECTION = AuthServiceProxy(f"http://{rpc_user}:{rpc_password}@{os.getenv('BITCOIN_RPC_HOST')}:{os.getenv('BITCOIN_RPC_PORT')}", timeout=300)
            return RPC_CONNECTION
        raise Exception("No valid authentication method available")

async def collect_utxo_stats():
    """Collect UTXO statistics independently"""
    try:
        print("[UTXO] Starting UTXO stats collection...", flush=True)
        rpc = get_rpc_connection()
        
        # First check if indexes are ready
        try:
            index_info = rpc.getindexinfo()
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
                utxo_info = rpc.gettxoutsetinfo()
                collection_time = time.time() - start_time
                print(f"[UTXO] Raw UTXO info: {json.dumps(utxo_info, indent=2, cls=DecimalEncoder)}", flush=True)
                
                if isinstance(utxo_info, dict):
                    # Extract and set metrics
                    txouts = utxo_info.get('txouts')
                    if txouts is not None:
                        print(f"[UTXO] Setting UTXO count: {txouts}", flush=True)
                        BITCOIN_UTXO_COUNT.set(float(txouts) if isinstance(txouts, Decimal) else txouts)
                    else:
                        print("[UTXO] No txouts found in UTXO info", flush=True)
                    
                    disk_size = utxo_info.get('disk_size')
                    if disk_size is not None:
                        print(f"[UTXO] Setting UTXO size: {disk_size}", flush=True)
                        BITCOIN_UTXO_SIZE.set(float(disk_size) if isinstance(disk_size, Decimal) else disk_size)
                    else:
                        print("[UTXO] No disk_size found in UTXO info", flush=True)
                    
                    print(f"[UTXO] Collection completed in {collection_time:.2f} seconds", flush=True)
                    print(f"[UTXO] Final values - count: {BITCOIN_UTXO_COUNT._value.get()}, size: {BITCOIN_UTXO_SIZE._value.get()}", flush=True)
                else:
                    print(f"[UTXO] Unexpected UTXO info type: {type(utxo_info)}", flush=True)
            except Exception as e:
                print(f"[UTXO] Error getting UTXO stats: {str(e)}", flush=True)
                
        except Exception as e:
            print(f"[UTXO] Error checking index info: {str(e)}", flush=True)
            
    except Exception as e:
        print(f"[UTXO] Error in UTXO stats collection: {str(e)}", flush=True)

def get_safe_fee_estimate(rpc, blocks, priority_level='low'):
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
        conservative = rpc.estimatesmartfee(blocks, "CONSERVATIVE")
        if 'feerate' in conservative:
            estimates.append(Decimal(str(conservative['feerate'])))
            print(f"[Fees] {priority_level} conservative ({blocks} blocks): {conservative['feerate']:.8f} BTC/kB", flush=True)
        
        # Get economical estimate
        economical = rpc.estimatesmartfee(blocks)
        if 'feerate' in economical:
            estimates.append(Decimal(str(economical['feerate'])))
            print(f"[Fees] {priority_level} economical ({blocks} blocks): {economical['feerate']:.8f} BTC/kB", flush=True)
        
        # For high priority, check adjacent block targets
        if priority_level == 'high':
            # Check 1-2 blocks for high priority
            for adj_blocks in [1, 2]:
                adj_estimate = rpc.estimatesmartfee(adj_blocks, "CONSERVATIVE")
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

async def collect_regular_metrics():
    """Collect all metrics except UTXO stats"""
    try:
        rpc = get_rpc_connection()
        
        # Get blockchain info
        try:
            blockchain_info = rpc.getblockchaininfo()
            current_height = blockchain_info['blocks']
            BITCOIN_BLOCK_HEIGHT.set(current_height)
            BITCOIN_VERIFICATION_PROGRESS.set(blockchain_info['verificationprogress'])
            BITCOIN_DIFFICULTY.set(blockchain_info['difficulty'])
            BITCOIN_SIZE_ON_DISK.set(blockchain_info['size_on_disk'])
            
            # Simplified block time calculation with consistent UTC/UNIX timestamps
            try:
                # Get latest block info
                latest_block_hash = rpc.getbestblockhash()
                latest_block = rpc.getblock(latest_block_hash)
                
                # Get last 6 blocks for interval history
                block_times = []
                current_hash = latest_block_hash
                for _ in range(6):  # Last 6 blocks is enough for recent history
                    if current_hash:
                        block = rpc.getblock(current_hash)
                        # Block times from Bitcoin Core are already in UNIX timestamp format
                        block_times.append(block['time'])
                        current_hash = block.get('previousblockhash')
                
                # Get current time in UNIX timestamp (UTC)
                current_time = int(time.time())
                
                # Calculate metrics using pure UNIX timestamps
                if len(block_times) >= 2:
                    # Time since last block (in seconds)
                    time_since_last = current_time - block_times[0]  # block_times[0] is the latest block
                    BITCOIN_TIME_SINCE_LAST_BLOCK.set(time_since_last)
                    
                    # Calculate intervals for the last 5 block pairs
                    for i in range(len(block_times)-1):
                        interval = block_times[i] - block_times[i+1]
                        # Create gauge if it doesn't exist
                        gauge_name = f'BITCOIN_BLOCK_INTERVAL_{i+1}'
                        if not hasattr(sys.modules[__name__], gauge_name):
                            setattr(sys.modules[__name__], gauge_name, 
                                  Gauge(f'bitcoin_block_interval_{i+1}_seconds', 
                                       f'Time between block {i+1} and {i+2} in seconds'))
                        # Set the value
                        getattr(sys.modules[__name__], gauge_name).set(interval)
                    
                    # Enhanced logging with all intervals
                    log_msg = f"[Metrics] Block times - Current UTC: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(current_time))}, "
                    log_msg += f"Last Block UTC: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(block_times[0]))}, "
                    log_msg += f"Time Since: {time_since_last:.1f}s\n"
                    log_msg += "Block intervals:\n"
                    for i in range(len(block_times)-1):
                        interval = block_times[i] - block_times[i+1]
                        log_msg += f"Block {i+1} to {i+2}: {interval:.1f}s ({interval/60:.1f}min)\n"
                    print(log_msg, flush=True)
                else:
                    print(f"[Metrics] Warning: Not enough blocks to calculate intervals", flush=True)
            except Exception as e:
                print(f"Error calculating block time: {str(e)}", flush=True)
                
            print("[Metrics] Successfully collected blockchain metrics", flush=True)
        except Exception as e:
            print(f"[Metrics] Error collecting blockchain metrics: {str(e)}", flush=True)
        
        # Get mempool info and fee estimates
        try:
            mempool_info = rpc.getmempoolinfo()
            BITCOIN_MEMPOOL_SIZE.set(mempool_info['size'])
            BITCOIN_MEMPOOL_BYTES.set(mempool_info['bytes'])
            BITCOIN_MEMPOOL_USAGE.set(mempool_info['usage'])
            
            # Get fee estimates for different priorities
            try:
                # High priority (next 1-2 blocks)
                fee_high = get_safe_fee_estimate(rpc, 1, 'high')
                if fee_high is not None:
                    BITCOIN_FEE_HIGH.set(fee_high)
                
                # Medium priority (next 3 blocks)
                fee_medium = get_safe_fee_estimate(rpc, 3, 'medium')
                if fee_medium is not None:
                    BITCOIN_FEE_MEDIUM.set(fee_medium)
                
                # Low priority (next 6 blocks)
                fee_low = get_safe_fee_estimate(rpc, 6, 'low')
                if fee_low is not None:
                    BITCOIN_FEE_LOW.set(fee_low)
                
                print(f"[Metrics] Final fee estimates - High: {fee_high} sat/vB, Medium: {fee_medium} sat/vB, Low: {fee_low} sat/vB", flush=True)
            
            except Exception as e:
                print(f"[Metrics] Error estimating fees: {str(e)}", flush=True)

            print("[Metrics] Successfully collected mempool metrics", flush=True)
        except Exception as e:
            print(f"[Metrics] Error collecting mempool metrics: {str(e)}", flush=True)
        
        # Get network info
        try:
            network_info = rpc.getnetworkinfo()
            BITCOIN_PEER_COUNT.set(network_info['connections'])
            
            net_totals = rpc.getnettotals()
            BITCOIN_NET_BYTES_SENT.set(net_totals.get('totalbytessent', 0))
            BITCOIN_NET_BYTES_RECV.set(net_totals.get('totalbytesrecv', 0))
            
            peers_info = rpc.getpeerinfo()
            inbound = 0
            outbound = 0
            
            for peer in peers_info:
                connection_type = peer.get('connection_type', '')
                if connection_type in ['outbound-full-relay', 'block-relay-only']:
                    outbound += 1
                elif connection_type == 'inbound':
                    inbound += 1
            
            BITCOIN_CONN_INBOUND.set(inbound)
            BITCOIN_CONN_OUTBOUND.set(outbound)
            print(f"[Metrics] Network stats: {inbound} inbound, {outbound} outbound connections", flush=True)
        except Exception as e:
            print(f"[Metrics] Error collecting network metrics: {str(e)}", flush=True)
            
        # Get block stats
        try:
            height = blockchain_info['blocks']
            block_stats = []
            for i in range(max(0, height - 100), height):
                stats = rpc.getblockstats(i)
                block_stats.append(stats)
            
            if block_stats:
                avg_size = sum(stat['total_size'] for stat in block_stats) / len(block_stats)
                avg_txs = sum(stat['txs'] for stat in block_stats) / len(block_stats)
                BITCOIN_BLOCK_SIZE_MEAN.set(avg_size)
                BITCOIN_BLOCK_TXS_MEAN.set(avg_txs)
                
            print("[Metrics] Successfully collected block stats", flush=True)
        except Exception as e:
            print(f"[Metrics] Error collecting block stats: {str(e)}", flush=True)
        
        # Get memory info
        try:
            memory_info = rpc.getmemoryinfo()
            if isinstance(memory_info, dict) and 'locked' in memory_info:
                locked_info = memory_info['locked']
                if isinstance(locked_info, dict) and 'used' in locked_info:
                    BITCOIN_MEMORY_USAGE.set(locked_info['used'])
                    print("[Metrics] Successfully collected memory metrics", flush=True)
        except Exception as e:
            print(f"[Metrics] Error collecting memory metrics: {str(e)}", flush=True)
            
        # Get node info including version
        try:
            # Get network info including version details
            network_info = rpc.getnetworkinfo()
            # Clean up version string - remove parentheses and quotes
            version_string = network_info['subversion'].replace('/', '').replace(':', '').strip("()'")
            
            # Parse numeric version
            version = network_info['version']
            major_version = version // 10000
            minor_version = (version // 100) % 100
            patch_version = version % 100
            
            # Set numeric version components
            BITCOIN_VERSION_MAJOR.set(major_version)
            BITCOIN_VERSION_MINOR.set(minor_version)
            BITCOIN_VERSION_PATCH.set(patch_version)
            
            # Set the current version with cleaned string
            BITCOIN_VERSION.labels(version=version_string).set(1)
            
            # Set text version for easy display
            version_text = f"v{major_version}.{minor_version}.{patch_version} ({version_string})"
            BITCOIN_VERSION_TEXT.labels(text=version_text).set(1)
            
            # Version as decimal number
            version_num = float(f"{major_version}.{minor_version}{patch_version/100:.2f}".replace('.0', ''))
            
            # Set version number metric
            if not hasattr(sys.modules[__name__], 'BITCOIN_VERSION_NUMBER'):
                setattr(sys.modules[__name__], 'BITCOIN_VERSION_NUMBER', 
                      Gauge('bitcoin_version_number', 'Bitcoin Core version as a decimal number'))
            getattr(sys.modules[__name__], 'BITCOIN_VERSION_NUMBER').set(version_num)
            
            # Set full version string metric
            if not hasattr(sys.modules[__name__], 'BITCOIN_FULL_VERSION_STRING'):
                setattr(sys.modules[__name__], 'BITCOIN_FULL_VERSION_STRING', 
                      Gauge('bitcoin_full_version_string', f'Running Bitcoin Core {version_string}'))
            getattr(sys.modules[__name__], 'BITCOIN_FULL_VERSION_STRING').set(1)
            
            print(f"[Metrics] Bitcoin Core version: {version_string} (v{major_version}.{minor_version}.{patch_version}) = {version_num}", flush=True)
        except Exception as e:
            print(f"[Metrics] Error collecting version info: {str(e)}", flush=True)
            
    except Exception as e:
        print(f"[Metrics] Error in collect_metrics: {str(e)}", flush=True)

async def collect_metrics_loop():
    """Main metrics collection loop"""
    while True:
        try:
            # Collect regular metrics
            await collect_regular_metrics()
            # Collect Bitcoin price
            await get_bitcoin_price()
            # Wait 15 seconds before next collection
            await asyncio.sleep(15)
        except Exception as e:
            print(f"[Metrics] Error in collection loop: {str(e)}", flush=True)
            await asyncio.sleep(5)  # Wait 5 seconds on error

async def collect_utxo_loop():
    """UTXO collection loop"""
    # Add initial delay to let regular metrics start first
    print("[UTXO] Waiting 15 seconds before starting initial UTXO collection...", flush=True)
    await asyncio.sleep(15)
    
    while True:
        try:
            # Collect UTXO stats
            await collect_utxo_stats()
            print("[UTXO] Waiting 5 minutes before next collection...", flush=True)
            # Wait 5 minutes before next collection
            await asyncio.sleep(300)
        except Exception as e:
            print(f"[UTXO] Error in collection loop: {str(e)}", flush=True)
            await asyncio.sleep(60)  # Wait 1 minute on error

def run_metrics_server():
    """Run the metrics server with separate collection loops"""
    start_http_server(METRICS_PORT)
    print(f"Starting Bitcoin metrics collector on port {METRICS_PORT}")
    print("[Startup] Regular metrics will start immediately, updating every 15 seconds")
    print("[Startup] UTXO metrics will start in 15 seconds, updating every 5 minutes")
    
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