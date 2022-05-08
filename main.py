from flask import Flask
from web3 import Web3
import requests
import asyncio

app = Flask(__name__)
app.config.from_pyfile('config.conf')

w3 = Web3(Web3.WebsocketProvider(app.config['AURORA_WSS']))
brl_master_chef_contract = w3.eth.contract(address=app.config['BRL_MASTER_CHEF_ADDRESS'], abi=app.config['BRL_MASTER_CHEF_ABI'])
loop = asyncio.get_event_loop()

@app.get('/')
def get_yearly_apr():
    key = 'NEAR-WETH'

    return read_pool_apr(key)

@app.get('/api/pools/<pool_key>')
def read_pool_apr(pool_key):
    pool = find_pool(pool_key)
    apr = find_annual_apr(pool)

    return {
        'token': pool_key,
        'APR': apr
    }

def find_pool(pool_key):
    pool_length = brl_master_chef_contract.functions.poolLength().call()

    for index in range(pool_length - 1):
        pool_info = get_pool_info(index)
        if is_key_for_pool(pool_key, pool_info):
            return pool_info
        
    return None

def find_annual_apr(pool):
    # Shortcut to work only with UNI LP token
    if 'token0' not in pool:
        return 0
    
    reward_token_address = brl_master_chef_contract.functions.BRL().call()
    reward_token_decimals = get_erc20_token(reward_token_address)['contract'].functions.decimals().call()
    rewared_token_price = fetch_token_prices([reward_token_address])[reward_token_address.lower()]
    total_alloc_points = brl_master_chef_contract.functions.totalAllocPoint().call()
    brl_per_block = brl_master_chef_contract.functions.BRLPerBlock().call()
    current_block = w3.eth.block_number
    multiplier = brl_master_chef_contract.functions.getMultiplier(current_block, current_block + 1).call()
    seconds_per_week = 7 * 24 * 60 * 60 # 604800
    rewards_per_week = brl_per_block / 10 ** reward_token_decimals * multiplier * seconds_per_week / 1.1
    pool_rewards_per_week = pool['allocPoints'] / total_alloc_points * rewards_per_week
    usd_per_week = pool_rewards_per_week * rewared_token_price

    token_prices = fetch_token_prices([pool['token0'], pool['token1']])
    t0 = pool['token0_data']
    p0 = token_prices[pool['token0'].lower()]
    t1 = pool['token1_data']
    p1 = token_prices[pool['token1'].lower()]

    q0 = pool['q0'] / 10 ** t0['decimals']
    q1 = pool['q1'] / 10 ** t1['decimals']
    tvl = q0 * p0 + q1 * p1


    lp_price = tvl / pool['totalSupply'] * 10 ** 18
    staked_tvl = pool['staked'] * lp_price

    if staked_tvl == 0:
        return 0

    weekly_apr = usd_per_week / staked_tvl * 100
    yearly_apr = weekly_apr * 52

    return yearly_apr

def get_pool_info(index):
    pool_info = brl_master_chef_contract.functions.poolInfo(index).call()
    pool_info = enrich_pool_info(pool_info)
    
    return pool_info

def is_key_for_pool(pool_key, pool_info):
    return pool_key == pool_info['key']
    
def get_pool_prices():
    return {}

def enrich_pool_info(pool_info):
    pool_info = {
            'address': pool_info[0],
            'allocPoints': pool_info[1],
            'lastRewardBlock': pool_info[2],
            'accBRLPerShare': pool_info[3],
            'depositFeeBP': pool_info[4]
    }
    address = pool_info['address']

    uni_pool_contract = w3.eth.contract(address=address, abi=app.config['ERC20_ABI'])

    try:
        uni_pool_contract = w3.eth.contract(address=address, abi=app.config['UNI_ABI'])

        pool_info['name'] = uni_pool_contract.functions.name().call()
        pool_info['token0'] = uni_pool_contract.functions.token0().call()
        pool_info['token1'] = uni_pool_contract.functions.token1().call()
        pool_info['symbol'] = uni_pool_contract.functions.symbol().call()
        pool_info['staked'] = uni_pool_contract.functions.balanceOf(app.config['BRL_MASTER_CHEF_ADDRESS']).call() / 10 ** 18

        pool_info['totalSupply'] = uni_pool_contract.functions.totalSupply().call()
        pool_info['contract'] = uni_pool_contract
        pool_info['tokens'] = [pool_info['token0'], pool_info['token1']]
        
        reserves = uni_pool_contract.functions.getReserves().call()
        pool_info['q0'] = reserves[0]
        pool_info['q1'] = reserves[1]

        token0 = get_erc20_token(pool_info['token0'])
        token1 = get_erc20_token(pool_info['token1'])

        pool_info['token0_data'] = token0
        pool_info['token1_data'] = token1

        pool_info['key'] = '-'.join((token0['symbol'], token1['symbol']))

        return pool_info
    except:
        None

    try:
        erc20_pool_contract = w3.eth.contract(address=address, abi=app.config['ERC20_ABI'])
        
        pool_info['name'] = erc20_pool_contract.functions.name().call()
        pool_info['symbol'] = pool_info['key'] = erc20_pool_contract.functions.symbol().call()
        pool_info['totalSupply'] = uni_pool_contract.functions.totalSupply().call()
        pool_info['decimals'] = uni_pool_contract.functions.decimals().call()
        pool_info['contract'] = erc20_pool_contract
        pool_info['tokens'] = [address]

        return pool_info
    except:
        None
    
    return pool_info

def get_erc20_token(address):
    token_contract = w3.eth.contract(address=address, abi=app.config['ERC20_ABI'])

    return {
        'address': address,
        'name': token_contract.functions.name().call(),
        'symbol': token_contract.functions.symbol().call(),
        'totalSupply': token_contract.functions.totalSupply().call(),
        'decimals': token_contract.functions.decimals().call(),
        'staked': 0,
        'unstaked': 0,
        'contract': token_contract,
        'tokens': [address]
    }

def fetch_token_prices(contract_addresses):
    result = {}
    contract_addresses = '%2C'.join(contract_addresses)
    url = ''.join(['https://api.coingecko.com/api/v3/simple/token_price/aurora?contract_addresses=', contract_addresses, '&vs_currencies=usd'])
    response = requests.get(url)
    for k,v in response.json().items():
        result[k] = v['usd']

    return result
