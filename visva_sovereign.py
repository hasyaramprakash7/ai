#!/usr/bin/env python3
# ============================================================================
# VISVA DATA - APEX SOVEREIGN V106 (PINNACLE CORE)
# Features: License Validation, POSIX Paths, Smart Regex Chunking,
#           Leak-Free Locks, Neural Dreams, Zero-Latency Directory Moves.
# ============================================================================

import asyncio, os, sys, hashlib, ctypes, logging, re, time, sqlite3, random, json
from collections import OrderedDict, defaultdict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import psutil
import chromadb
from chromadb.utils import embedding_functions
from chromadb.errors import InvalidDimensionException
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from ollama import AsyncClient as OllamaAsyncClient

try:
    import pynvml
    HAS_NVML = True
except ImportError:
    HAS_NVML = False

# ==========================================
# 🛡️ LICENSE VALIDATION
# ==========================================
def validate_license():
    key_path = os.getenv("VISVA_KEY_FILE", "/visva_engine/visva_enterprise.key")
    api_key = os.getenv("VISVA_API_KEY", "")
    if not api_key:
        logger.critical("VISVA_API_KEY missing. Engine locked.")
        sys.exit(1)
    try:
        with open(key_path, "r") as f:
            license_data = json.load(f)
        expected = license_data.get("signature", "")
        if api_key != expected:
            raise ValueError("Key mismatch")
        expiry = license_data.get("expiry", "1970-01-01")
        if time.strptime(expiry, "%Y-%m-%d") < time.localtime():
            raise ValueError("License expired")
        logger.info("✅ Enterprise license valid")
    except Exception as e:
        logger.critical(f"License invalid: {e}")
        sys.exit(1)

# ==========================================
# 🧠 CORE CONFIGURATION
# ==========================================
class Config:
    SYSTEM_VERSION = "Apex-Sovereign-v106-Pinnacle-Core"
    MODEL_NAME = os.getenv("VISVA_MODEL", "gemma2:2b")
    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    CHROMA_DB_PATH = "./visva_neural_core"
    MANIFEST_DB_PATH = "./visva_neural_core/manifest.sqlite3"
    STORAGE_ROOT = Path(os.getenv("VISVA_STORAGE_ROOT", "./visva_os_root")).as_posix()
    
    IGNORE_DIRS = {'.git', 'node_modules', '__pycache__', 'build', 'dist', '.expo', 'venv', '.venv', 'Windows', 'Program Files'}
    IGNORE_EXTS = {'.exe', '.dll', '.png', '.jpg', '.mp4', '.zip', '.pdf', '.bin', '.tar', '.gz', '.iso'}
    
    CHUNK_SIZE = 1000
    MAX_FILE_SIZE = 5 * 1024 * 1024  
    MAX_QUERY_LENGTH = 500
    BATCH_SIZE = 50 
    
    DREAM_IDLE_TIME = 300 
    DREAM_PROMPTS = ["architecture", "supply chain logic", "user interface styling", "database optimization", "API integration", "deployment strategy"]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("VisvaApex")

# ==========================================
# 🗄️ MANIFEST DATABASE (Ledger)
# ==========================================
class ManifestDB:
    def __init__(self, db_path):
        self.db_path = Path(db_path).as_posix()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL)")
            
    def get_all(self):
        with sqlite3.connect(self.db_path) as conn:
            return {row[0]: row[1] for row in conn.execute("SELECT path, mtime FROM files")}
            
    def upsert(self, path, mtime):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO files (path, mtime) VALUES (?, ?)", (path, mtime))
            
    def delete(self, path):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM files WHERE path = ?", (path,))
            
    def delete_prefix(self, prefix):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT path FROM files WHERE path LIKE ?", (prefix + '%',))
            paths = [row[0] for row in cursor.fetchall()]
            conn.execute("DELETE FROM files WHERE path LIKE ?", (prefix + '%',))
            return paths

# ==========================================
# ⚡ TRUE O(1) NEURAL CACHE
# ==========================================
class TrueLRUCache:
    def __init__(self, maxsize: int = 100000):
        self.cache = OrderedDict()
        self.maxsize = maxsize
        self._lock = asyncio.Lock()

    async def get(self, key):
        async with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            return None

    async def put(self, key, value):
        async with self._lock:
            self.cache[key] = value
            self.cache.move_to_end(key)
            if len(self.cache) > self.maxsize:
                self.cache.popitem(last=False)

