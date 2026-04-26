#!/usr/bin/env python3
import asyncio
import os
import sys
import psutil
import logging
import time
import gc
import json
import aiohttp
from collections import deque
from datetime import datetime
from contextlib import asynccontextmanager
from typing import List, Dict, Optional, Tuple, Any, Set
from fastapi import FastAPI, HTTPException, Security, Request, Depends
from fastapi.security import APIKeyHeader

# ==========================================
# 🔐 CONFIGURATION & STRICT SECURITY
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s: %(message)s')
logger = logging.getLogger("Sovereign_V46")

# ---- API Key Management (supports multiple keys + file reload) ----
def load_api_keys() -> Set[str]:
    keys = set()
    # Env var: comma-separated
    env_keys = os.getenv("VISVA_API_KEYS", "")
    if env_keys:
        for k in env_keys.split(","):
            keys.add(k.strip())
    # Single key fallback
    single_key = os.getenv("VISVA_API_KEY", "")
    if single_key and single_key != "CHANGE_ME_IN_PRODUCTION":
        keys.add(single_key)
    # Hot-reloadable key file
    key_file = os.getenv("VISVA_KEY_FILE", "visva_enterprise.key")
    if os.path.exists(key_file):
        try:
            with open(key_file, 'r') as f:
                data = json.load(f)
            if "signature" in data:
                # For enterprise license, use signature as key
                keys.add(data["signature"])
        except Exception as e:
            logger.error(f"Failed to load key file: {e}")
    if not keys:
        logger.critical("FATAL: No API keys configured. Set VISVA_API_KEYS or VISVA_API_KEY.")
        sys.exit(1)
    return keys

API_KEYS = load_api_keys()
logger.info(f"Loaded {len(API_KEYS)} API key(s)")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# ---- Rate Limiter ----
class RateLimiter:
    def __init__(self, max_req: int = 10, window: float = 60.0):
        self.db: Dict[str, List[float]] = {}
        self.max_req = max_req
        self.window = window
        self.prune_task: Optional[asyncio.Task] = None

    async def check(self, request: Request):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        # Background pruning task runs elsewhere (started in lifespan)
        if client_ip not in self.db:
            self.db[client_ip] = []
        timestamps = self.db[client_ip]
        # Remove expired (fast in-place)
        timestamps[:] = [t for t in timestamps if now - t < self.window]
        if len(timestamps) >= self.max_req:
            logger.warning(f"Rate limit hit for {client_ip}")
            raise HTTPException(status_code=429, detail="Rate limit exceeded.")
        timestamps.append(now)

    async def prune_loop(self):
        """Periodic background pruning to avoid O(n) on every request."""
        while True:
            await asyncio.sleep(30)
            now = time.time()
            for ip in list(self.db.keys()):
                self.db[ip] = [t for t in self.db[ip] if now - t < self.window]
                if not self.db[ip]:
                    del self.db[ip]

ratelimiter = RateLimiter(max_req=10, window=60.0)

# ==========================================
# 🧬 BIOLOGICAL STATE (Strictly Managed)
# ==========================================
class BiologicalState:
    def __init__(self):
        self.cpu_usage: Optional[float] = None
        self.memory_usage: Optional[float] = None
        self.battery_percent: Optional[float] = None
        self.is_plugged_in: bool = True
        self.temperature: Optional[float] = None
        self.disk_usage: Optional[float] = None
        self.is_online: bool = False
        self.emotions: Tuple[str, ...] = ("CALM",)
        
        self.is_awake: bool = False
        self.synaptic_lock = asyncio.Lock()
        self.short_term_memory = deque(maxlen=10)
        self.heartbeat_count: int = 0
        self.last_gc_time: float = 0.0 
        self.heartbeat_task: Optional[asyncio.Task] = None

    def start_heartbeat(self):
        """Cancel any old task and start a new heartbeat loop."""
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
        self.is_awake = True
        self.heartbeat_task = asyncio.create_task(autonomic_nervous_system())

state = BiologicalState()

