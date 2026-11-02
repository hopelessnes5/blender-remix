import bpy
import bmesh
import os
import sys
import time
import asyncio
import threading
import subprocess
import hashlib
import uuid
import json
import tempfile
from typing import Optional, Tuple, Dict, Any, List, Callable
from concurrent.futures import ThreadPoolExecutor


def _require_blender_main_thread():
    """Reject Blender datablock access from worker threads."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('Blender image operations require the main thread')


def _progress_on_loop(loop, callback, gates=None):
    """Forward worker progress to the event loop that started the operation."""
    if callback is None:
        return None

    def deliver(message):
        if gates is None or any(not gate.is_cancelled() for gate in gates):
            callback(message)

    def forward(message):
        try:
            loop.call_soon_threadsafe(deliver, message)
        except RuntimeError:
            # The caller closed its loop while the worker was still running.
            pass

    return forward

try:
    from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf, Vt
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False

from . import constants

# Global thread pool for async operations
_thread_pool = None
_texture_queue = None

def get_thread_pool():
    """Get or create the global thread pool for async operations."""
    global _thread_pool
    if _thread_pool is None:
        # Use CPU count for optimal parallel processing
        # Each worker will run its own texconv process, so we want one per core
        cpu_count = os.cpu_count() or 4
        max_workers = min(cpu_count, 8)  # Cap at 8 to prevent resource exhaustion
        _thread_pool = ThreadPoolExecutor(max_workers=max_workers)
        print(f"[TextureProcessor] Initialized thread pool with {max_workers} workers for {cpu_count} CPU cores")
    return _thread_pool

def get_texture_queue():
    """Get or create the global texture processing queue."""
    global _texture_queue
    if _texture_queue is None:
        _texture_queue = TextureProcessingQueue()
    return _texture_queue

def cleanup_thread_pool():
    """Cleanup the global thread pool and texture queue."""
    global _thread_pool, _texture_queue
    if _texture_queue is not None:
        _texture_queue.cancel_tasks(list(_texture_queue.active_tasks), 'Addon shutdown')
        for task in _texture_queue.active_tasks.values():
            task.progress_callback = None
        _texture_queue.shutdown()
        _texture_queue = None
    if _thread_pool is not None:
        # Let queued workers run their finally blocks to remove prepared files.
        # Publication gates are already cancelled, so they must not publish.
        _thread_pool.shutdown(wait=False, cancel_futures=False)
        _thread_pool = None

# --- Path Utilities ---

def get_relative_path(from_path: str, to_path: str) -> str:
    """Calculates the relative path from one file to another with proper cross-platform handling."""
    try:
        from_dir = os.path.dirname(from_path)
        abs_from_path = os.path.abspath(from_dir)
        abs_to_path = os.path.abspath(to_path)
        rel_path = os.path.relpath(abs_to_path, start=abs_from_path)
        
        # Normalize path separators for USD (always forward slashes)
        rel_path = rel_path.replace('\\', '/')
        
        # Ensure relative paths start with './' for clarity
        if not rel_path.startswith("../") and not rel_path.startswith("./") and rel_path != ".":
            rel_path = "./" + rel_path
            
        return rel_path
    except Exception as e:
        print(f"Error calculating relative path from '{from_path}' to '{to_path}': {e}")
        return to_path

def sanitize_prim_name(name: str) -> str:
    """Sanitizes a name for use as a USD prim name."""
    invalid_chars = " .!@#$%^&*()-+=[]{};:'\"<>,/?`~"
    sanitized = name
    for char in invalid_chars:
        sanitized = sanitized.replace(char, '_')
    
    # Ensure it starts with a letter or underscore
    if not sanitized or not (sanitized[0].isalpha() or sanitized[0] == '_'):
        sanitized = '_' + sanitized
    return sanitized

def generate_uuid_name(name: str, prefix: str = "ref_") -> str:
    """Generates a UUID-style name based on the input name for RTX Remix compatibility."""
    import uuid
    uuid_seed = uuid.uuid5(uuid.NAMESPACE_DNS, name)
    return f"{prefix}{uuid_seed.hex[:32]}"

# --- Material Utilities ---

class MaterialPathResolver:
    """Handles material path resolution and caching."""
    
    def __init__(self, usd_file_path_context: str):
        self.usd_file_path_context = usd_file_path_context
        self._texture_dir_cache = {}
    
    def find_texture_dir(self) -> Optional[str]:
        """Find texture directory with caching."""
        if self.usd_file_path_context in self._texture_dir_cache:
            return self._texture_dir_cache[self.usd_file_path_context]
        
        usd_dir = os.path.dirname(self.usd_file_path_context)
        mod_dir = os.path.dirname(usd_dir)
        mod_root_dir = os.path.dirname(mod_dir)

        potential_dirs = [
            os.path.join(mod_dir, "captures", "textures"),
            os.path.join(mod_dir, "assets", "textures"),
            os.path.join(mod_dir, "assets"),
            os.path.join(mod_root_dir, "assets", "textures"),
            os.path.join(mod_root_dir, "assets"),
            os.path.join(mod_root_dir, "textures"),
        ]

        for p_dir in potential_dirs:
            if os.path.isdir(p_dir):
                self._texture_dir_cache[self.usd_file_path_context] = p_dir
                return p_dir
        
        self._texture_dir_cache[self.usd_file_path_context] = None
        return None

def create_material_cache_key(base_material_path: str, instance_metadata: Dict[str, Any]) -> str:
    """Create a unique cache key for materials with metadata."""
    metadata_hash = ""
    if instance_metadata:
        sorted_meta_string = json.dumps(instance_metadata, sort_keys=True)
        metadata_hash = hashlib.md5(sorted_meta_string.encode('utf-8')).hexdigest()[:8]
    
    return f"{base_material_path}_{metadata_hash}" if metadata_hash else base_material_path

# --- USD Stage Utilities ---

class USDStageManager:
    """Manages USD stage operations with proper resource cleanup."""
    
    def __init__(self):
        self._open_stages = {}
    
    def get_or_open_stage(self, file_path: str) -> Optional[Usd.Stage]:
        """Get or open a USD stage with caching."""
        if not USD_AVAILABLE:
            return None
        
        abs_path = os.path.abspath(file_path)
        if abs_path in self._open_stages:
            return self._open_stages[abs_path]
        
        try:
            stage = Usd.Stage.Open(abs_path, Usd.Stage.LoadAll)
            if stage:
                self._open_stages[abs_path] = stage
                return stage
        except Exception as e:
            print(f"Error opening USD stage '{abs_path}': {e}")
        
        return None
    
    def close_stage(self, file_path: str):
        """Close and remove a stage from cache."""
        abs_path = os.path.abspath(file_path)
        if abs_path in self._open_stages:
            # USD stages don't have explicit close, just remove from cache
            del self._open_stages[abs_path]
    
    def cleanup_all(self):
        """Cleanup all cached stages."""
        self._open_stages.clear()

# Global stage manager instance
_stage_manager = None

def get_stage_manager():
    """Get or create the global stage manager."""
    global _stage_manager
    if _stage_manager is None:
        _stage_manager = USDStageManager()
    return _stage_manager

# --- Async Texture Processing Queue ---

import weakref

_publication_gates = weakref.WeakSet()
_publication_gates_lock = threading.Lock()


def cancel_all_publications():
    """Block all in-flight publications before unloading the add-on."""
    with _publication_gates_lock:
        gates = list(_publication_gates)
    for gate in gates:
        gate.cancel()


class _PublicationGate:
    """Serialize cancellation with the final filesystem publication."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False
        with _publication_gates_lock:
            _publication_gates.add(self)

    def cancel(self):
        with self._lock:
            self._cancelled = True

    def is_cancelled(self):
        with self._lock:
            return self._cancelled

    def publish(self, source, destination):
        with self._lock:
            if self._cancelled:
                return False
            if source != destination:
                os.replace(source, destination)
            return True


class TextureTask:
    """Represents a texture processing task."""
    
    def __init__(self, task_id: str, bl_image: bpy.types.Image, output_path: str, 
                 texture_type: str = 'base color', dds_format: Optional[str] = None,
                 progress_callback: Optional[Callable[[str], None]] = None):
        self.task_id = task_id
        self.bl_image = bl_image
        self.output_path = output_path
        self.texture_type = texture_type
        self.dds_format = dds_format
        self.progress_callback = progress_callback
        self.publication_gate = _PublicationGate()
        self.future = None
        self.status = "pending"  # pending, processing, completed, failed
        self.result = None
        self.error = None
        self.created_at = asyncio.get_event_loop().time()

