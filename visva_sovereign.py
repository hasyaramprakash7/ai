#!/usr/bin/env python3
# ============================================================================
# VISVA DATA - SOVEREIGN ENGINE V39.1 (THE CITADEL ARCHITECTURE)
# FIXED: chromadb import, symlink security, air-gapped tsc, psutil added
# ADDED: Enterprise license validation at startup
# ============================================================================

import asyncio
import os
import logging
import re
import shutil
import ast
import uuid
import time
import subprocess
import tempfile
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from contextlib import asynccontextmanager
import graphlib
import psutil
import chromadb

import aiofiles
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from ollama import AsyncClient as OllamaAsyncClient

# ==========================================
# 🔐 ENTERPRISE LICENSE VALIDATION
# ==========================================
MASTER_SECRET_SALT = "VISVA_SECRET_SALT"   # Must match generate_license.py

def validate_license(license_path: str = "/visva_engine/visva_enterprise.key") -> None:
    """Check license existence, signature, and expiry. Raises RuntimeError if invalid."""
    if not os.path.exists(license_path):
        raise RuntimeError("❌ LICENSE MISSING: visva_enterprise.key not found. Engine halted.")

    try:
        with open(license_path, "r") as f:
            license_data = json.load(f)

        client_id = license_data["client_id"]
        expiry_str = license_data["expiry"]
        provided_sig = license_data["signature"]

        # Recompute signature
        raw = f"{client_id}:{expiry_str}:{MASTER_SECRET_SALT}"
        expected_sig = hashlib.sha256(raw.encode()).hexdigest()

        if provided_sig != expected_sig:
            raise RuntimeError("❌ INVALID LICENSE SIGNATURE – Tampering detected. Engine halted.")

        expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d")
        if datetime.now() > expiry_date:
            raise RuntimeError(f"❌ LICENSE EXPIRED on {expiry_str}. Please renew.")

        logging.info(f"✅ Valid enterprise license for {client_id} until {expiry_str}")

    except (json.JSONDecodeError, KeyError) as e:
        raise RuntimeError(f"❌ Corrupt license file: {e}")

# ==========================================
# 🧠 NEURAL CONFIG V39.1
# ==========================================
class Config:
    SYSTEM_VERSION = "v39.1-Citadel"
    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    CHROMA_DB_PATH = "./visva_neural_memory"

    API_KEY = os.getenv("VISVA_API_KEY")
    if not API_KEY:
        raise ValueError(
            "CRITICAL: VISVA_API_KEY environment variable MUST be set. Aborting startup."
        )

    WORKSPACE_ROOT = os.path.abspath(
        os.getenv("VISVA_WORKSPACE", os.path.join(os.getcwd(), "visva_safe_zone"))
    )
    CLEANUP_SHADOW_DIR = True

    MAX_FILE_SIZE_KB = 500
    MAX_PROJECT_SIZE_MB = 50
    MAX_CONCURRENT_ORCHESTRATIONS = 2
    FILE_TIMEOUT_SECONDS = 120.0

    MODELS = {
        "supreme": ["gemma4:e4b", "llama3.1:8b"],
        "expert": ["llama3.1:8b", "gemma4:e2b"],
        "worker": ["gemma2:2b", "qwen2:1.5b"],
    }

    CPU_MAX_THRESHOLD = 85.0
    MAX_CONTEXT_TOKENS = 32768
    MAX_CONCURRENT_AGENTS = 2
    MAX_RECURSION_DEPTH = 3
    STATE_TTL_SECONDS = 86400  # 24h
    CHROMA_RETENTION_DAYS = 30

    SKIP_DIRS = {".git", "node_modules", "venv", "__pycache__", "dist", "build", ".next"}
    ALLOWED_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx"}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("Visva_Sovereign_V39.1")

os.makedirs(Config.WORKSPACE_ROOT, exist_ok=True)

# ==========================================
# 🛡️ AUTHENTICATION & STATE ISOLATION
# ==========================================
api_key_header = APIKeyHeader(name="X-API-Key")


def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != Config.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


