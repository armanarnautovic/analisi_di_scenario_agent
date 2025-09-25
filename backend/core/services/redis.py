import redis.asyncio as redis
import os
from dotenv import load_dotenv
import asyncio
from core.utils.logger import logger
from typing import List, Any, AsyncGenerator, Dict
from core.utils.retry import retry

# Redis client and connection pool
client: redis.Redis | None = None
pool: redis.ConnectionPool | None = None
_initialized = False
_init_lock = asyncio.Lock()

# Constants
REDIS_KEY_TTL = 3600 * 24  # 24 hour TTL as safety mechanism

def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "y", "on")

def initialize():
    """Initialize Redis connection pool and client using environment variables."""
    global client, pool

    load_dotenv()

    redis_host = os.getenv("REDIS_HOST", "redis")
    redis_port = int(os.getenv("REDIS_PORT", 6379))
    redis_password = os.getenv("REDIS_PASSWORD", "")
    # NB: SSL va usato solo se Redis è esposto con TLS (non nel tuo compose locale)
    redis_ssl = _env_bool("REDIS_SSL", False)

    # Pool dimensionato in modo prudente: abbastanza alto per backend+worker ma non enorme
    max_connections = int(os.getenv("REDIS_MAX_CONNECTIONS", "320"))
    socket_timeout = float(os.getenv("REDIS_SOCKET_TIMEOUT", "30.0"))
    connect_timeout = float(os.getenv("REDIS_CONNECT_TIMEOUT", "15.0"))
    retry_on_timeout = _env_bool("REDIS_RETRY_ON_TIMEOUT", True)

    logger.info(
        f"Initializing Redis pool {redis_host}:{redis_port} "
        f"(max={max_connections}, ssl={redis_ssl}, retry_on_timeout={retry_on_timeout})"
    )

    # Connection pool
    kwargs = dict(
        host=redis_host,
        port=redis_port,
        password=redis_password or None,
        decode_responses=True,
        socket_timeout=socket_timeout,
        socket_connect_timeout=connect_timeout,
        socket_keepalive=True,
        retry_on_timeout=retry_on_timeout,
        health_check_interval=30,
        max_connections=max_connections,
    )

    # aggiungi SSL solo se esplicitamente richiesto e supportato
    if redis_ssl:
        kwargs["ssl"] = True

    pool = redis.ConnectionPool(**kwargs)

    # Client dal pool
    client = redis.Redis(connection_pool=pool)
    return client

async def initialize_async():
    """Initialize Redis connection asynchronously."""
    global client, _initialized

    async with _init_lock:
        if not _initialized:
            initialize()

        try:
            await asyncio.wait_for(client.ping(), timeout=5.0)
            logger.info("Successfully connected to Redis")
            _initialized = True
        except asyncio.TimeoutError:
            logger.error("Redis connection timeout during initialization")
            client = None
            _initialized = False
            raise ConnectionError("Redis connection timeout")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            client = None
            _initialized = False
            raise

    return client

async def close():
    """Close Redis connection and connection pool."""
    global client, pool, _initialized
    if client:
        try:
            await asyncio.wait_for(client.aclose(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Redis close timeout, forcing close")
        except Exception as e:
            logger.warning(f"Error closing Redis client: {e}")
        finally:
            client = None
    
    if pool:
        try:
            await asyncio.wait_for(pool.aclose(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Redis pool close timeout, forcing close")
        except Exception as e:
            logger.warning(f"Error closing Redis pool: {e}")
        finally:
            pool = None
    
    _initialized = False
    logger.info("Redis connection and pool closed")

async def get_client():
    """Get the Redis client, initializing if necessary."""
    global client, _initialized
    if client is None or not _initialized:
        await retry(lambda: initialize_async())
    return client

# ---------- Basic ops ----------
async def set(key: str, value: str, ex: int = None, nx: bool = False):
    redis_client = await get_client()
    return await redis_client.set(key, value, ex=ex, nx=nx)

async def get(key: str, default: str = None):
    redis_client = await get_client()
    result = await redis_client.get(key)
    return result if result is not None else default

async def delete(key: str):
    redis_client = await get_client()
    return await redis_client.delete(key)

async def publish(channel: str, message: str):
    redis_client = await get_client()
    return await redis_client.publish(channel, message)

# ---------- Lists ----------
async def rpush(key: str, *values: Any):
    redis_client = await get_client()
    return await redis_client.rpush(key, *values)

async def lrange(key: str, start: int, end: int) -> List[str]:
    redis_client = await get_client()
    return await redis_client.lrange(key, start, end)

# ---------- Keys ----------
async def keys(pattern: str) -> List[str]:
    redis_client = await get_client()
    return await redis_client.keys(pattern)

async def expire(key: str, seconds: int):
    redis_client = await get_client()
    return await redis_client.expire(key, seconds)

# ---------- Pub/Sub dedicato ----------
async def create_pubsub():
    """
    Crea un oggetto PubSub DEDICATO (connessione separata).
    Nota: decode_responses=False per performance; decodifichiamo noi i bytes.
    """
    redis_client = await get_client()
    pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
    return pubsub

async def pubsub_subscribe_and_listen(
    channels: List[str],
    stop_event: asyncio.Event,
    ping_interval: float = 20.0,
    initial_backoff: float = 0.5,
    max_backoff: float = 8.0,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Yield messaggi da channels con auto-reconnect e backoff esponenziale.
    Ogni ciclo crea una nuova connessione Pub/Sub: in caso di errore o
    chiusura da parte del server, si riconnette in automatico.
    """
    backoff = initial_backoff
    while not stop_event.is_set():
        pubsub = None
        try:
            pubsub = await create_pubsub()
            await pubsub.subscribe(*channels)
            last_ping = asyncio.get_event_loop().time()

            listener = pubsub.listen()
            next_msg_task = asyncio.create_task(listener.__anext__())

            while not stop_event.is_set():
                done, _ = await asyncio.wait([next_msg_task], return_when=asyncio.FIRST_COMPLETED)

                if next_msg_task in done:
                    try:
                        raw = next_msg_task.result()
                    except StopAsyncIteration:
                        # Connessione chiusa dal server: forza reconnect
                        break
                    except Exception:
                        # Errore imprevisto: forza reconnect
                        break
                    finally:
                        if not stop_event.is_set():
                            next_msg_task = asyncio.create_task(listener.__anext__())

                    if raw and isinstance(raw, dict) and raw.get("type") == "message":
                        ch = raw.get("channel")
                        data = raw.get("data")
                        if isinstance(ch, bytes):
                            ch = ch.decode("utf-8", errors="ignore")
                        if isinstance(data, bytes):
                            data = data.decode("utf-8", errors="ignore")
                        yield {"channel": ch, "data": data}

                # Ping periodico per tenere viva la connessione
                now = asyncio.get_event_loop().time()
                if now - last_ping >= ping_interval:
                    try:
                        await pubsub.ping()
                    except Exception:
                        # Ping fallito → riconnessione
                        break
                    last_ping = now

            # esce dal while → stop_event o necessità di reconnect
        except Exception as e:
            # errore → backoff e retry
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
            continue
        finally:
            # cleanup connessione
            try:
                if pubsub is not None:
                    try:
                        await pubsub.unsubscribe(*channels)
                    except Exception:
                        pass
                    try:
                        await pubsub.close()
                    except Exception:
                        pass
            except Exception:
                pass

        # se siamo qui senza stop_event → reconnette subito (backoff già gestito sopra)
        # reset del backoff dopo un ciclo “sano”
        backoff = initial_backoff