# ==========================================
# 🧠 SENSORY ORGANS (Decoupled Threading)
# ==========================================
def read_hardware_sensors() -> Dict[str, Any]:
    """
    Returns raw readings. On failure, values are None (not 0.0) to avoid masking.
    """
    readings: Dict[str, Any] = {
        "cpu": None, "mem": None, "disk": None,
        "bat_pct": None, "plugged": True, "temp": None
    }
    
    try:
        # CPU & memory
        try:
            readings["cpu"] = psutil.cpu_percent(interval=None)
            readings["mem"] = psutil.virtual_memory().percent
        except Exception as e:
            logger.error(f"CPU/Mem read error: {e}")

        # Disk – iterate all physical drives safely
        try:
            max_disk = 0.0
            for part in psutil.disk_partitions(all=False):
                if os.name == 'nt' and ('cdrom' in part.opts or part.fstype == ''):
                    continue
                try:
                    usage = psutil.disk_usage(part.mountpoint).percent
                    if usage > max_disk:
                        max_disk = usage
                except Exception:
                    continue
            readings["disk"] = max_disk if max_disk > 0 else None
        except Exception as e:
            logger.error(f"Disk partition scan failed: {e}")

        # Battery
        try:
            battery = psutil.sensors_battery()
            if battery is not None:
                readings["bat_pct"] = battery.percent
                readings["plugged"] = battery.power_plugged
        except Exception:
            pass

        # Thermal
        if hasattr(psutil, "sensors_temperatures"):
            try:
                temps = psutil.sensors_temperatures()
                highest_temp = None
                cpu_keys = ['coretemp', 'k10temp', 'cpu_thermal', 'acpitz']
                for key in cpu_keys:
                    if key in temps:
                        for entry in temps[key]:
                            if highest_temp is None or entry.current > highest_temp:
                                highest_temp = entry.current
                readings["temp"] = highest_temp
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Sensor read failure: {e}")

    return readings

async def check_vision_network() -> bool:
    """Multi-target TCP probe with proper cleanup."""
    targets = [('1.1.1.1', 53), ('8.8.8.8', 53)]
    for ip, port in targets:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=2.0
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (asyncio.TimeoutError, OSError):
            continue
    return False

# ==========================================
# 🛡️ THE IMMUNE SYSTEM & AMYGDALA
# ==========================================
def calculate_emotions(readings: Dict[str, Any], is_online: bool) -> Tuple[str, ...]:
    emotions = []

    cpu = readings.get("cpu")
    temp = readings.get("temp")
    bat = readings.get("bat_pct")
    plugged = readings.get("plugged", True)
    mem = readings.get("mem")
    disk = readings.get("disk")

    # Actual checks (only if values are valid)
    if cpu is not None and cpu > 90.0:
        emotions.append("PANIC")
    if temp is not None and temp > 85.0:
        emotions.append("PANIC")
    if bat is not None and bat < 20.0 and not plugged:
        emotions.append("STARVING")
    if mem is not None and mem > 85.0:
        emotions.append("OVERWHELMED")
    if disk is not None and disk > 95.0:
        emotions.append("CLAUSTROPHOBIC")
    if not is_online:
        emotions.append("BLIND")

    # Sensor numbness (all main sensors failed)
    if cpu is None and mem is None and temp is None and bat is None:
        emotions.append("NUMB")

    hour = datetime.now().hour
    if hour >= 23 or hour <= 4:
        emotions.append("SLEEPY")

    if not emotions:
        emotions.append("CALM")
    return tuple(emotions)

def get_heartbeat_delay(emotions: Tuple[str, ...]) -> float:
    """Return max delay based on prevailing emotions."""
    delay_map = {"STARVING": 3.0, "SLEEPY": 1.5}
    max_delay = 1.0
    for e in emotions:
        max_delay = max(max_delay, delay_map.get(e, 1.0))
    return max_delay

# ==========================================
# 🚨 ALERTING (Webhook)
# ==========================================
ALERT_WEBHOOK = os.getenv("ALERT_WEBHOOK_URL")

async def send_alert(emotions: Tuple[str, ...]):
    if not ALERT_WEBHOOK:
        return
    critical = [e for e in emotions if e in ("PANIC", "STARVING")]
    if not critical:
        return
    payload = {
        "text": f"⚠️ Visva Alert: {', '.join(critical)}",
        "emotions": emotions,
        "cpu": state.cpu_usage,
        "mem": state.memory_usage,
        "temp": state.temperature,
        "battery": state.battery_percent
    }
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(ALERT_WEBHOOK, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"Alert webhook failed: {e}")