# ==========================================
# 🧬 UNIFIED STATE & OS HIJACK
# ==========================================
class UnifiedState:
    def __init__(self):
        self.is_awake = False
        self.last_activity = time.time()
        self.metrics = {"cpu": 0.0, "gpu_temp": [], "vram_gb": []}
        
        self._hijack_os()
        
        self.db_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="ChromaDB")
        self.fs_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="FileIO")
        
        self.think_semaphore = asyncio.Semaphore(10) 
        self.fs_semaphore = asyncio.Semaphore(20) 
        self.file_locks = defaultdict(asyncio.Lock)
        
        self.chroma_client = chromadb.PersistentClient(path=Config.CHROMA_DB_PATH)
        self.embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        
        try:
            self.embed_fn(["Visva init"])
        except Exception as e:
            logger.critical(f"Embedding model failure: {e}")
            sys.exit(1)
            
        try:
            self.synapse = self.chroma_client.get_or_create_collection(name="system_synapse", embedding_function=self.embed_fn)
        except (InvalidDimensionException, ValueError):
            logger.warning("Collection mismatch. Rebuilding Synapse...")
            self.chroma_client.delete_collection("system_synapse")
            self.synapse = self.chroma_client.get_or_create_collection(name="system_synapse", embedding_function=self.embed_fn)
        
        self.ollama = OllamaAsyncClient(host=Config.OLLAMA_HOST)
        self.neural_cache = TrueLRUCache()
        self.manifest = ManifestDB(Config.MANIFEST_DB_PATH)
        self.indexed_files = self.manifest.get_all()
        
        if HAS_NVML:
            try:
                pynvml.nvmlInit()
                self.gpu_count = pynvml.nvmlDeviceGetCount()
            except pynvml.NVMLError:
                self.gpu_count = 0
        else:
            self.gpu_count = 0

    def update_activity(self):
        self.last_activity = time.time()

    def _hijack_os(self):
        try:
            p = psutil.Process(os.getpid())
            if os.name == 'nt':
                p.nice(psutil.HIGH_PRIORITY_CLASS) 
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000001) 
            else:
                p.nice(-10)
            logger.info("🛡️ OS Priority Hijacked.")
        except Exception as e:
            logger.debug(f"OS hijack skipped: {e}")

state = UnifiedState()

# ==========================================
# 🌌 THE NEURAL DREAM SEQUENCE
# ==========================================
async def neural_dream_sequence():
    while state.is_awake:
        await asyncio.sleep(60) 
        idle_time = time.time() - state.last_activity
        if idle_time > Config.DREAM_IDLE_TIME:
            logger.info("🌌 Entering REM Sleep...")
            seed_concept = random.choice(Config.DREAM_PROMPTS)
            try:
                results = await asyncio.get_running_loop().run_in_executor(
                    state.db_executor, 
                    lambda: state.synapse.query(query_texts=[seed_concept], n_results=4)
                )
                memories = "\n".join(results['documents'][0]) if results and results['documents'] else ""
            except Exception:
                continue
            if not memories: continue
            prompt = f"You are an AI dreaming. Review these memories:\n{memories}\n\nSynthesize a single hidden technical insight from this data. Output ONLY the insight."
            try:
                async with state.think_semaphore:
                    resp = await state.ollama.generate(model=Config.MODEL_NAME, prompt=prompt)
                    insight = f"[Dream | {time.strftime('%Y-%m-%d %H:%M')}]\n" + resp['response'].strip()
                dream_id = hashlib.sha256(f"dream_{time.time()}".encode()).hexdigest()
                await asyncio.get_running_loop().run_in_executor(
                    state.db_executor,
                    lambda: state.synapse.upsert(
                        ids=[dream_id], 
                        metadatas=[{"path": "neural_dreamscape", "mtime": time.time(), "type": "dream"}], 
                        documents=[insight]
                    )
                )
                logger.info("✨ Dream saved to Synapse.")
                state.update_activity()
            except Exception as e:
                logger.error(f"Dream failed: {e}")

# ==========================================
# 🧠 SMART CHUNKER & I/O
# ==========================================
def is_valid_file(file_path: str) -> bool:
    path_obj = Path(file_path)
    if path_obj.suffix.lower() in Config.IGNORE_EXTS: return False
    if any(part in Config.IGNORE_DIRS for part in path_obj.parts): return False
    return True

def read_text_safe(file_path: str) -> str:
    try:
        with open(file_path, 'rb') as f:
            if b'\x00' in f.read(1024): return ""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read(Config.MAX_FILE_SIZE)
    except Exception:
        return ""