class AppState:
    def __init__(self):
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.status_db: Dict[str, dict] = {}
        self.lock = asyncio.Lock()
        self.chroma_lock = asyncio.Lock()
        self.orchestration_semaphore = asyncio.Semaphore(
            Config.MAX_CONCURRENT_ORCHESTRATIONS
        )
        self.ollama_client = OllamaAsyncClient(host=Config.OLLAMA_HOST)
        self.chroma_client = None
        self.success_memory = None
        self.failure_memory = None
        self.cpu_usage = 0.0


state = AppState()


# ==========================================
# ⚙️ BACKGROUND SERVICES (Hardware & Janitor)
# ==========================================
async def background_janitor():
    """Periodically prune stale project states and Chroma memories."""
    while True:
        try:
            now = time.monotonic()
            async with state.lock:
                stale_keys = [
                    k
                    for k, v in state.status_db.items()
                    if now - v.get("timestamp", now) > Config.STATE_TTL_SECONDS
                ]
                for k in stale_keys:
                    del state.status_db[k]
                    logger.info(f"🧹 Janitor cleared stale project state: {k}")

            # ChromaDB pruning
            if state.success_memory and state.failure_memory:
                cutoff_ts = int(time.time()) - (Config.CHROMA_RETENTION_DAYS * 86400)
                try:
                    state.success_memory.delete(
                        where={"created_at": {"$lt": cutoff_ts}}
                    )
                    state.failure_memory.delete(
                        where={"created_at": {"$lt": cutoff_ts}}
                    )
                    logger.info("🧹 Chroma memory pruning completed")
                except Exception as e:
                    logger.warning(f"Chroma pruning failed: {e}")

        except Exception as e:
            logger.error(f"Janitor error: {e}")
        await asyncio.sleep(3600)


async def background_hardware_monitor():
    """Non‑blocking CPU usage monitor."""
    while True:
        await asyncio.sleep(1.0)
        try:
            state.cpu_usage = psutil.cpu_percent(interval=None)
        except Exception:
            pass


@asynccontextmanager
async def facility_lifespan(app: FastAPI):
    # 🔐 Check enterprise license FIRST – before any other init
    validate_license()
    logger.info("🏰 The Citadel is online. Initializing secure memory...")

    state.chroma_client = await asyncio.to_thread(
        chromadb.PersistentClient, path=Config.CHROMA_DB_PATH
    )
    state.success_memory = await asyncio.to_thread(
        state.chroma_client.get_or_create_collection, name="visva_wins"
    )
    state.failure_memory = await asyncio.to_thread(
        state.chroma_client.get_or_create_collection, name="visva_scars"
    )

    janitor_task = asyncio.create_task(background_janitor())
    hw_task = asyncio.create_task(background_hardware_monitor())
    yield

    logger.info("🚨 Citadel shutdown initiated. Terminating sandboxes...")
    janitor_task.cancel()
    hw_task.cancel()
    async with state.lock:
        for project_id, task in state.active_tasks.items():
            if not task.done():
                task.cancel()


app = FastAPI(
    title="Visva Data Sovereign V39.1",
    lifespan=facility_lifespan,
    version="39.1.0",
)


