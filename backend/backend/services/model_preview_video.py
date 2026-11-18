"""Video frame sampling and overlay export for detection previews."""
import asyncio
import json
import shutil
import subprocess
import weakref
from fractions import Fraction
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont

MAX_VIDEO_SIZE = 512 * 1024**2
MAX_VIDEO_SECONDS = 600
_render_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def cache_path(tenant_id, run_id) -> str:
    return f'previews/{tenant_id}/{run_id}/rendered.mp4'


def render_lock(run_id) -> asyncio.Lock:
    key = str(run_id)
    lock = _render_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _render_locks[key] = lock
    return lock


def frame_rate(interval):
    fraction = Fraction(str(interval)).limit_denominator(10000)
    return f'{fraction.denominator}/{fraction.numerator}'


def prepare(upload, directory, interval):
    source = Path(directory) / 'source'
    upload.seek(0)
    with source.open('wb') as target:
        shutil.copyfileobj(upload, target)
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
            'stream=width,height:format=duration', '-of', 'json', str(source)], capture_output=True, check=True, timeout=30)
        metadata = json.loads(result.stdout)
        stream = metadata['streams'][0]
        width, height, duration = int(stream['width']), int(stream['height']), float(metadata['format']['duration'])
        if not 0 < duration <= MAX_VIDEO_SECONDS or not 0 < width * height <= 1920 * 1080 or max(width, height) > 1920:
            raise ValueError('视频时长或分辨率超限')
        if duration / interval > 5000:raise HTTPException(400,'采样帧超过5000张，请调大分析间隔')
        frames = Path(directory) / 'frames'
        frames.mkdir()
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(source), '-vf', f'fps={frame_rate(interval)}',
            '-frames:v', '5000', '-q:v', '3', str(frames / '%06d.jpg')], capture_output=True, check=True, timeout=180)
        paths = sorted(frames.glob('*.jpg'))
        if not paths:raise ValueError('视频没有可分析的画面')
        return dict(width=width, height=height, duration=duration), paths
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(400, '视频无法解码，或超过 10 分钟 / 1920×1080 限制')


def render(source, items, interval, directory):
    overlays = Path(directory) / 'overlays'
    overlays.mkdir()
    width, height = items[0].width, items[0].height
    cjk_font = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    try:
        font = ImageFont.truetype(str(cjk_font) if cjk_font.exists() else 'DejaVuSans.ttf', max(14, width // 70))
    except OSError:
        font = ImageFont.load_default()
    for index, item in enumerate(items):
        picture = Image.new('RGBA', (width, height))
        draw = ImageDraw.Draw(picture)
        for box in item.predictions:
            x, y = box['x'], box['y']
            draw.rectangle((x, y, x + box['width'], y + box['height']), outline='#00e5a8', width=max(2, width // 500))
            label = box['label'] + (f" {box['score'] * 100:.1f}%" if 'score' in box else '')
            draw.text((x, max(0, y - 20)), label, font=font, fill='white', stroke_width=2, stroke_fill='black')
        picture.save(overlays / f'{index + 1:06d}.png')
    output = Path(directory) / 'result.mp4'
    try:
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(source), '-framerate', frame_rate(interval),
            '-i', str(overlays / '%06d.png'), '-filter_complex', '[0:v][1:v]overlay=0:0:shortest=0:eof_action=repeat[v]',
            '-map', '[v]', '-map', '0:a?', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
            '-movflags', '+faststart', str(output)], capture_output=True, check=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(500, '视频绘制失败，请稍后重试')
    return output
