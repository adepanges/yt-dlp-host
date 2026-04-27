import os
import base64
import json
import time
import shutil
import threading
import traceback
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any

import yt_dlp
from yt_dlp.utils import download_range_func

from src.storage import Storage
from src.auth import memory_manager
from src.models import TaskStatus, TaskType
from config import storage, memory
from config import task as task_config

class YTDownloader:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=task_config.MAX_WORKERS)
        self._ensure_download_dir()
        self._write_cookies_file()

    def _ensure_download_dir(self):
        os.makedirs(storage.DOWNLOAD_DIR, exist_ok=True)

    def _write_cookies_file(self):
        content = ''
        if storage.COOKIES_B64:
            content = base64.b64decode(storage.COOKIES_B64).decode('utf-8')
        elif storage.COOKIES_CONTENT:
            content = storage.COOKIES_CONTENT
        if content:
            os.makedirs(os.path.dirname(storage.COOKIES_FILE), exist_ok=True)
            with open(storage.COOKIES_FILE, 'w') as f:
                f.write(content)
            os.chmod(storage.COOKIES_FILE, 0o600)

    def _cookies_opts(self) -> dict:
        if os.path.exists(storage.COOKIES_FILE):
            return {'cookiefile': storage.COOKIES_FILE}
        return {}
    
    def _get_task_dir(self, task_id: str) -> str:
        return os.path.join(storage.DOWNLOAD_DIR, task_id)
    
    def _update_task(self, task_id: str, **kwargs):
        tasks = Storage.load_tasks()
        if task_id in tasks:
            tasks[task_id].update(kwargs)
            Storage.save_tasks(tasks)
    
    def _handle_error(self, task_id: str, error: Exception):
        tb = traceback.format_exc()
        self._update_task(
            task_id,
            status=TaskStatus.ERROR.value,
            error=str(error),
            traceback=tb,
            completed_time=datetime.now().isoformat()
        )
        print(f"[task {task_id}] ERROR: {error}\n{tb}", flush=True)
    
    def estimate_size(self, url: str, video_format: Optional[str] = None, 
                      audio_format: Optional[str] = None) -> int:
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'skip_download': True,
                'extractor_args': { 'youtube': { 'player_client': ['default', '-tv_simply'], }, },
                **self._cookies_opts(),
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                total_size = 0
                formats = info.get('formats', [])
                
                if video_format:
                    video_size = self._get_format_size(formats, video_format, is_video=True)
                    if not video_size:
                        video_size = self._get_format_size(formats, 'bestvideo', is_video=True)
                    total_size += video_size
                
                if audio_format and str(audio_format).lower() not in ['none', 'null']:
                    audio_size = self._get_format_size(formats, audio_format, is_video=False)
                    if not audio_size:
                        audio_size = self._get_format_size(formats, 'bestaudio', is_video=False)
                    total_size += audio_size
                
                if total_size > 0:
                    return int(total_size * memory.SIZE_BUFFER)

                # Fallback: estimate from duration + first available bitrate
                duration = info.get('duration', 0) or 0
                tbr = max((f.get('tbr') or 0 for f in formats), default=0)
                if duration > 0 and tbr > 0:
                    return int(tbr * duration * 128 * memory.SIZE_BUFFER)

                # Last resort: assume 50 MB so quota check doesn't block download
                print(f"Could not derive size from metadata for {url}; using 50MB fallback", flush=True)
                return 50 * 1024 * 1024
        except Exception as e:
            print(f"Error in estimate_size: {e}\n{traceback.format_exc()}", flush=True)
            return -1
    
    def _get_format_size(self, formats: list, format_spec: str, is_video: bool) -> int:
        if format_spec in ('bestvideo', 'bv', 'bv*'):
            filtered = [f for f in formats if f.get('vcodec') and f.get('vcodec') != 'none']
        elif format_spec in ('bestaudio', 'ba'):
            filtered = [f for f in formats if f.get('acodec') and f.get('acodec') != 'none']
        else:
            filtered = [f for f in formats if f.get('format_id') == format_spec]

        if not filtered:
            if is_video:
                filtered = [f for f in formats if f.get('vcodec') and f.get('vcodec') != 'none']
            else:
                filtered = [f for f in formats if f.get('acodec') and f.get('acodec') != 'none']
        
        if not filtered:
            return 0
        
        best = max(filtered, key=lambda f: (f.get('filesize') or f.get('filesize_approx') or 0, f.get('tbr') or 0, f.get('height') or 0 if is_video else f.get('abr') or 0))
        
        size = best.get('filesize') or best.get('filesize_approx') or 0

        if not size:
            duration = best.get('duration') or 0
            if is_video:
                bitrate = best.get('tbr') or best.get('vbr') or 0
            else:
                bitrate = best.get('abr') or best.get('tbr') or 0

            if duration > 0 and bitrate > 0:
                size = int(bitrate * duration * 128)

        return int(size or 0)
    
    def download_info(self, task_id: str):
        try:
            tasks = Storage.load_tasks()
            task = tasks[task_id]
            self._update_task(task_id, status=TaskStatus.PROCESSING.value)
            
            download_path = self._get_task_dir(task_id)
            os.makedirs(download_path, exist_ok=True)
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'skip_download': True,
                **self._cookies_opts(),
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(task['url'], download=False)
            
            info_file = os.path.join(download_path, 'info.json')
            with open(info_file, 'w') as f:
                json.dump(info, f)
            
            self._update_task(
                task_id,
                status=TaskStatus.COMPLETED.value,
                completed_time=datetime.now().isoformat(),
                file=f'/files/{task_id}/info.json'
            )
        except Exception as e:
            self._handle_error(task_id, e)
    
    def download_media(self, task_id: str):
        try:
            tasks = Storage.load_tasks()
            task = tasks[task_id]
            self._update_task(task_id, status=TaskStatus.PROCESSING.value)
            
            # Check memory quota
            is_video = task['task_type'] in ['get_video', 'get_live_video']
            total_size = self.estimate_size(
                task['url'],
                task.get('video_format') if is_video else None,
                task.get('audio_format')
            )
            
            if total_size <= 0:
                print(f"[task {task_id}] size estimation failed; proceeding without quota check", flush=True)
                total_size = 50 * 1024 * 1024

            keys = Storage.load_keys()
            api_key = keys[task['key_name']]['key']
            memory_manager.check_and_update_quota(api_key, total_size, task_id)
            
            # Prepare download
            download_path = self._get_task_dir(task_id)
            os.makedirs(download_path, exist_ok=True)
            
            # Configure yt-dlp
            ydl_opts = self._build_ydl_options(task, download_path)
            
            # Download
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([task['url']])
            
            # Update task
            files = os.listdir(download_path)
            if files:
                self._update_task(
                    task_id,
                    status=TaskStatus.COMPLETED.value,
                    completed_time=datetime.now().isoformat(),
                    file=f'/files/{task_id}/{files[0]}'
                )
        except Exception as e:
            self._handle_error(task_id, e)
    
    def _build_ydl_options(self, task: dict, download_path: str) -> dict:
        is_video = task['task_type'] in ['get_video', 'get_live_video']
        is_live = 'live' in task['task_type']
        output_format = task.get('output_format')
        audio_format = task.get('audio_format')

        if is_video:
            video_format = task.get('video_format') or 'bv*'
            if audio_format is None or str(audio_format).lower() in ['none', 'null']:
                format_option = f"{video_format}/b"
            else:
                format_option = f"{video_format}+{audio_format}/b"
            output_name = 'live_video.%(ext)s' if is_live else 'video.%(ext)s'
        else:
            format_option = f"{task.get('audio_format') or 'ba'}/b"
            output_name = 'live_audio.%(ext)s' if is_live else 'audio.%(ext)s'

        opts = {
            'format': format_option,
            'outtmpl': os.path.join(download_path, output_name),
            'extractor_args': { 'youtube': { 'player_client': ['default', '-tv_simply'], }, },
            **self._cookies_opts(),
        }
        
        if output_format:
            if not is_video:
                opts['extract_audio'] = True 
                opts['postprocessors'] = [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': output_format,
                }]
            else:
                opts['merge_output_format'] = output_format
        
        # Handle time ranges
        if is_live and task.get('duration'):
            current = int(time.time())
            start_time = current - task.get('start', 0)
            end_time = start_time + task['duration']
            opts['download_ranges'] = lambda *_: [{'start_time': start_time, 'end_time': end_time}]
        
        elif task.get('start_time') or task.get('end_time'):
            start = self._time_to_seconds(task.get('start_time', '00:00:00'))
            end = self._time_to_seconds(task.get('end_time', '10:00:00'))
            opts['download_ranges'] = download_range_func(None, [(start, end)])
            opts['force_keyframes_at_cuts'] = task.get('force_keyframes', False)
        
        return opts
    
    def _time_to_seconds(self, ts) -> float:
        if ts is None:
            return 0.0
        
        if isinstance(ts, (int, float)):
            return float(ts)

        ts_str = str(ts)
        
        if ':' not in ts_str:
            try:
                return float(ts_str)
            except ValueError:
                return 0.0
        
        # Time format string
        parts = ts_str.split(':')
        try:
            # Support formats: "SS", "MM:SS", "HH:MM:SS"
            if len(parts) == 1:
                return float(parts[0])
            elif len(parts) == 2:
                m, s = map(float, parts)
                return m * 60 + s
            elif len(parts) == 3:
                h, m, s = map(float, parts)
                return h * 3600 + m * 60 + s
            else:
                # Invalid format, return 0
                return 0.0
        except (ValueError, TypeError):
            return 0.0
    
    def cleanup_task(self, task_id: str):
        task_dir = self._get_task_dir(task_id)
        if os.path.exists(task_dir):
            shutil.rmtree(task_dir, ignore_errors=True)
        
        tasks = Storage.load_tasks()
        if task_id in tasks:
            del tasks[task_id]
            Storage.save_tasks(tasks)
    
    def process_tasks(self):
        while True:
            tasks = Storage.load_tasks()
            current_time = datetime.now()
            
            for task_id, task_data in list(tasks.items()):
                if task_data['status'] == TaskStatus.WAITING.value:
                    self._submit_task(task_id, task_data)
                
                elif task_data['status'] in [TaskStatus.COMPLETED.value, TaskStatus.ERROR.value]:
                    if 'completed_time' in task_data:
                        completed = datetime.fromisoformat(task_data['completed_time'])
                        if current_time - completed > timedelta(minutes=task_config.CLEANUP_TIME_MINUTES):
                            self.cleanup_task(task_id)
            
            # Cleanup orphaned folders every 5 minutes
            if current_time.minute % 5 == 0 and current_time.second == 0:
                self._cleanup_orphaned_folders()
            
            time.sleep(1)
    
    def _submit_task(self, task_id: str, task_data: dict):
        task_type = task_data['task_type']

        self._update_task(task_id, status=TaskStatus.PROCESSING.value)

        if task_type == TaskType.GET_INFO.value:
            self.executor.submit(self.download_info, task_id)
        else:
            self.executor.submit(self.download_media, task_id)
    
    def _cleanup_orphaned_folders(self):
        tasks = Storage.load_tasks()
        task_ids = set(tasks.keys())
        
        for folder in os.listdir(storage.DOWNLOAD_DIR):
            folder_path = os.path.join(storage.DOWNLOAD_DIR, folder)
            if os.path.isdir(folder_path) and folder not in task_ids:
                shutil.rmtree(folder_path, ignore_errors=True)
    
    def initialize(self):
        # Fix interrupted tasks
        tasks = Storage.load_tasks()
        for task_id, task_data in tasks.items():
            if task_data['status'] == TaskStatus.PROCESSING.value:
                task_data['status'] = TaskStatus.ERROR.value
                task_data['error'] = 'Task was interrupted'
                task_data['completed_time'] = datetime.now().isoformat()
        Storage.save_tasks(tasks)
        
        # Start processing thread
        thread = threading.Thread(target=self.process_tasks, daemon=True)
        thread.start()

# Initialize downloader
downloader = YTDownloader()
downloader.initialize()