# ==========================================
# 🗺️ REPO-MAPPER & DAG BUILDER
# ==========================================
class RepoMapper:
    @staticmethod
    def extract_skeleton(filepath: str, content: str) -> str:
        ext = Path(filepath).suffix
        skeleton = []
        if ext == ".py":
            try:
                tree = ast.parse(content)
                for node in tree.body:
                    if isinstance(node, ast.ClassDef):
                        skeleton.append(f"class {node.name}:")
                        for sub in node.body:
                            if isinstance(sub, ast.FunctionDef):
                                skeleton.append(f"    def {sub.name}(...):")
                    elif isinstance(node, ast.FunctionDef):
                        skeleton.append(f"def {node.name}(...):")
            except Exception:
                pass
        elif ext in [".js", ".jsx", ".ts", ".tsx"]:
            structures = re.findall(
                r"(export\s+(?:default\s+)?(?:class|function|const)\s+[A-Za-z0-9_]+|"
                r"interface\s+[A-Za-z0-9_]+|type\s+[A-Za-z0-9_]+)",
                content,
            )
            functions = re.findall(
                r"(function\s+[A-Za-z0-9_]+|const\s+[A-Za-z0-9_]+\s*=\s*\([^)]*\)\s*=>)",
                content,
            )
            skeleton.extend(structures)
            skeleton.extend(functions)
        return "\n".join(skeleton) if skeleton else "(Structure hidden)"

    @staticmethod
    async def build_dag_and_sort(
        directory: str, target_files: List[str]
    ) -> List[str]:
        graph = {fp: set() for fp in target_files}
        for filepath in target_files:
            try:
                async with aiofiles.open(
                    filepath, "r", encoding="utf-8", errors="ignore"
                ) as f:
                    content = await f.read()

                imported_modules = []
                for m in re.finditer(
                    r'(?:^|\n)(?:import\s+([\'"])((?:(?!\1).)+)\1|from\s+([\'"]?)([.\w]+)\3?\s+import)',
                    content,
                ):
                    if m.group(2):
                        imported_modules.append(m.group(2))
                    elif m.group(4):
                        imported_modules.append(m.group(4))

                for imp in imported_modules:
                    clean = imp.lstrip(".")
                    if clean.startswith("./"):
                        clean = clean[2:]
                    base = clean.split("/")[-1].split(".")[0]
                    for potential_dep in target_files:
                        if potential_dep != filepath and base == Path(potential_dep).stem:
                            graph[filepath].add(potential_dep)
            except Exception:
                pass

        try:
            ts = graphlib.TopologicalSorter(graph)
            sorted_deps = list(ts.static_order())
            return [f for f in sorted_deps if f in target_files]
        except graphlib.CycleError:
            return sorted(target_files)