# ==========================================
# 🫀 THE HEARTBEAT EVENT LOOP
# ==========================================
async def autonomic_nervous_system():
    # Prime CPU sensor
    psutil.cpu_percent(interval=None)
    await asyncio.sleep(0.1)
    
    network_task = None
    
    while state.is_awake:
        try:
            # 1. Gather raw data (outside lock)
            raw_data = await asyncio.to_thread(read_hardware_sensors)

            # 2. Network check (skip first heart, then every 10)
            if state.heartbeat_count > 0 and state.heartbeat_count % 10 == 0:
                if not network_task or network_task.done():
                    network_task = asyncio.create_task(check_vision_network())

            current_online = state.is_online
            if network_task and network_task.done():
                current_online = network_task.result()
                network_task = None

            # 3. Calculate emotions & delay (pure functions)
            new_emotions = calculate_emotions(raw_data, current_online)
            delay = get_heartbeat_delay(new_emotions)

            # 4. Atomic state update under lock
            async with state.synaptic_lock:
                state.heartbeat_count += 1
                state.cpu_usage = raw_data["cpu"]
                state.memory_usage = raw_data["mem"]
                state.disk_usage = raw_data["disk"]
                state.battery_percent = raw_data["bat_pct"]
                state.is_plugged_in = raw_data["plugged"]
                state.temperature = raw_data["temp"]
                state.is_online = current_online

                if new_emotions != state.emotions:
                    logger.info(f"State Shift: {state.emotions} -> {new_emotions}")
                state.emotions = new_emotions
                state.short_term_memory.append(new_emotions)

                # GC flag – actual GC done outside lock
                need_gc = False
                if "OVERWHELMED" in new_emotions:
                    now = time.time()
                    if now - state.last_gc_time > 60.0:
                        state.last_gc_time = now
                        need_gc = True

            if need_gc:
                gc.collect()  # outside lock

            # Send alert if critical emotions
            await send_alert(new_emotions)

            await asyncio.sleep(delay)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Critical Heartbeat Fault: {e}")
            await asyncio.sleep(2.0)

# ==========================================
# 🌐 FASTAPI APPLICATION
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("👁️ Genesis: V46 Final is booting...")
    initial_sight = await check_vision_network()
    state.is_online = initial_sight

    # Start background rate‑limit pruning
    prune_task = asyncio.create_task(ratelimiter.prune_loop())

    state.start_heartbeat()
    logger.info("⚡ Nervous system online.")

    yield

    logger.info("🌙 Shutting down gracefully...")
    state.is_awake = False
    if state.heartbeat_task:
        state.heartbeat_task.cancel()
        try:
            await state.heartbeat_task
        except asyncio.CancelledError:
            pass
    prune_task.cancel()

app = FastAPI(title="Visva Sentient Engine V46", lifespan=lifespan)

# ---- Dependencies ----
async def verify_api_key(api_key: str = Security(api_key_header)):
    if not api_key or api_key not in API_KEYS:
        raise HTTPException(status_code=401, detail="Unauthorized access.")

# ---- Endpoints ----
@app.get("/health")
async def health():
    """Liveness probe for orchestrators. Returns 200 only if heartbeat is running."""
    if not state.is_awake or not state.heartbeat_task or state.heartbeat_task.done():
        raise HTTPException(status_code=503, detail="Engine not alive")
    return {"status": "ok", "heartbeats": state.heartbeat_count}

@app.get("/mind/read", dependencies=[Depends(ratelimiter.check), Depends(verify_api_key)])
async def read_mind():
    async with state.synaptic_lock:
        return {
            "identity": "Visva V46 – Production Final",
            "heartbeats_lived": state.heartbeat_count,
            "current_emotions": state.emotions,
            "short_term_memory": list(state.short_term_memory),
            "sensory_data": {
                "stress_cpu": f"{state.cpu_usage}%" if state.cpu_usage is not None else "N/A",
                "brain_fog_ram": f"{state.memory_usage}%" if state.memory_usage is not None else "N/A",
                "energy_battery": f"{state.battery_percent}%" if state.battery_percent is not None else "N/A",
                "fever_temp": f"{state.temperature}°C" if state.temperature is not None else "N/A",
                "clutter_disk": f"{state.disk_usage}%" if state.disk_usage is not None else "N/A",
                "sight_network": state.is_online
            }
        }

@app.post("/mind/sleep", dependencies=[Depends(ratelimiter.check), Depends(verify_api_key)])
async def put_to_sleep():
    state.is_awake = False
    if state.heartbeat_task:
        state.heartbeat_task.cancel()
    return {"status": "Going to sleep."}

@app.post("/mind/wake", dependencies=[Depends(ratelimiter.check), Depends(verify_api_key)])
async def wake_up():
    state.start_heartbeat()
    return {"status": "Waking up."}

# ==========================================
# 🔒 MAIN – SSL SUPPORT
# ==========================================
if __name__ == "__main__":
    import uvicorn
    ssl_keyfile = os.getenv("SSL_KEYFILE")
    ssl_certfile = os.getenv("SSL_CERTFILE")
    if not ssl_keyfile or not ssl_certfile:
        logger.warning("No SSL certificates provided – running without HTTPS. Set SSL_KEYFILE/SSL_CERTFILE.")
        uvicorn.run(app, host="0.0.0.0", port=8000)
    else:
        uvicorn.run(app, host="0.0.0.0", port=8000,
                    ssl_keyfile=ssl_keyfile, ssl_certfile=ssl_certfile)