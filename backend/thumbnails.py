"""Vantage thumbnail generation — ffmpeg frame extraction + SVG fallbacks."""
import os, re, subprocess

THUMBNAIL_DIR = "/opt/ares/media/thumbnails"
FFMPEG = "/usr/bin/ffmpeg"
os.makedirs(THUMBNAIL_DIR, exist_ok=True)

DEFAULT_THUMBS = {
    "cinematic": ("#1a0533", "#4a00e0"),
    "trading-recap": ("#0a1628", "#00d4ff"),
    "agent-birth": ("#1a0a2e", "#8B5CF6"),
    "market-update": ("#16281a", "#22c55e"),
    "debate-breakdown": ("#281a0a", "#f59e0b"),
    "custom": ("#0f0a1a", "#3b82f6"),
}
CATEGORY_THUMBS = {
    "DeFi": ("#0a1628", "#00d4ff"),
    "NFT": ("#2e0a2e", "#8B5CF6"),
    "Market Analysis": ("#16281a", "#22c55e"),
    "Tutorials": ("#1a1a2e", "#6366f1"),
    "AI": ("#0f0a1a", "#ec4899"),
    "Governance": ("#281a0a", "#f59e0b"),
    "Memes": ("#2e1a0a", "#ef4444"),
    "video": ("#0f0a1a", "#3b82f6"),
}

def _make_svg(filename, c1, c2, icon, label):
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360">
  <defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
    <stop offset="0%" style="stop-color:{c1}"/>
    <stop offset="100%" style="stop-color:{c2}"/>
  </linearGradient></defs>
  <rect width="640" height="360" fill="url(#g)"/>
  <text x="320" y="170" text-anchor="middle" dominant-baseline="central"
        font-family="sans-serif" font-size="48" fill="rgba(255,255,255,0.12)">{icon}</text>
  <text x="320" y="235" text-anchor="middle" dominant-baseline="central"
        font-family="sans-serif" font-size="13" fill="rgba(255,255,255,0.2)">{label}</text>
</svg>"""
    path = os.path.join(THUMBNAIL_DIR, filename)
    with open(path, "w") as f:
        f.write(svg)
    return f"/media/thumbnails/{filename}"

def generate_thumbnail(video_path, project_id, template="custom"):
    """Extract frame at 2s, or generate gradient SVG."""
    thumb_name = f"{project_id}_thumb.jpg"
    thumb_path = os.path.join(THUMBNAIL_DIR, thumb_name)
    try:
        subprocess.run([
            FFMPEG, "-y", "-i", video_path, "-ss", "2", "-vframes", "1",
            "-q:v", "3", thumb_path
        ], capture_output=True, timeout=15, check=True)
        if os.path.getsize(thumb_path) > 500:
            return f"/media/thumbnails/{thumb_name}"
    except Exception:
        pass
    c1, c2 = DEFAULT_THUMBS.get(template, DEFAULT_THUMBS["custom"])
    return _make_svg(f"{project_id}_thumb.svg", c1, c2, "🎬", template)

def get_default_thumbnail(category):
    """Get or create a category default thumbnail SVG."""
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in category)[:30].lower()
    filename = f"default_{safe}.svg"
    path = os.path.join(THUMBNAIL_DIR, filename)
    if os.path.exists(path):
        return f"/media/thumbnails/{filename}"
    c1, c2 = CATEGORY_THUMBS.get(category, DEFAULT_THUMBS["custom"])
    return _make_svg(filename, c1, c2, "🎬", category[:24])