def sentence_aware_chunking(file_path: str, content: str, mtime: float) -> list:
    file_name = Path(file_path).name
    clean_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime))
    header = f"[Type: Factual Document | File: {file_name} | Modified: {clean_time}]\n"
    
    sentences = re.split(r'(?<!\b[A-Z][a-z]\.)(?<!\b[A-Z]\.)(?<=\.|\?|\!)\s+(?=[A-Z])', content)
    chunks, current_chunk, current_length = [], [], len(header)
    
    for sentence in sentences:
        if not sentence.strip(): continue
        if current_length + len(sentence) > Config.CHUNK_SIZE and current_chunk:
            chunks.append({
                "id": hashlib.sha256(f"{file_path}_{len(chunks)}".encode()).hexdigest(),
                "text": header + " ".join(current_chunk)
            })
            overlap = current_chunk[-2:] if len(current_chunk) >= 2 else current_chunk[-1:]
            current_chunk = overlap + [sentence]
            current_length = len(header) + sum(len(s) + 1 for s in current_chunk)
        else:
            current_chunk.append(sentence)
            current_length += len(sentence) + 1
            
    if current_chunk:
        chunks.append({
            "id": hashlib.sha256(f"{file_path}_{len(chunks)}".encode()).hexdigest(),
            "text": header + " ".join(current_chunk)
        })
    return chunks

async def process_file(file_path: str):
    state.update_activity()
    posix_path = Path(file_path).as_posix()
    if not is_valid_file(posix_path): return

    async with state.fs_semaphore: 
        async with state.file_locks[posix_path]:
            try:
                mtime = os.path.getmtime(posix_path)
            except FileNotFoundError:
                return
            if state.indexed_files.get(posix_path) == mtime: return
            content = await asyncio.get_running_loop().run_in_executor(state.fs_executor, read_text_safe, posix_path)
            if not content: return
            chunks = sentence_aware_chunking(posix_path, content, mtime)
            def _upsert():
                state.synapse.delete(where={"path": posix_path})
                if chunks:
                    state.synapse.upsert(
                        ids=[c["id"] for c in chunks], 
                        metadatas=[{"path": posix_path, "mtime": mtime, "type": "document"} for _ in chunks], 
                        documents=[c["text"] for c in chunks]
                    )
                state.manifest.upsert(posix_path, mtime)
            await asyncio.get_running_loop().run_in_executor(state.db_executor, _upsert)
            state.indexed_files[posix_path] = mtime

async def purge_file(file_path: str):
    posix_path = Path(file_path).as_posix()
    async with state.file_locks[posix_path]:
        def _delete():
            state.synapse.delete(where={"path": posix_path})
            state.manifest.delete(posix_path)
        await asyncio.get_running_loop().run_in_executor(state.db_executor, _delete)
        state.indexed_files.pop(posix_path, None)
    state.file_locks.pop(posix_path, None)

async def purge_directory(dir_path: str):
    posix_path = Path(dir_path).as_posix()
    prefix = posix_path if posix_path.endswith('/') else posix_path + '/'
    def _delete_prefix():
        paths_to_delete = state.manifest.delete_prefix(prefix)
        for p in paths_to_delete:
            state.synapse.delete(where={"path": p})
        return paths_to_delete
    deleted_paths = await asyncio.get_running_loop().run_in_executor(state.db_executor, _delete_prefix)
    for p in deleted_paths:
        state.indexed_files.pop(p, None)
    logger.info(f"🗑️ Purged dir: {posix_path} ({len(deleted_paths)} items)")

# ==========================================
# 👁️ THE WATCHMAN 
# ==========================================
class NeuralWatcher(FileSystemEventHandler):
    def __init__(self, loop): self.loop = loop
    def on_modified(self, event):
        if not event.is_directory: asyncio.run_coroutine_threadsafe(process_file(event.src_path), self.loop)
    def on_created(self, event):
        if not event.is_directory: asyncio.run_coroutine_threadsafe(process_file(event.src_path), self.loop)
    def on_deleted(self, event):
        if event.is_directory: asyncio.run_coroutine_threadsafe(purge_directory(event.src_path), self.loop)
        else: asyncio.run_coroutine_threadsafe(purge_file(event.src_path), self.loop)
    def on_moved(self, event):
        if event.is_directory: 
            asyncio.run_coroutine_threadsafe(purge_directory(event.src_path), self.loop)
            asyncio.run_coroutine_threadsafe(walk_and_queue(event.dest_path), self.loop)
        else:
            asyncio.run_coroutine_threadsafe(purge_file(event.src_path), self.loop)
            asyncio.run_coroutine_threadsafe(process_file(event.dest_path), self.loop)