class TextureProcessingQueue:
    """Manages a queue of texture processing tasks with parallel execution and batching."""
    
    def __init__(self, max_concurrent_tasks: int = None, batch_size: int = None):
        # Use CPU count for optimal parallel processing
        cpu_count = os.cpu_count() or 4
        self.max_concurrent_tasks = max_concurrent_tasks or min(cpu_count, 8)
        self.batch_size = batch_size or 4  # Textures per batch
        
        self.queue = asyncio.Queue()
        self.active_tasks = {}  # task_id -> TextureTask
        self.completed_tasks = {}  # task_id -> TextureTask
        
        # Use a semaphore that allows one texconv process per CPU core
        self.processing_semaphore = asyncio.Semaphore(self.max_concurrent_tasks)
        self.worker_tasks = []
        self.is_running = False
        self._task_counter = 0
        self._lock = asyncio.Lock()
        
        # Batching state
        self._pending_batch = []
        self._batch_timer = None
        self._batch_timeout = 0.5  # Seconds to wait before processing incomplete batch
    
    async def start(self):
        """Start the queue processing workers."""
        if self.is_running:
            return
        
        self.is_running = True
        # Create worker tasks - one per CPU core for optimal texconv utilization
        for i in range(self.max_concurrent_tasks):
            worker = asyncio.create_task(self._worker(f"worker-{i}"))
            self.worker_tasks.append(worker)
    
    async def stop(self):
        """Stop the queue processing workers."""
        self.is_running = False
        self.cancel_tasks(list(self.active_tasks), 'Queue stopped')
        
        # Cancel batch timer if running
        if self._batch_timer:
            self._batch_timer.cancel()
        
        # Process any remaining batch
        if self._pending_batch:
            await self._process_pending_batch()
        
        # Cancel all worker tasks
        for worker in self.worker_tasks:
            worker.cancel()
        
        # Wait for workers to finish
        if self.worker_tasks:
            await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        
        self.worker_tasks.clear()
        # Batches which were waiting for a worker never reached its finally
        # block. Move them out of the active set so waiters do not hang.
        for task_id, task in list(self.active_tasks.items()):
            task.status = 'cancelled'
            task.result = False
            task.error = 'Queue stopped before processing'
            self.completed_tasks[task_id] = task
            del self.active_tasks[task_id]
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
    
    def shutdown(self):
        """Synchronous shutdown for cleanup."""
        if self.is_running:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Schedule shutdown for later
                    asyncio.create_task(self.stop())
                else:
                    loop.run_until_complete(self.stop())
            except:
                pass  # Best effort cleanup
    
    async def add_task(self, bl_image: bpy.types.Image, output_path: str, 
                      texture_type: str = 'base color', dds_format: Optional[str] = None,
                      progress_callback: Optional[Callable[[str], None]] = None) -> str:
        """Add a texture processing task to the queue."""
        async with self._lock:
            self._task_counter += 1
            task_id = f"texture_task_{self._task_counter}"
        
        task = TextureTask(task_id, bl_image, output_path, texture_type, dds_format, progress_callback)
        
        # Ensure workers are running
        if not self.is_running:
            await self.start()
        
        # Add to pending batch instead of directly to queue
        await self._add_to_batch(task)
        
        if progress_callback:
            progress_callback(f"Task {task_id} queued for batch processing")
        
        return task_id
    
    async def _add_to_batch(self, task: TextureTask):
        """Add task to pending batch and process when batch is full."""
        self._pending_batch.append(task)
        self.active_tasks[task.task_id] = task
        
        # If batch is full, process it immediately
        if len(self._pending_batch) >= self.batch_size:
            await self._process_pending_batch()
        else:
            # Start/restart timer for incomplete batch
            if self._batch_timer:
                self._batch_timer.cancel()
            self._batch_timer = asyncio.create_task(self._batch_timeout_handler())
    
    async def _batch_timeout_handler(self):
        """Handle batch timeout - process incomplete batch after timeout."""
        try:
            await asyncio.sleep(self._batch_timeout)
            if self._pending_batch:
                await self._process_pending_batch()
        except asyncio.CancelledError:
            pass
    
    async def _process_pending_batch(self):
        """Process the current pending batch."""
        if not self._pending_batch:
            return
        
        # Create batch from pending tasks
        batch = self._pending_batch.copy()
        self._pending_batch.clear()
        
        # Cancel timer
        if self._batch_timer:
            self._batch_timer.cancel()
            self._batch_timer = None
        
        # Add batch to processing queue
        await self.queue.put(batch)
    
    async def add_batch(self, tasks: List[Tuple[bpy.types.Image, str, str, Optional[str]]],
                       progress_callback: Optional[Callable[[str], None]] = None) -> List[str]:
        """Add multiple texture processing tasks to the queue."""
        task_ids = []
        
        for bl_image, output_path, texture_type, dds_format in tasks:
            task_id = await self.add_task(bl_image, output_path, texture_type, dds_format, progress_callback)
            task_ids.append(task_id)
        
        # Force process any remaining batch
        if self._pending_batch:
            await self._process_pending_batch()
        
        if progress_callback:
            progress_callback(f"Added {len(task_ids)} tasks to processing queue")
        
        return task_ids
    
    async def wait_for_task(self, task_id: str, timeout: Optional[float] = None) -> bool:
        """Wait for a task and return True only when conversion succeeded."""
        start_time = asyncio.get_event_loop().time()
        
        while task_id in self.active_tasks:
            if timeout is not None and (asyncio.get_event_loop().time() - start_time) >= timeout:
                self.cancel_tasks([task_id], 'Texture conversion timed out')
                return False
            
            try:
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                self.cancel_tasks([task_id])
                raise
        
        task = self.completed_tasks.get(task_id)
        return bool(task and task.status == 'completed' and task.result is True)
    
    async def wait_for_all(self, task_ids: List[str], timeout: Optional[float] = None,
                          progress_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Return True only when every requested task completed successfully."""
        start_time = asyncio.get_event_loop().time()
        
        while True:
            remaining_tasks = [tid for tid in task_ids if tid in self.active_tasks]
            
            if not remaining_tasks:
                break
            
            if timeout is not None and (asyncio.get_event_loop().time() - start_time) >= timeout:
                self.cancel_tasks(remaining_tasks, 'Texture conversion timed out')
                if progress_callback:
                    progress_callback(f"Timeout waiting for {len(remaining_tasks)} tasks")
                return False
            
            if progress_callback:
                completed = len(task_ids) - len(remaining_tasks)
                progress_callback(f"Completed {completed}/{len(task_ids)} texture tasks")
            
            try:
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                self.cancel_tasks(task_ids)
                raise
        
        success = all(
            task_id in self.completed_tasks
            and self.completed_tasks[task_id].status == 'completed'
            and self.completed_tasks[task_id].result is True
            for task_id in task_ids
        )
        if progress_callback:
            if success:
                progress_callback(f"All {len(task_ids)} texture tasks completed successfully")
            else:
                progress_callback("One or more texture tasks failed, were cancelled, or were not found")
        return success

    def cancel_tasks(self, task_ids, reason='Texture conversion cancelled'):
        """Prevent later publication for the specified still-active tasks."""
        for task_id in task_ids:
            task = self.active_tasks.get(task_id)
            if task is not None:
                task.publication_gate.cancel()
                task.status = 'cancelled'
                task.result = False
                task.error = reason
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get the status of a specific task."""
        if task_id in self.active_tasks:
            task = self.active_tasks[task_id]
            return {
                "task_id": task_id,
                "status": task.status,
                "texture_name": task.bl_image.name,
                "output_path": task.output_path,
                "texture_type": task.texture_type,
                "error": task.error
            }
        elif task_id in self.completed_tasks:
            task = self.completed_tasks[task_id]
            return {
                "task_id": task_id,
                "status": task.status,
                "texture_name": task.bl_image.name,
                "output_path": task.output_path,
                "texture_type": task.texture_type,
                "result": task.result,
                "error": task.error
            }
        
        return None
    
    def get_queue_status(self) -> Dict[str, Any]:
        """Get overall queue status."""
        return {
            "is_running": self.is_running,
            "queue_size": self.queue.qsize(),
            "active_tasks": len(self.active_tasks),
            "completed_tasks": len(self.completed_tasks),
            "max_concurrent": self.max_concurrent_tasks,
            "worker_count": len(self.worker_tasks),
            "batch_size": self.batch_size,
            "pending_batch_size": len(self._pending_batch)
        }
    
    async def _worker(self, worker_name: str):
        """Worker coroutine that processes batches of tasks from the queue."""
        texture_processor = get_texture_processor()
        
        while self.is_running:
            try:
                # Get batch from queue with timeout
                batch = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                
                # Acquire semaphore for this texconv process
                async with self.processing_semaphore:
                    if not batch:
                        continue
                    
                    # Update all tasks in batch to processing status
                    for task in batch:
                        if not task.publication_gate.is_cancelled():
                            task.status = "processing"
                            self._report_task_progress(task, f"[{worker_name}] Processing batch with {task.bl_image.name}")
                    
                    try:
                        # Prepare batch data for texconv
                        processing = [task for task in batch if not task.publication_gate.is_cancelled()]
                        batch_data = []
                        for task in processing:
                            batch_data.append((task.bl_image, task.output_path, task.texture_type, task.dds_format))
                        
                        # Process entire batch with single texconv call
                        batch_results = await texture_processor.convert_texture_batch_async(
                            batch_data,
                            progress_callback=lambda msg: self._batch_progress_callback(processing, msg),
                            publication_gates=[task.publication_gate for task in processing],
                        ) if processing else []
                        
                        # Update individual task results
                        for i, task in enumerate(processing):
                            if task.publication_gate.is_cancelled():
                                task.status = 'cancelled'
                                task.result = False
                                continue
                            if i < len(batch_results):
                                task.result = batch_results[i]
                                task.status = "completed" if batch_results[i] else "failed"
                                if not batch_results[i]:
                                    task.error = "Batch conversion failed"
                            else:
                                task.result = False
                                task.status = "failed"
                                task.error = "Batch processing error"
                        
                    except asyncio.CancelledError:
                        for task in batch:
                            task.publication_gate.cancel()
                            task.status = 'cancelled'
                            task.result = False
                            task.error = 'Cancelled; publication blocked'
                        raise
                    except Exception as e:
                        # Mark all tasks in batch as failed
                        for task in batch:
                            task.status = "failed"
                            task.error = str(e)
                            self._report_task_progress(task, f"Error processing batch with {task.bl_image.name}: {e}")
                    
                    finally:
                        # Move all tasks from active to completed
                        for task in batch:
                            if task.task_id in self.active_tasks:
                                del self.active_tasks[task.task_id]
                            self.completed_tasks[task.task_id] = task
                            
                            status_msg = "completed successfully" if task.status == "completed" else f"failed: {task.error}"
                            self._report_task_progress(task, f"[{worker_name}] Task {task.task_id} {status_msg}")
                        
                        self.queue.task_done()
            
            except asyncio.TimeoutError:
                # No batches in queue, continue
                continue
            except asyncio.CancelledError:
                # Worker was cancelled
                break
            except Exception as e:
                print(f"Worker {worker_name} error: {e}")
                continue
    
    def _batch_progress_callback(self, batch: List[TextureTask], message: str):
        """Forward progress messages to all tasks in a batch."""
        for task in batch:
            if not task.publication_gate.is_cancelled():
                self._report_task_progress(task, message)

    @staticmethod
    def _report_task_progress(task: TextureTask, message: str):
        if task.progress_callback:
            try:
                task.progress_callback(message)
            except Exception as exc:
                print(f"Texture progress callback failed: {exc}")

# --- Async Texture Processing ---

class TextureProcessor:
    """Handles texture processing operations asynchronously."""

    def is_source_target(self, bl_image, output_path) -> bool:
        """Whether writing *output_path* would replace one of *bl_image*'s files.

        Two paths may look different yet name the same data on disk: hard
        links share the bytes under different names, and symlinks resolve
        elsewhere.  The check therefore compares resolved paths and falls
        back to ``os.path.samefile`` when both files exist (audit A02).
        """
        output_real = os.path.normcase(os.path.realpath(output_path))
        for raw in (getattr(bl_image, 'filepath', ''), bl_image.get('remix_normal_source_path', '')):
            if not raw:
                continue
            source_path = bpy.path.abspath(raw)
            if os.path.normcase(os.path.realpath(source_path)) == output_real:
                return True
            if os.path.exists(source_path) and os.path.exists(output_path):
                try:
                    if os.path.samefile(source_path, output_path):
                        return True
                except OSError:
                    pass
        return False

    def export_height_texture(self, bl_image, output_path, channel='Color'):
        """Export the height output as linear BC4, without modifying its image."""
        _require_blender_main_thread()
        import numpy as np

        # The alias guard runs before texconv availability so a bad target
        # fails loudly even when conversion itself is unavailable.
        if self.is_source_target(bl_image, output_path):
            raise ValueError('The height export would overwrite its source image')
        if not self.is_available():
            return False
        width, height = bl_image.size
        if width <= 0 or height <= 0:
            return False
        pixels = np.empty(width * height * 4, dtype=np.float32)
        bl_image.pixels.foreach_get(pixels)
        pixels = pixels.reshape(height, width, 4)
        if channel == 'Alpha':
            scalar = pixels[:, :, 3].copy()
        elif channel in ('R', 'G', 'B'):
            scalar = pixels[:, :, {'R': 0, 'G': 1, 'B': 2}[channel]].copy()
        elif channel == 'Color':
            scalar = pixels[:, :, 0] * 0.2126 + pixels[:, :, 1] * 0.7152 + pixels[:, :, 2] * 0.0722
        else:
            raise ValueError(f'Unsupported height channel: {channel}')
        pixels[:, :, 0] = scalar
        pixels[:, :, 1] = scalar
        pixels[:, :, 2] = scalar
        pixels[:, :, 3] = 1.0
        directory = os.path.dirname(output_path)
        os.makedirs(directory, exist_ok=True)
        temporary_image = None
        try:
            with tempfile.TemporaryDirectory(prefix='remix_height_') as temp_dir:
                png = os.path.join(temp_dir, 'height.png')
                temporary_image = bpy.data.images.new('Remix Height Temporary', width=width, height=height, alpha=True, float_buffer=True)
                temporary_image.colorspace_settings.name = 'Non-Color'
                temporary_image.pixels.foreach_set(pixels.ravel())
                temporary_image.filepath_raw = png
                temporary_image.file_format = 'PNG'
                temporary_image.save()
                result = subprocess.run([self.texconv_path, png, '-o', temp_dir, '-ft', 'dds', '-f', 'BC4_UNORM', '-m', '0', '-y', '-nologo'], capture_output=True, text=True, check=False, shell=False, timeout=120)
                dds = os.path.join(temp_dir, 'height.dds')
                if result.returncode != 0 or not os.path.isfile(dds):
                    print(f'Height DDS conversion failed: {result.stderr}')
                    return False
                import shutil
                with tempfile.NamedTemporaryFile(dir=directory, suffix='.dds', delete=False) as output_temp:
                    staged = output_temp.name
                try:
                    shutil.copyfile(dds, staged)
                    os.replace(staged, output_path)
                finally:
                    if os.path.exists(staged):
                        os.remove(staged)
                return True
        finally:
            if temporary_image is not None:
                bpy.data.images.remove(temporary_image)

    def texture_filename(self, bl_image):
        """Keep the image's name, replacing its extension with .dds."""
        # Use the source name only while the internal decoded image retains
        # its generated name. A user rename explicitly selects a new name.
        generated_name = bl_image.get('remix_normal_decoded_name')
        name = bl_image.name
        if generated_name and name == generated_name:
            name = bl_image.get('remix_normal_source_name', name)
        elif name == bl_image.get('remix_dds_fallback_name'):
            name = bl_image.get('remix_dds_original_name', name)
        name = os.path.basename(name)
        stem, ext = os.path.splitext(name)
        if ext.lower() not in {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.tga', '.exr', '.hdr', '.dds', '.webp'}:
            stem = name
        return f"{stem}.dds"

    def export_octahedral_normal(self, bl_image, output_path):
        """Convert an OpenGL Blender normal map on the main thread to BC5 DDS."""
        return self.export_normal_map(bl_image, output_path, 'OCT_CONVERT')

    def export_normal_map(self, bl_image, output_path, mode):
        """Export normals in the requested encoding without applying color transforms."""
        _require_blender_main_thread()
        import numpy as np
        from .nvidia_core.octahedral import convert_float_to_octahedral_in_place
        from .normal_export import MODES

        if mode not in MODES:
            raise ValueError(f"Unknown normal export encoding: {mode}")
        if self.is_source_target(bl_image, output_path):
            raise ValueError('The normal export would overwrite its source image')
        if not self.is_available():
            return False
        width, height = bl_image.size
        if width <= 0 or height <= 0:
            return False
        pixels = np.empty(width * height * 4, dtype=np.float32)
        bl_image.pixels.foreach_get(pixels)
        pixels = pixels.reshape(height, width, 4)
        decoded_opengl = bool(bl_image.get('remix_normal_decoded_opengl', False))
        if mode == 'OCT_CONVERT' or (mode == 'OCT_EXISTING' and decoded_opengl):
            inward = convert_float_to_octahedral_in_place(pixels, opengl=True)
            if inward:
                print(f"Warning: mirrored {inward} inward normals in {bl_image.name}")
        elif mode == 'DIRECTX' and decoded_opengl:
            pixels[:, :, 1] = 1.0 - pixels[:, :, 1]
        directory = os.path.dirname(output_path)
        os.makedirs(directory, exist_ok=True)
        temporary_image = None
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as temp_file:
                temp_path = temp_file.name
            temporary_image = bpy.data.images.new('Remix Octahedral Temporary', width=width, height=height, alpha=True, float_buffer=True)
            temporary_image.colorspace_settings.name = 'Non-Color'
            temporary_image.pixels.foreach_set(pixels.ravel())
            temporary_image.filepath_raw = temp_path
            temporary_image.file_format = 'PNG'
            temporary_image.save()
            result = subprocess.run([self.texconv_path, temp_path, '-o', directory, '-ft', 'dds', '-f', MODES[mode][1], '-m', '0', '-y', '-nologo'], capture_output=True, text=True, check=False, shell=False, timeout=120)
            converted = os.path.join(directory, os.path.splitext(os.path.basename(temp_path))[0] + '.dds')
            if result.returncode != 0 or not os.path.isfile(converted):
                print(f"Normal DDS conversion failed: {result.stderr}")
                return False
            os.replace(converted, output_path)
            return True
        finally:
            if temporary_image is not None:
                bpy.data.images.remove(temporary_image)
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
    
    def __init__(self, texconv_path: Optional[str] = None):
        self.texconv_path = texconv_path or self._find_texconv()
        self._format_map = {
            'BC1_UNORM': 'BC1_UNORM',
            'BC1_UNORM_SRGB': 'BC1_UNORM_SRGB',
            'BC3_UNORM': 'BC3_UNORM',
            'BC3_UNORM_SRGB': 'BC3_UNORM_SRGB',
            'BC4_UNORM': 'BC4_UNORM',
            'BC4_SNORM': 'BC4_SNORM',
            'BC5_UNORM': 'BC5_UNORM',
            'BC5_SNORM': 'BC5_SNORM',
            'BC7_UNORM': 'BC7_UNORM',
            'BC7_UNORM_SRGB': 'BC7_UNORM_SRGB',
        }
        
        # RTX Remix texture type suffixes
        self.texture_type_suffixes = {
            'base color': ".a.rtex",
            'albedo': ".a.rtex",
            'normal': ".n.rtex", 
            'roughness': ".r.rtex",
            'metallic': ".m.rtex",
            'emission': ".e.rtex",
            'emissive': ".e.rtex",
            'opacity': ".o.rtex",
            'specular': ".s.rtex"
        }
        
        # DDS format recommendations by texture type
        self.format_recommendations = {
            'base color': 'BC7_UNORM_SRGB',
            'albedo': 'BC7_UNORM_SRGB',
            'normal': 'BC5_UNORM',
            'roughness': 'BC4_UNORM',
            'height': 'BC4_UNORM',
            'metallic': 'BC4_UNORM',
            'emission': 'BC7_UNORM_SRGB',
            'emissive': 'BC7_UNORM_SRGB',
            'opacity': 'BC4_UNORM',
            'specular': 'BC4_UNORM'
        }
        
        # Batching configuration
        self.batch_size = 4  # Number of textures to process per texconv call
        self.max_concurrent_processes = min(os.cpu_count() or 4, 8)  # One per CPU core, max 8
    
    def _find_texconv(self) -> Optional[str]:
        """Find texconv.exe in the addon directory."""
        addon_dir = os.path.dirname(__file__)
        potential_path = os.path.join(addon_dir, "texconv", "texconv.exe")
        return potential_path if os.path.exists(potential_path) else None
    
    def is_available(self) -> bool:
        """Check if texture processing is available."""
        return self.texconv_path is not None
    
    def get_recommended_format(self, texture_type: str) -> str:
        """Get recommended DDS format for texture type."""
        return self.format_recommendations.get(texture_type.lower(), 'BC7_UNORM_SRGB')
    
    def get_texture_suffix(self, texture_type: str) -> str:
        """Get RTX Remix suffix for texture type."""
        return self.texture_type_suffixes.get(texture_type.lower(), "")
    
    # --- Batch Processing Methods ---
    
    async def convert_texture_batch_async(
        self,
        texture_batch: List[Tuple[bpy.types.Image, str, str, Optional[str]]],
        progress_callback: Optional[Callable[[str], None]] = None,
        publication_gates=None,
    ) -> List[bool]:
        """Convert a batch of textures using a single texconv process.

        Blender image access is a main-thread operation, so the batch is
        prepared (saved to temporary PNGs) on the thread that owns the images
        before any pool worker sees data; the worker only runs texconv on
        prepared files (audit A03).
        
        Args:
            texture_batch: List of (bl_image, output_path, texture_type, dds_format) tuples
            progress_callback: Optional progress callback function
            
        Returns:
            List of success status for each texture in the batch
        """
        _require_blender_main_thread()
        if not self.is_available() or not texture_batch:
            return [False] * len(texture_batch)
        
        results, groups = self._prepare_conversion_batch(texture_batch, progress_callback)
        if not groups:
            return results
        
        loop = asyncio.get_event_loop()
        gates = publication_gates or [_PublicationGate() for _ in texture_batch]
        worker_progress = _progress_on_loop(loop, progress_callback, gates)
        try:
            return await asyncio.shield(loop.run_in_executor(
                get_thread_pool(),
                lambda: self._run_texconv_batch(results, groups, worker_progress, gates)
            ))
        except asyncio.CancelledError:
            for gate in gates:
                gate.cancel()
            raise
    
    def _prepare_conversion_batch(
        self,
        texture_batch: List[Tuple[bpy.types.Image, str, str, Optional[str]]],
        progress_callback: Optional[Callable[[str], None]] = None
    ):
        """Save the Blender images of a texconv batch to temporary PNG files.

        Blender datablocks may only be touched from Blender's main thread
        (audit A03), so this runs on the thread that owns the images; the
        conversion step receives prepared files only.  Returns
        ``(results, groups)``: *results* is a ``[bool]`` list aligned with
        *texture_batch*, and *groups* maps ``(output_dir, texconv_format)`` to
        ``(batch_index, input_png, output_path)`` tuples.  Ownership of every
        prepared PNG passes to the run step, which removes them.
        """
        _require_blender_main_thread()
        if progress_callback:
            progress_callback(f"Processing batch of {len(texture_batch)} textures...")
        
        results = [False] * len(texture_batch)
        groups: Dict[Tuple[str, str], list] = {}
        existing_count = 0
        for i, (bl_image, output_path, texture_type, dds_format) in enumerate(texture_batch):
            if os.path.exists(output_path):
                if progress_callback:
                    progress_callback(f"Skipping existing texture: {os.path.basename(output_path)}")
                results[i] = True  # Existing output is reused as-is
                existing_count += 1
                continue
            
            if dds_format is None:
                dds_format = self.get_recommended_format(texture_type)
            output_dir = os.path.dirname(output_path)
            texconv_format = self._format_map.get(dds_format, 'BC7_UNORM_SRGB')
            
            temp_input_path = None
            try:
                temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                temp_input_path = temp_file.name
                temp_file.close()
                # Blender image access; must run on the owner/main thread.
                self._save_blender_image_to_file(bl_image, temp_input_path)
            except Exception as e:
                if temp_input_path and os.path.exists(temp_input_path):
                    try:
                        os.remove(temp_input_path)
                    except OSError:
                        pass
                if progress_callback:
                    progress_callback(f"Error saving {bl_image.name}: {e}")
                continue
            
            group_key = (output_dir, texconv_format)
            groups.setdefault(group_key, []).append((i, temp_input_path, output_path))
            
        if existing_count and existing_count == len(texture_batch):
            if progress_callback:
                progress_callback(f"All {len(texture_batch)} textures already exist - skipping batch")
        elif existing_count:
            if progress_callback:
                progress_callback(f"Processing {len(texture_batch) - existing_count} new textures (skipped {existing_count} existing)...")

        return results, groups

    def _discard_prepared_groups(self, groups) -> None:
        """Remove prepared input PNGs that no run step consumed."""
        for items in (groups or {}).values():
            for _, temp_input_path, _ in items:
                try:
                    if os.path.exists(temp_input_path):
                        os.remove(temp_input_path)
                except OSError:
                    pass

    def _run_texconv_batch(self, results, groups, progress_callback: Optional[Callable[[str], None]] = None, publication_gates=None) -> List[bool]:
        """Run one texconv invocation per group of prepared input files.

        Blender data is never touched here, so this may run in any thread
        (audit A03).  Each converted file lands at its destination via
        ``os.replace``, and every prepared PNG is removed in ``finally``.
        """
        prepared_pngs = [item[1] for items in groups.values() for item in items]
        temporary_outputs = []
        try:
            for (output_dir, texconv_format), group_items in groups.items():
                if publication_gates and all(publication_gates[i].is_cancelled() for i, _, _ in group_items):
                    continue
                if progress_callback:
                    progress_callback(f"Converting {len(group_items)} textures with format {texconv_format}...")
                
                group_temp_files = [item[1] for item in group_items]
                temp_to_final_mapping = {}
                for i, temp_input_path, output_path in group_items:
                    # Map temp file to final output
                    temp_base = os.path.splitext(os.path.basename(temp_input_path))[0]
                    temp_output_path = os.path.join(output_dir, f"{temp_base}.dds")
                    temp_to_final_mapping[temp_output_path] = (i, output_path)
                    if temp_output_path != output_path:
                        temporary_outputs.append(temp_output_path)
                
                try:
                    os.makedirs(output_dir, exist_ok=True)
                except OSError as e:
                    if progress_callback:
                        progress_callback(f"Error creating output directory {output_dir}: {e}")
                    continue
                
                # Run texconv on the entire batch
                cmd = [
                    self.texconv_path,
                    *group_temp_files,  # All input files
                    "-o", output_dir,
                    "-ft", "dds",
                    "-f", texconv_format,
                    "-m", "0",  # Generate all mipmaps
                    "-y",  # Overwrite existing
                    "-nologo",
                ]
                
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        check=False,
                        shell=False,
                        timeout=120  # 2 minute timeout for batch
                    )
                except subprocess.TimeoutExpired:
                    if progress_callback:
                        progress_callback("texconv batch timed out")
                    continue
                except Exception as e:
                    if progress_callback:
                        progress_callback(f"Error running texconv batch: {e}")
                    continue

                if result.returncode != 0:
                    if progress_callback:
                        progress_callback(f"texconv batch failed: {result.stderr}")
                    continue

                # Check which files were successfully converted and rename them
                for temp_output_path, (batch_index, final_output_path) in temp_to_final_mapping.items():
                    if os.path.exists(temp_output_path):
                        try:
                            gate = publication_gates[batch_index] if publication_gates else None
                            if gate is not None:
                                results[batch_index] = gate.publish(temp_output_path, final_output_path)
                            else:
                                if temp_output_path != final_output_path:
                                    os.replace(temp_output_path, final_output_path)
                                results[batch_index] = True
                            if progress_callback and results[batch_index]:
                                progress_callback(f"Converted: {os.path.basename(final_output_path)}")
                        except Exception as e:
                            if progress_callback:
                                progress_callback(f"Error moving {temp_output_path}: {e}")
                    else:
                        if progress_callback:
                            progress_callback(f"Missing output: {temp_output_path}")

            successful_count = sum(results)
            if progress_callback:
                progress_callback(f"Batch complete: {successful_count}/{len(results)} successful")
            return results
        finally:
            # Cleanup temporary files
            for temp_file in prepared_pngs:
                try:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                except OSError:
                    pass
            for temp_file in temporary_outputs:
                try:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                except OSError:
                    pass

    def _convert_texture_batch_sync(
        self,
        texture_batch: List[Tuple[bpy.types.Image, str, str, Optional[str]]],
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> List[bool]:
        """Convert a batch of textures on the calling thread.

        Blender image saving happens in ``_prepare_conversion_batch`` (main
        thread, audit A03); ``_run_texconv_batch`` only invokes texconv on the
        prepared files.
        """
        if not texture_batch:
            return []
        
        results, groups = self._prepare_conversion_batch(texture_batch, progress_callback)
        return self._run_texconv_batch(results, groups, progress_callback)
    
    # --- Queue Integration Methods ---
    
    async def queue_texture_conversion(
        self, 
        bl_image: bpy.types.Image, 
        output_path: str, 
        texture_type: str = 'base color',
        dds_format: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> str:
        """Queue a texture conversion task and return task ID."""
        queue = get_texture_queue()
        return await queue.add_task(bl_image, output_path, texture_type, dds_format, progress_callback)
    
    async def queue_batch_conversion(
        self, 
        textures: List[Tuple[bpy.types.Image, str, str, Optional[str]]],
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> List[str]:
        """Queue multiple texture conversion tasks and return list of task IDs.
        
        Args:
            textures: List of (bl_image, output_path, texture_type, dds_format) tuples
            progress_callback: Optional progress callback function
            
        Returns:
            List of task IDs for tracking
        """
        queue = get_texture_queue()
        return await queue.add_batch(textures, progress_callback)
    
    async def wait_for_conversions(
        self, 
        task_ids: List[str], 
        timeout: Optional[float] = None,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Wait for multiple texture conversion tasks to complete."""
        queue = get_texture_queue()
        return await queue.wait_for_all(task_ids, timeout, progress_callback)
    
    def get_conversion_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get the status of a texture conversion task."""
        queue = get_texture_queue()
        return queue.get_task_status(task_id)
    
    def get_queue_status(self) -> Dict[str, Any]:
        """Get overall texture processing queue status."""
        queue = get_texture_queue()
        return queue.get_queue_status()
    
    async def process_textures_parallel(
        self,
        textures: List[Tuple[bpy.types.Image, str, str, Optional[str]]],
        progress_callback: Optional[Callable[[str], None]] = None,
        timeout: Optional[float] = None
    ) -> Dict[str, bool]:
        """Process multiple textures in parallel using batching and return results.
        
        Args:
            textures: List of (bl_image, output_path, texture_type, dds_format) tuples
            progress_callback: Optional progress callback function
            timeout: Optional timeout in seconds
            
        Returns:
            Dictionary mapping task_id to success status
        """
        if not textures:
            return {}
        
        start_time = asyncio.get_event_loop().time()
        
        if progress_callback:
            progress_callback(f"Starting parallel processing of {len(textures)} textures with batching...")
            progress_callback(f"Using {self.max_concurrent_processes} concurrent texconv processes")
            progress_callback(f"Batch size: {self.batch_size} textures per process")
        
        # Queue all tasks - they will be automatically batched
        task_ids = await self.queue_batch_conversion(textures, progress_callback)
        
        # Wait for completion
        try:
            success = await self.wait_for_conversions(task_ids, timeout, progress_callback)
        except asyncio.CancelledError:
            get_texture_queue().cancel_tasks(task_ids)
            raise
        
        # Collect results
        results = {}
        queue = get_texture_queue()
        successful_count = 0
        
        for task_id in task_ids:
            task_status = queue.get_task_status(task_id)
            if task_status:
                task_success = task_status.get('status') == 'completed'
                results[task_id] = task_success
                if task_success:
                    successful_count += 1
            else:
                results[task_id] = False
        
        # Performance metrics
        end_time = asyncio.get_event_loop().time()
        total_time = end_time - start_time
        
        if progress_callback:
            progress_callback(f"Parallel processing completed in {total_time:.2f} seconds")
            progress_callback(f"Success rate: {successful_count}/{len(textures)} ({successful_count/len(textures)*100:.1f}%)")
            if total_time > 0:
                throughput = len(textures) / total_time
                progress_callback(f"Throughput: {throughput:.2f} textures/second")
        
        return results
    
    # --- Direct Conversion Methods ---
    
    async def convert_png_to_dds_async(
        self, 
        bl_image: bpy.types.Image, 
        output_path: str, 
        texture_type: str = 'base color',
        dds_format: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Convert a Blender image to DDS format asynchronously.

        The Blender image is saved to a temporary PNG on the thread that owns
        it before any pool worker runs texconv on the prepared file
        (audit A03).
        """
        _require_blender_main_thread()
        if not self.is_available():
            if progress_callback:
                progress_callback("texconv.exe not found")
            return False
        
        # Use recommended format if not specified
        if dds_format is None:
            dds_format = self.get_recommended_format(texture_type)
        
        try:
            temp_input_path = self._prepare_png_source(bl_image, progress_callback)
            loop = asyncio.get_event_loop()
            gate = _PublicationGate()
            worker_progress = _progress_on_loop(loop, progress_callback, [gate])
            try:
                return await asyncio.shield(loop.run_in_executor(
                    get_thread_pool(),
                    lambda: self._run_png_to_dds(temp_input_path, output_path, dds_format, worker_progress, gate)
                ))
            except asyncio.CancelledError:
                gate.cancel()
                raise
        except Exception as e:
            import traceback
            error_msg = f"Error: {e}\n{traceback.format_exc()}"
            print(f"[TextureProcessor] Conversion failed for {bl_image.name}: {error_msg}")
            if progress_callback:
                progress_callback(f"Error: {e}")
            return False
    
    def convert_png_to_dds_sync(
        self, 
        bl_image: bpy.types.Image, 
        output_path: str, 
        texture_type: str = 'base color',
        dds_format: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Convert a Blender image to DDS format synchronously."""
        if not self.is_available():
            if progress_callback:
                progress_callback("texconv.exe not found")
            return False
        
        # Use recommended format if not specified
        if dds_format is None:
            dds_format = self.get_recommended_format(texture_type)
        
        return self._convert_png_to_dds_sync(bl_image, output_path, dds_format, progress_callback)
    
    def _prepare_png_source(
        self,
        bl_image: bpy.types.Image,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> str:
        """Save a Blender image to a temporary PNG for a later texconv run.

        Blender datablocks may only be touched from Blender's main thread
        (audit A03); the conversion step receives the prepared file only.
        Raises on failure — callers log it through *progress_callback*.
        """
        _require_blender_main_thread()
        if progress_callback:
            progress_callback(f"Converting {bl_image.name} to DDS...")

        # Create temporary PNG file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            temp_input_path = temp_file.name

        # Save Blender image to temporary file
        self._save_blender_image_to_file(bl_image, temp_input_path)
        return temp_input_path

    def _run_png_to_dds(
        self,
        temp_input_path: str,
        output_path: str,
        dds_format: str,
        progress_callback: Optional[Callable[[str], None]] = None,
        publication_gate=None,
    ) -> bool:
        """Convert a prepared PNG into a DDS at *output_path* via texconv.

        No Blender data access here, so this may run in any thread (audit
        A03).  The prepared input file is removed in ``finally``.
        """
        temp_dds_path = None
        try:
            if publication_gate and publication_gate.is_cancelled():
                return False
            # Create output directory
            output_dir = os.path.dirname(output_path)
            os.makedirs(output_dir, exist_ok=True)
            
            if progress_callback:
                progress_callback("Running texconv...")
            
            # Convert using texconv
            texconv_format = self._format_map.get(dds_format, 'BC7_UNORM_SRGB')
            cmd = [
                self.texconv_path,
                temp_input_path,
                "-o", output_dir,
                "-ft", "dds",
                "-f", texconv_format,
                "-m", "0",  # Generate all mipmaps
                "-y",  # Overwrite existing
                "-nologo",
            ]

            # Add -sepalpha for formats that support alpha channels
            # This is critical for BC7, BC3, BC2 to preserve alpha during mipmap generation
            formats_with_alpha = ['BC7_UNORM', 'BC7_UNORM_SRGB', 'BC3_UNORM', 'BC3_UNORM_SRGB',
                                 'BC2_UNORM', 'BC2_UNORM_SRGB', 'BC1_UNORM', 'BC1_UNORM_SRGB']
            if texconv_format in formats_with_alpha:
                cmd.append("-sepalpha")  # Resize/generate mips alpha channel separately from color channels
                if progress_callback:
                    progress_callback(f"Using separate alpha processing for {texconv_format}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=60  # 60 second timeout
            )

            if result.returncode != 0:
                if progress_callback:
                    progress_callback(f"texconv failed: {result.stderr}")
                return False

            # Handle texconv output file naming
            temp_output_base = os.path.splitext(os.path.basename(temp_input_path))[0]
            temp_dds_filename = f"{temp_output_base}.dds"
            temp_dds_path = os.path.join(output_dir, temp_dds_filename)

            if not os.path.exists(temp_dds_path):
                if progress_callback:
                    progress_callback("texconv output file not found")
                return False

            # Rename to final output path
            if publication_gate:
                if not publication_gate.publish(temp_dds_path, output_path):
                    return False
            elif temp_dds_path != output_path:
                os.replace(temp_dds_path, output_path)

            if progress_callback:
                progress_callback("Conversion complete")

            return True

        finally:
            if temp_dds_path and temp_dds_path != output_path and os.path.exists(temp_dds_path):
                try:
                    os.remove(temp_dds_path)
                except OSError:
                    pass
            # Cleanup temporary file
            if os.path.exists(temp_input_path):
                try:
                    os.remove(temp_input_path)
                except OSError:
                    pass

    def _convert_png_to_dds_sync(
        self,
        bl_image: bpy.types.Image,
        output_path: str,
        dds_format: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Internal PNG to DDS conversion implementation."""
        try:
            temp_input_path = self._prepare_png_source(bl_image, progress_callback)
            return self._run_png_to_dds(temp_input_path, output_path, dds_format, progress_callback)
        except Exception as e:
            import traceback
            error_msg = f"Error: {e}\n{traceback.format_exc()}"
            print(f"[TextureProcessor] Conversion failed for {bl_image.name}: {error_msg}")
            if progress_callback:
                progress_callback(f"Error: {e}")
            return False
    
    def _save_blender_image_to_file(self, bl_image: bpy.types.Image, filepath: str):
        """Save a Blender image to a file with alpha channel preserved."""
        _require_blender_main_thread()
        # Store original file format settings
        orig_file_format = bl_image.file_format
        
        try:
            # Set PNG format for export
            bl_image.file_format = 'PNG'
            
            # Use scene render settings to ensure RGBA with 16-bit depth
            scene = bpy.context.scene
            orig_color_mode = scene.render.image_settings.color_mode
            orig_color_depth = scene.render.image_settings.color_depth
            orig_compression = scene.render.image_settings.compression
            
            # Set RGBA mode with 16-bit depth to preserve alpha precision
            scene.render.image_settings.color_mode = 'RGBA'
            scene.render.image_settings.color_depth = '16'
            scene.render.image_settings.compression = 15  # No compression for speed
            
            try:
                # Save with render settings applied
                bl_image.save_render(filepath, scene=scene)
            finally:
                # Restore scene settings
                scene.render.image_settings.color_mode = orig_color_mode
                scene.render.image_settings.color_depth = orig_color_depth
                scene.render.image_settings.compression = orig_compression
            
        except Exception as e:
            # Fallback to simple save if save_render fails
            print(f"Warning: save_render failed ({e}), using fallback save method")
            bl_image.file_format = 'PNG'
            bl_image.save(filepath=filepath)
            
        finally:
            # Restore original file format
            bl_image.file_format = orig_file_format

    # --- DDS -> PNG Conversion Methods ---
    # These were dropped during the PNG->DDS refactor above but are still
    # used by the "Fix Broken Textures" / "Convert DDS to PNG" operators.

    def convert_dds_to_png_sync(
        self,
        dds_path: str,
        output_path: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Convert a single DDS file to PNG format synchronously."""
        if not self.is_available():
            if progress_callback:
                progress_callback("texconv.exe not found")
            return False

        try:
            if progress_callback:
                progress_callback(f"Converting {os.path.basename(dds_path)} to PNG...")

            output_dir = os.path.dirname(output_path)
            os.makedirs(output_dir, exist_ok=True)

            cmd = [
                self.texconv_path,
                dds_path,
                "-o", output_dir,
                "-ft", "png",
                "-y",  # Overwrite existing
                "-nologo",
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=30
            )

            if result.returncode != 0:
                if progress_callback:
                    progress_callback(f"texconv failed: {result.stderr}")
                return False

            # texconv names the output after the input file's base name
            original_name = os.path.splitext(os.path.basename(dds_path))[0]
            texconv_output = os.path.join(output_dir, f"{original_name}.png")

            if os.path.exists(texconv_output):
                if texconv_output != output_path:
                    os.replace(texconv_output, output_path)
                if progress_callback:
                    progress_callback("Conversion complete")
                return True
            else:
                if progress_callback:
                    progress_callback("texconv output file not found")
                return False

        except subprocess.TimeoutExpired:
            if progress_callback:
                progress_callback("texconv timed out")
            return False
        except Exception as e:
            if progress_callback:
                progress_callback(f"Error: {e}")
            return False

    async def convert_dds_to_png_async(
        self,
        dds_path: str,
        output_path: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Convert DDS file to PNG format asynchronously."""
        if not self.is_available():
            if progress_callback:
                progress_callback("texconv.exe not found")
            return False

        loop = asyncio.get_event_loop()
        gate = _PublicationGate()
        worker_progress = _progress_on_loop(loop, progress_callback, [gate])

        def _convert():
            if gate.is_cancelled():
                return False
            output_dir = os.path.dirname(os.path.abspath(output_path))
            os.makedirs(output_dir, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='remix_dds_', dir=output_dir) as temp_dir:
                staged = os.path.join(temp_dir, os.path.basename(output_path))
                if not self.convert_dds_to_png_sync(dds_path, staged, worker_progress):
                    return False
                return gate.publish(staged, output_path)

        try:
            return await asyncio.shield(loop.run_in_executor(get_thread_pool(), _convert))
        except asyncio.CancelledError:
            gate.cancel()
            raise

    def batch_convert_dds_to_png(
        self,
        dds_files: List[str],
        output_dir: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> List[str]:
        """Batch convert multiple DDS files to PNG. Returns list of output PNG paths."""
        if not self.is_available():
            return []

        converted_files = []
        total_files = len(dds_files)

        for i, dds_file in enumerate(dds_files):
            if progress_callback:
                progress_callback(i, total_files, f"Converting {os.path.basename(dds_file)}")

            base_name = os.path.splitext(os.path.basename(dds_file))[0]
            output_path = os.path.join(output_dir, f"{base_name}.png")

            if self.convert_dds_to_png_sync(dds_file, output_path):
                converted_files.append(output_path)

        if progress_callback:
            progress_callback(total_files, total_files, "Batch conversion complete")

        return converted_files

    # Legacy method names for backward compatibility
    async def convert_texture_async(self, bl_image, output_path, dds_format='BC7_UNORM_SRGB', progress_callback=None):
        """Legacy method - use convert_png_to_dds_async instead."""
        return await self.convert_png_to_dds_async(bl_image, output_path, 'base color', dds_format, progress_callback)

# Global texture processor instance
_texture_processor = None

def get_texture_processor() -> TextureProcessor:
    """Get the global texture processor."""
    global _texture_processor
    if _texture_processor is None:
        _texture_processor = TextureProcessor()
    return _texture_processor

# --- Progress Tracking ---

class ProgressTracker:
    """Tracks progress for long-running operations."""
    
    def __init__(self, total_steps: int, callback: Optional[Callable[[float, str], None]] = None):
        self.total_steps = total_steps
        self.current_step = 0
        self.callback = callback
        self._cancelled = False
    
    def update(self, message: str = ""):
        """Update progress."""
        if self._cancelled:
            return False
        
        progress = self.current_step / self.total_steps if self.total_steps > 0 else 0.0
        if self.callback:
            self.callback(progress, message)
        return True
    
    def step(self, message: str = ""):
        """Advance to next step."""
        self.current_step += 1
        return self.update(message)
    
    def cancel(self):
        """Cancel the operation."""
        self._cancelled = True
    
    def is_cancelled(self) -> bool:
        """Check if operation was cancelled."""
        return self._cancelled

# --- Cleanup Functions ---

# Global background processing state
_background_processor = None

class BackgroundTextureProcessor:
    """Handles texture processing in the background without blocking Blender's main thread."""
    
    def __init__(self):
        self.active_jobs = {}  # job_id -> job_info
        self.completed_jobs = {}  # job_id -> results
        self.job_counter = 0
        self.timer_registered = False
        
    def start_background_job(self, textures: List[Tuple], progress_callback=None, completion_callback=None):
        """Start a background texture processing job.

        Blender images may only be touched from the thread that calls this
        (the main thread), so every image is saved to a temporary PNG right
        here, before the worker starts; the worker only receives prepared
        files (audit A03).
        """
        _require_blender_main_thread()
        self.job_counter += 1
        job_id = f"texture_job_{self.job_counter}"

        job_info = {
            'id': job_id,
            'progress_callback': progress_callback,
            'completion_callback': completion_callback,
            'status': 'queued',
            'progress': 0,
            'total': len(textures),
            'results': [],
            'prepared_results': [],
            'prepared_groups': {},
            'publication_gate': _PublicationGate(),
            'thread': None,
            'start_time': time.time()
        }

        self.active_jobs[job_id] = job_info

        texture_processor = get_texture_processor()
        if not texture_processor.is_available():
            job_info['status'] = 'failed'
            job_info['error'] = 'texconv.exe not available'
        else:
            results, groups = texture_processor._prepare_conversion_batch(
                textures,
                progress_callback=lambda msg: self._update_progress(job_id, msg)
            )
            job_info['prepared_results'] = results
            job_info['prepared_groups'] = groups
            job_info['progress'] = sum(1 for ok in results if ok)

        if not self.timer_registered:
            bpy.app.timers.register(self._timer_callback, first_interval=0.1, persistent=True)
            self.timer_registered = True

        if job_info['status'] != 'failed':
            # Start the processing thread
            import threading
            thread = threading.Thread(target=self._process_job_thread, args=(job_id,))
            thread.daemon = True
            job_info['thread'] = thread
            thread.start()

        print(f"Started background texture job: {job_id} with {len(textures)} textures")
        return job_id

    def _process_job_thread(self, job_id):
        """Run texconv over prepared files in a background thread.

        Blender data access is forbidden here (audit A03): the job only
        receives the file groups prepared in ``start_background_job``.
        """
        job_info = self.active_jobs.get(job_id)
        if not job_info:
            return

        try:
            job_info['status'] = 'processing'
            texture_processor = get_texture_processor()

            results = job_info['prepared_results']
            groups = job_info['prepared_groups']

            for group_key in list(groups.keys()):
                # Check if job was cancelled
                if job_info.get('cancelled', False):
                    texture_processor._discard_prepared_groups(groups)
                    groups.clear()
                    job_info['status'] = 'cancelled'
                    return

                group_items = groups.pop(group_key)
                texture_processor._run_texconv_batch(
                    results,
                    {group_key: group_items},
                    progress_callback=lambda msg: self._update_progress(job_id, msg),
                    publication_gates=[job_info['publication_gate']] * len(results),
                )
                job_info['progress'] = sum(1 for ok in results if ok)

                # Small delay to prevent overwhelming the system
                time.sleep(0.1)

            groups.clear()
            job_info['results'] = results

            if job_info.get('cancelled', False):
                job_info['status'] = 'cancelled'
            elif all(results):
                job_info['status'] = 'completed'
            else:
                # A texture error must never read as success (audit A04).
                failed = sum(1 for ok in results if not ok)
                job_info['status'] = 'failed'
                job_info['error'] = f'{failed} of {len(results)} texture conversions failed'

        except Exception as e:
            # Discard prepared files that no run step consumed.
            get_texture_processor()._discard_prepared_groups(job_info.get('prepared_groups') or {})
            job_info['prepared_groups'] = {}
            job_info['results'] = list(job_info['prepared_results'])
            job_info['status'] = 'failed'
            job_info['error'] = str(e)
            print(f"Background job {job_id} failed: {e}")
    
    def _update_progress(self, job_id, message):
        """Update progress from background thread."""
        job_info = self.active_jobs.get(job_id)
        if job_info and job_info.get('progress_callback'):
            # Store message for main thread to pick up
            job_info['last_message'] = message
    
    def _timer_callback(self):
        """Timer callback to check job status and update UI."""
        completed_jobs = []
        
        for job_id, job_info in self.active_jobs.items():
            # Update progress callback if available
            if 'last_message' in job_info and job_info.get('progress_callback'):
                try:
                    job_info['progress_callback'](job_info['last_message'])
                    del job_info['last_message']
                except:
                    pass  # Ignore callback errors
            
            # Check if job is completed
            if job_info['status'] in ['completed', 'failed', 'cancelled']:
                completed_jobs.append(job_id)
                
                # Call completion callback
                if job_info.get('completion_callback'):
                    try:
                        job_info['completion_callback'](job_id, job_info)
                    except Exception as e:
                        print(f"Error in completion callback for {job_id}: {e}")
        
        # Move completed jobs
        for job_id in completed_jobs:
            self.completed_jobs[job_id] = self.active_jobs.pop(job_id)
        
        # Continue timer if there are active jobs
        if self.active_jobs:
            return 0.1  # Check again in 0.1 seconds
        else:
            self.timer_registered = False
            return None  # Stop timer
    
    def get_job_status(self, job_id):
        """Get status of a background job."""
        if job_id in self.active_jobs:
            job_info = self.active_jobs[job_id]
            return {
                'status': job_info['status'],
                'progress': job_info['progress'],
                'total': job_info['total'],
                'elapsed': time.time() - job_info['start_time'],
                'error': job_info.get('error')
            }
        elif job_id in self.completed_jobs:
            job_info = self.completed_jobs[job_id]
            return {
                'status': job_info['status'],
                'progress': job_info['progress'],
                'total': job_info['total'],
                'elapsed': time.time() - job_info['start_time'],
                'results': job_info.get('results', []),
                'error': job_info.get('error')
            }
        else:
            return None
    
    def cancel_job(self, job_id):
        """Cancel a background job."""
        if job_id in self.active_jobs:
            job = self.active_jobs[job_id]
            job['publication_gate'].cancel()
            job['cancelled'] = True
            return True
        return False
    
    def shutdown(self):
        """Detach UI callbacks and cancel jobs without waiting for texconv."""
        for job_id, info in list(self.active_jobs.items()):
            self.cancel_job(job_id)
            info['progress_callback'] = None
            info['completion_callback'] = None
            info.pop('last_message', None)
        if bpy.app.timers.is_registered(self._timer_callback):
            bpy.app.timers.unregister(self._timer_callback)
        self.timer_registered = False
        self.completed_jobs.clear()

    def cleanup_completed_jobs(self):
        """Clean up old completed jobs."""
        self.completed_jobs.clear()

def get_background_processor():
    """Get or create the global background processor."""
    global _background_processor
    if _background_processor is None:
        _background_processor = BackgroundTextureProcessor()
    return _background_processor 

def get_thumbnail_preview(capture_full_path):
    """Find the original DDS thumbnail using the Toolkit's naming convention."""
    if not capture_full_path:
        return None
    capture_dir = os.path.dirname(capture_full_path)
    filename = os.path.basename(capture_full_path)
    stem = os.path.splitext(filename)[0]
    for folder in ('.thumbs', 'thumbs'):
        for name in (filename + '.dds', stem + '.dds'):
            path = os.path.join(capture_dir, folder, name)
            if os.path.isfile(path):
                return path
    return None

def cleanup_addon_resources():
    """Release callbacks and caches when disabling or opening another file."""
    global _background_processor
    cancel_all_publications()
    if _background_processor is not None:
        _background_processor.shutdown()
        _background_processor = None
    cleanup_thread_pool()
    if _stage_manager is not None:
        _stage_manager.cleanup_all()
    from .material_utils import clear_material_cache
    from .mod_apply_utils import clear_mod_apply_caches
    clear_material_cache()
    clear_mod_apply_caches()


# Blender version compatibility functions
def get_blender_version():
    """Get Blender version as tuple (major, minor, patch)"""
    return bpy.app.version

def is_blender_4_1_or_newer():
    """Check if running Blender 4.1 or newer"""
    version = get_blender_version()
    return version >= (4, 1, 0)

def set_mesh_auto_smooth_compatible(mesh, enable=True):
    """Set auto smooth on mesh in a version-compatible way"""
    if hasattr(mesh, 'use_auto_smooth'):
        # Blender 4.0 and earlier
        mesh.use_auto_smooth = enable
    else:
        # Blender 4.1+ - auto smooth is always enabled, no need to set
        pass

def calc_normals_split_compatible(mesh):
    """Calculate split normals in a version-compatible way"""
    if hasattr(mesh, 'calc_normals_split'):
        # Blender 4.0 and earlier
        mesh.calc_normals_split()
    elif hasattr(mesh, 'corner_normals'):
        # Blender 4.1+ computes corner normals lazily; touching `corner_normals`
        # is enough, `calc_loop_triangles()` does not produce them.
        len(mesh.corner_normals)
    else:
        mesh.calc_loop_triangles()

def get_corner_normals_compatible(mesh):
    """Return per-loop (corner) normals for any supported Blender version.

    Blender 4.1 removed `Mesh.calc_normals_split()`; corner normals moved to
    `Mesh.corner_normals`. Returns an empty list when nothing can be read so the
    caller can skip writing a normals attribute.
    """
    try:
        corner_normals = getattr(mesh, 'corner_normals', None)
        if corner_normals is not None and len(corner_normals) == len(mesh.loops):
            return [tuple(cn.vector) for cn in corner_normals]
    except Exception as e:
        print(f"  Warning: could not read corner_normals: {e}")

    try:
        if hasattr(mesh, 'calc_normals_split'):
            mesh.calc_normals_split()
    except Exception as e:
        print(f"  Warning: could not calculate split normals: {e}")

    try:
        return [tuple(loop.normal) for loop in mesh.loops]
    except Exception as e:
        print(f"  Warning: could not read loop normals: {e}")
        return []

def set_custom_normals_compatible(mesh, normals_data=None, from_vertices=False):
    """Set custom normals in a version-compatible way"""
    try:
        if hasattr(mesh, 'normals_split_custom_set_from_vertices') and hasattr(mesh, 'normals_split_custom_set'):
            # Blender 4.0 and earlier
            if from_vertices and normals_data:
                mesh.normals_split_custom_set_from_vertices(normals_data)
            elif normals_data:
                mesh.normals_split_custom_set(normals_data)
            else:
                mesh.calc_normals_split()
        else:
            # Blender 4.1+ - In newer versions, custom normals are handled differently
            # For now, we'll just ensure the mesh has proper geometry and let Blender calculate normals
            if normals_data:
                # Ensure we have the required loop data
                mesh.calc_loop_triangles()
                # Note: In Blender 4.1+, custom normals are typically handled through
                # mesh modifiers or different APIs. For basic compatibility, we'll
                # let Blender calculate the normals automatically.
                print("  Note: Custom normals in Blender 4.1+ - using auto-calculated normals")
            else:
                # Just ensure proper loop data
                mesh.calc_loop_triangles()
    except Exception as e:
        print(f"  Warning: Error setting custom normals: {e}")
        # Fallback to calculated normals
        calc_normals_split_compatible(mesh)

def to_mesh_clear_compatible(obj):
    """Clear temporary mesh data in a version-compatible way"""
    if hasattr(obj, 'to_mesh_clear'):
        # Blender 4.0 and earlier
        obj.to_mesh_clear()
    else:
        # Blender 4.1+ - to_mesh_clear was removed
        # The temporary mesh is automatically cleaned up by garbage collection
        pass

def set_material_blend_method_compatible(material, blend_method='OPAQUE', shadow_method='OPAQUE', alpha_threshold=0.5):
    """Set material blend and shadow methods in a version-compatible way"""
    # blend_method is still available in Blender 4.4
    if hasattr(material, 'blend_method'):
        material.blend_method = blend_method
    
    # shadow_method was deprecated in Blender 4.1+
    if hasattr(material, 'shadow_method'):
        # Blender 4.0 and earlier
        material.shadow_method = shadow_method
    else:
        # Blender 4.1+ - shadow_method was removed
        # Shadow behavior is now handled automatically based on blend_method
        pass
    
    # alpha_threshold is still available
    if hasattr(material, 'alpha_threshold') and blend_method == 'CLIP':
        material.alpha_threshold = alpha_threshold