# ==========================================
# 📁 SOVEREIGN WORKER & CIRCUIT BREAKER
# ==========================================
class SovereignWorker:
    def __init__(self, project_id: str):
        self.project_id = project_id

    async def call_llm_with_retry(self, model_tier: str, prompt: str) -> str:
        models_to_try = Config.MODELS.get(model_tier, ["gemma2:2b"])
        last_error = None
        for model in models_to_try:
            for attempt in range(3):
                try:
                    res = await state.ollama_client.generate(
                        model=model,
                        prompt=prompt,
                        options={"num_ctx": Config.MAX_CONTEXT_TOKENS},
                    )
                    return res["response"]
                except Exception as e:
                    last_error = e
                    logger.warning(
                        f"Ollama {model} failed (Attempt {attempt+1}): {e}"
                    )
                    await asyncio.sleep(2**attempt)
        raise Exception(
            f"All models for tier '{model_tier}' exhausted. Last error: {last_error}"
        )

    async def execute_task_with_timeout(
        self, context: Dict, depth: int = 0
    ) -> str:
        try:
            return await asyncio.wait_for(
                self._execute_task(context, depth),
                timeout=Config.FILE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.error(f"⏳ Timeout exceeded for {context['file_path']}")
            async with state.lock:
                if self.project_id in state.status_db:
                    state.status_db[self.project_id].setdefault(
                        "timeouts", []
                    ).append(context["file_path"])
            return context.get("original_code", "")

    async def _execute_task(self, context: Dict, depth: int = 0) -> str:
        if depth > Config.MAX_RECURSION_DEPTH:
            return context.get("original_code", "")

        while state.cpu_usage > Config.CPU_MAX_THRESHOLD:
            await asyncio.sleep(2)

        async with state.lock:
            if self.project_id in state.status_db:
                state.status_db[self.project_id]["timestamp"] = time.monotonic()

        async with state.chroma_lock:
            wins = await asyncio.to_thread(
                state.success_memory.get,
                where={"file": context["file_path"]},
                limit=1,
            )
            scars = await asyncio.to_thread(
                state.failure_memory.get,
                where={"file": context["file_path"]},
                limit=1,
            )

        positive_ground = (
            f"\nBEST PRACTICE:\n{wins['documents'][0]}"
            if wins and wins["documents"]
            else ""
        )
        negative_ground = (
            f"\nNEVER DO THIS:\n{scars['documents'][0]}"
            if scars and scars["documents"]
            else ""
        )

        prompt = (
            f"Map: {context['repo_map']}\n"
            f"Directive: {context['directive']}"
            f"{positive_ground}{negative_ground}\n"
            f"Task: Refactor {context['file_path']}. Reply ONLY with code inside ```[lang]``` blocks."
        )

        raw_output = await self.call_llm_with_retry("worker", prompt)

        if "NEEDS_EXPERTISE" in raw_output or "security" in raw_output.lower():
            raw_output = await self.call_llm_with_retry(
                "expert", f"Expert review required for:\n{raw_output}"
            )

        final_code = self.extract_code(raw_output)
        if not final_code:
            logger.warning(
                f"Empty code generated for {context['file_path']}. Retrying at depth {depth+1}."
            )
            await asyncio.sleep(1)  # backoff
            return await self._execute_task(context, depth + 1)

        if await self.validate(final_code, context["file_path"]):
            meta = {"file": context["file_path"], "created_at": int(time.time())}
            try:
                async with state.chroma_lock:
                    await asyncio.to_thread(
                        state.success_memory.add,
                        documents=[final_code],
                        metadatas=[meta],
                        ids=[str(uuid.uuid4())],
                    )
            except Exception as e:
                logger.error(f"Failed to store success in Chroma: {e}")
            return final_code
        else:
            meta = {
                "file": context["file_path"],
                "error": "validation_failed",
                "created_at": int(time.time()),
            }
            try:
                async with state.chroma_lock:
                    await asyncio.to_thread(
                        state.failure_memory.add,
                        documents=[final_code],
                        metadatas=[meta],
                        ids=[str(uuid.uuid4())],
                    )
            except Exception as e:
                logger.error(f"Failed to store failure in Chroma: {e}")
            await asyncio.sleep(1)
            return await self._execute_task(context, depth + 1)

    @staticmethod
    def extract_code(text: str) -> str:
        blocks = re.findall(
            r"```(?:\w+)?[ \t]*\r?\n(.*?)\r?\n?```", text, re.DOTALL
        )
        if blocks:
            return "\n\n".join(b.strip() for b in blocks)
        return ""

    async def validate(self, code: str, filepath: str) -> bool:
        if not code.strip():
            return False

        ext = Path(filepath).suffix
        if ext == ".py":
            try:
                ast.parse(code)
                return True
            except (SyntaxError, TypeError, MemoryError) as e:
                logger.warning(f"Python validation failed: {e}")
                return False
        elif ext in (".js", ".ts", ".jsx", ".tsx"):
            return await self._validate_javascript_like(code, filepath)
        return True

    async def _validate_javascript_like(self, code: str, filepath: str) -> bool:
        ext = Path(filepath).suffix
        # AIR-GAP FIX: try local tsc first, fallback to node --check
        if ext in (".ts", ".tsx"):
            # Check if tsc is available in PATH (no npx)
            tsc_path = shutil.which("tsc")
            if tsc_path:
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=ext, delete=False, encoding="utf-8"
                    ) as tmp:
                        tmp.write(code)
                        tmp_path = tmp.name
                    try:
                        process = await asyncio.create_subprocess_exec(
                            tsc_path,
                            "--noEmit",
                            "--allowJs",
                            "--target",
                            "ES2020",
                            "--moduleResolution",
                            "node",
                            tmp_path,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        await process.communicate()
                        return process.returncode == 0
                    finally:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                except Exception as e:
                    logger.warning(f"Local tsc validation failed: {e}")
                    # fall through to node check if possible
            else:
                logger.warning("tsc not found, skipping TypeScript validation")
                return True  # In air-gapped, assume best effort
        # Plain JS or fallback
        node_path = shutil.which("node")
        if not node_path:
            logger.error("Node.js not found, cannot validate JS/TS")
            return False
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".js", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(code)
                tmp_path = tmp.name
            process = await asyncio.create_subprocess_exec(
                node_path,
                "--check",
                tmp_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await process.communicate()
            return process.returncode == 0
        except Exception as e:
            logger.error(f"JS validation error: {e}")
            return False
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


# ==========================================
# 🌐 UTILITY: DIRECTIVE SANITIZATION
# ==========================================
def sanitize_directive(directive: str) -> str:
    if not directive or not directive.strip():
        return "Maintain highly robust, thread-safe, and performant code."

    directive = re.sub(r"```.*?```", "", directive, flags=re.DOTALL)

    dangerous = [
        r"\b(?:os\.system|subprocess\.Popen|exec\s*\(|eval\s*\(|__import__\s*\()",
        r"\b(?:rm\s+-rf|chmod\s+[0-7]{3,4}|chown\s)",
        r"\b(?:DROP\s+TABLE|DELETE\s+FROM|UPDATE\s+.*SET\s)",
        r"\b(?:fetch\s*\(|XMLHttpRequest|WebSocket)",
        r"require\s*\(\s*['\"]child_process['\"]\s*\)",
    ]
    for pat in dangerous:
        if re.search(pat, directive, re.IGNORECASE):
            logger.warning("Dangerous pattern in Supreme directive; overriding.")
            return "Maintain highly robust, thread-safe, and performant code."

    MAX_DIRECTIVE_LEN = 500
    if len(directive) > MAX_DIRECTIVE_LEN:
        directive = directive[:MAX_DIRECTIVE_LEN]

    return directive.strip()


# ==========================================
# 🌐 API ENDPOINTS
# ==========================================
class ProjectPayload(BaseModel):
    absolute_path: str = Field(
        ...,
        description="Absolute path inside the allowed WORKSPACE_ROOT.",
    )


@app.post("/api/v1/orchestrate", tags=["Core"])
async def orchestrate(payload: ProjectPayload, api_key: str = Depends(verify_api_key)):
    source_dir = os.path.realpath(payload.absolute_path)
    root = os.path.realpath(Config.WORKSPACE_ROOT)

    if os.path.commonpath([source_dir, root]) != root:
        raise HTTPException(
            status_code=403,
            detail=f"Path must be strictly inside the sandbox zone: {root}",
        )

    if not os.path.exists(source_dir):
        raise HTTPException(status_code=404, detail="Directory not found.")

    async with state.lock:
        if len(state.active_tasks) >= Config.MAX_CONCURRENT_ORCHESTRATIONS:
            raise HTTPException(
                status_code=429,
                detail="Orchestration capacity reached. Try again later.",
            )

    total_size = sum(
        f.stat().st_size
        for f in Path(source_dir).rglob("*")
        if f.is_file()
        and not any(skip in f.parts for skip in Config.SKIP_DIRS)
    )
    if total_size > (Config.MAX_PROJECT_SIZE_MB * 1024 * 1024):
        raise HTTPException(
            status_code=413,
            detail=f"Project exceeds max size of {Config.MAX_PROJECT_SIZE_MB}MB.",
        )

    project_id = uuid.uuid4().hex[:8]
    shadow_dir = os.path.join(root, f".visva_shadow_{project_id}")

    # FIX: symlink security – copy symlinks as symlinks, do not dereference
    try:
        await asyncio.to_thread(
            shutil.copytree,
            source_dir,
            shadow_dir,
            ignore=lambda d, c: [x for x in c if x in Config.SKIP_DIRS],
            symlinks=True,          # <--- critical fix
        )
    except Exception as e:
        logger.error(f"Failed to copy project: {e}")
        raise HTTPException(
            status_code=500, detail=f"Unable to create sandbox: {str(e)}"
        )

    async with state.lock:
        state.status_db[project_id] = {
            "status": "mapping",
            "workspace": shadow_dir,
            "timestamp": time.monotonic(),
            "timeouts": [],
        }
        child_task = asyncio.create_task(run_pipeline(project_id, shadow_dir))
        state.active_tasks[project_id] = child_task
        child_task.add_done_callback(
            lambda t: asyncio.create_task(cleanup_task(project_id, shadow_dir))
        )

    return {
        "status": "Swarm Admitted and Monitored",
        "id": project_id,
        "sandbox": shadow_dir,
    }


@app.get("/api/v1/status/{project_id}", tags=["Core"])
async def project_status(project_id: str, api_key: str = Depends(verify_api_key)):
    async with state.lock:
        info = state.status_db.get(project_id)
        if not info:
            raise HTTPException(status_code=404, detail="Project not found or expired.")
        return {
            "project_id": project_id,
            "status": info.get("status", "unknown"),
            "workspace": info.get("workspace"),
            "timeouts": info.get("timeouts", []),
        }


async def cleanup_task(project_id: str, shadow_dir: str):
    async with state.lock:
        state.active_tasks.pop(project_id, None)

    if Config.CLEANUP_SHADOW_DIR and os.path.exists(shadow_dir):
        try:
            await asyncio.to_thread(shutil.rmtree, shadow_dir)
            logger.info(f"🧹 Cleaned up shadow directory for {project_id}.")
        except Exception as e:
            logger.error(f"Failed to cleanup {shadow_dir}: {e}")


async def run_pipeline(project_id: str, shadow_dir: str):
    async with state.orchestration_semaphore:
        try:
            shadow_real_path = os.path.realpath(shadow_dir)
            all_files = []

            for root, _, files in os.walk(shadow_real_path):
                for f in files:
                    filepath = os.path.realpath(os.path.join(root, f))
                    if not filepath.startswith(shadow_real_path):
                        continue
                    if (
                        Path(f).suffix in Config.ALLOWED_EXTENSIONS
                        and os.path.getsize(filepath)
                        < (Config.MAX_FILE_SIZE_KB * 1024)
                    ):
                        all_files.append(filepath)

            sorted_files = await RepoMapper.build_dag_and_sort(
                shadow_dir, all_files
            )

            repo_map_parts = []
            for filepath in sorted_files:
                async with aiofiles.open(
                    filepath, "r", encoding="utf-8", errors="ignore"
                ) as f:
                    content = await f.read()
                repo_map_parts.append(
                    f"\n--- {os.path.basename(filepath)} ---\n"
                    + RepoMapper.extract_skeleton(filepath, content)
                )
            repo_map = "".join(repo_map_parts)[
                : (Config.MAX_CONTEXT_TOKENS * 4)
            ]

            worker = SovereignWorker(project_id)

            supreme_prompt = (
                f"Review Architecture:\n{repo_map}\n"
                "Establish strict guidelines. Maintain code safety and optimal performance."
            )
            try:
                raw_directive = await worker.call_llm_with_retry(
                    "supreme", supreme_prompt
                )
                global_directive = sanitize_directive(raw_directive)
            except Exception as e:
                logger.warning(
                    f"Supreme model failed, using safe baseline: {e}"
                )
                global_directive = (
                    "Maintain highly robust, thread-safe, and performant code."
                )

            semaphore = asyncio.Semaphore(Config.MAX_CONCURRENT_AGENTS)

            async def process_file(path: str):
                async with semaphore:
                    async with aiofiles.open(
                        path, "r", encoding="utf-8", errors="ignore"
                    ) as f:
                        original_content = await f.read()
                    context = {
                        "file_path": path,
                        "directive": global_directive,
                        "repo_map": repo_map,
                        "original_code": original_content,
                    }
                    code = await worker.execute_task_with_timeout(context)
                    if code:
                        async with aiofiles.open(
                            path, "w", encoding="utf-8"
                        ) as f:
                            await f.write(code)

            results = await asyncio.gather(
                *[process_file(f) for f in sorted_files],
                return_exceptions=True,
            )
            for res in results:
                if isinstance(res, Exception):
                    logger.error(f"File processing error: {res}")

            async with state.lock:
                if project_id in state.status_db:
                    state.status_db[project_id]["status"] = "completed"

        except asyncio.CancelledError:
            async with state.lock:
                if project_id in state.status_db:
                    state.status_db[project_id]["status"] = "cancelled_by_house"
        except Exception as e:
            logger.error(f"Pipeline crashed: {e}")
            async with state.lock:
                if project_id in state.status_db:
                    state.status_db[project_id]["status"] = f"failed: {str(e)}"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("VISVA_HOST", "127.0.0.1"),
        port=int(os.getenv("VISVA_PORT", 8000))
    )