# ==========================================
# ⚔️ MASSIVE DATA SWEEPERS
# ==========================================
def offloaded_walk(root_path):
    paths = []
    for root, dirs, files in os.walk(root_path):
        dirs[:] = [d for d in dirs if d not in Config.IGNORE_DIRS]
        for file in files: paths.append(Path(os.path.join(root, file)).as_posix())
    return paths

async def walk_and_queue(target_dir):
    all_files = await asyncio.get_running_loop().run_in_executor(state.fs_executor, offloaded_walk, target_dir)
    for file_path in all_files:
        await process_file(file_path)

async def conquer_massive_drive(target_dir=Config.STORAGE_ROOT):
    logger.info(f"⚔️ Conquering Storage: {target_dir} ({len(state.indexed_files)} files in manifest)")
    os.makedirs(target_dir, exist_ok=True)
    all_files = await asyncio.get_running_loop().run_in_executor(state.fs_executor, offloaded_walk, target_dir)
    tasks = []
    for file_path in all_files:
        tasks.append(process_file(file_path))
        if len(tasks) >= Config.BATCH_SIZE: 
            await asyncio.gather(*tasks)
            tasks = []
    if tasks: await asyncio.gather(*tasks)
    logger.info("✅ Storage Conquered. Engine live.")

# ==========================================
# 🫀 HARDWARE TELEMETRY 
# ==========================================
async def monitor_hardware():
    while state.is_awake:
        try:
            state.metrics["cpu"] = psutil.cpu_percent()
            if state.gpu_count > 0:
                temps, vrams = [], []
                for i in range(state.gpu_count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                    temps.append(pynvml.nvmlDeviceGetTemperature(handle, 0))
                    mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    vrams.append(mem.used / (1024**3))
                state.metrics["gpu_temp"] = temps
                state.metrics["vram_gb"] = vrams
        except Exception:
            pass
        await asyncio.sleep(5)

# ==========================================
# 🌐 CITADEL FASTAPI
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_license()   # 🔐 block start if license invalid
    state.is_awake = True
    app.state.hw_task = asyncio.create_task(monitor_hardware())
    app.state.drive_task = asyncio.create_task(conquer_massive_drive())
    app.state.dream_task = asyncio.create_task(neural_dream_sequence())
    
    observer = Observer()
    observer.schedule(NeuralWatcher(asyncio.get_running_loop()), Config.STORAGE_ROOT, recursive=True)
    observer.start()
    
    logger.info(f"🚀 {Config.SYSTEM_VERSION} IS ONLINE.")
    yield
    
    state.is_awake = False
    app.state.hw_task.cancel()
    app.state.drive_task.cancel()
    app.state.dream_task.cancel()
    observer.stop()
    observer.join()
    state.db_executor.shutdown(wait=True)
    state.fs_executor.shutdown(wait=True)
    logger.info("🛑 Apex Engine offline.")

app = FastAPI(title="Visva Data Apex", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "operational", "version": Config.SYSTEM_VERSION}

class Intent(BaseModel):
    query: str

@app.post("/think")
async def think(intent: Intent):
    state.update_activity() 
    
    if len(intent.query) > Config.MAX_QUERY_LENGTH:
        raise HTTPException(status_code=400, detail="Query too long.")
        
    query = re.sub(r'[^\w\s\?.,!\-\'":;/\\(){}\[\]]', '', intent.query.strip())
    if not query: raise HTTPException(status_code=400, detail="Invalid query.")

    intent_hash = hashlib.sha256(query.encode()).hexdigest()
    cached = await state.neural_cache.get(intent_hash)
    if cached: return {"answer": cached, "source": "O(1) Memory"}

    async with state.think_semaphore:
        try:
            results = await asyncio.get_running_loop().run_in_executor(
                state.db_executor, 
                lambda: state.synapse.query(query_texts=[query], n_results=5)
            )
            context = "\n".join(results['documents'][0]) if results and results['documents'] else ""
        except Exception as e:
            logger.error(f"DB query failed: {e}")
            context = ""

        prompt = f"Context from validated files and synthesized insights:\n{context}\n\nQuery: {query}\nAnswer directly based on context:"
        try:
            resp = await state.ollama.generate(model=Config.MODEL_NAME, prompt=prompt)
            answer = resp['response']
            await state.neural_cache.put(intent_hash, answer)
            return {"answer": answer, "source": "Gemma Inference"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Inference failure: {str(e)}")

if __name__ == "__main__":
    uvicorn.run("visva_sovereign:app", host="127.0.0.1", port=8000, reload=False)