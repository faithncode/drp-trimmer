#!/usr/bin/env python3
"""
DRP Trimmer GUI
───────────────
Cross-platform (Mac / Windows) desktop tool for trimming multicam DRP recordings.

Workflow:
  1. Click "Open Project Folder" and pick the folder (or load a .drp directly)
     Supported layouts:
        A)  Project/Talisman.drp + Project/Video ISO Files/Talisman CAM 1.mp4 ...
        B)  Project/Talisman.drp + Project/Talisman CAM 1.mp4 ...   (siblings)
     No .drp?  If camera files are found, all cameras are stacked on separate
     tracks (no cuts) for the whole In/Out window.
  2. Set In / Out timecode (HH:MM:SS:FF, HH:MM:SS, MM:SS, or seconds)
  3. Choose output format: Premiere XML or FCPXML
  4. Optionally trim camera ISO footage with FFmpeg (lossless stream-copy)
  5. Export trimmed XML + optionally the trimmed video files

Requirements:
  tkinter (built-in), FFmpeg in PATH for footage trimming (ffprobe optional,
  used to auto-detect fps/duration when there is no .drp)
"""

import json, math, os, re, shutil, subprocess, sys, threading, tkinter as tk
from fractions import Fraction
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import quote

def _fix_environment_path():
    """Ensure standard tool paths (Homebrew, MacPorts, local bin) are in PATH for GUI apps on macOS/Linux/Windows."""
    extra_paths = []
    if sys.platform == "darwin":
        extra_paths.extend([
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/usr/local/bin",
            "/usr/local/sbin",
            "/opt/local/bin",
            "/opt/local/sbin",
        ])
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        extra_paths.extend([exe_dir, os.path.join(exe_dir, "..", "Resources")])
        if hasattr(sys, "_MEIPASS"):
            extra_paths.append(sys._MEIPASS)
    extra_paths.extend([
        os.path.expanduser("~/bin"),
        os.path.expanduser("~/.local/bin"),
    ])
    cur_path = os.environ.get("PATH", "")
    cur_list = cur_path.split(os.pathsep)
    for p in extra_paths:
        if os.path.isdir(p) and p not in cur_list:
            cur_list.insert(0, p)
    os.environ["PATH"] = os.pathsep.join(cur_list)

_fix_environment_path()

def find_tool(name):
    """Find executable path in bundled dirs, next to exe/script, PATH, or common locations."""
    exe_name = f"{name}.exe" if sys.platform == "win32" else name

    # 1. Bundled inside PyInstaller onefile temp folder (_MEIPASS)
    if hasattr(sys, "_MEIPASS"):
        p = os.path.join(sys._MEIPASS, exe_name)
        if os.path.isfile(p):
            return p

    # 2. Next to the running executable or script (portable mode)
    app_dir = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    candidates = [
        os.path.join(app_dir, exe_name),
        os.path.join(app_dir, name),
        os.path.join(app_dir, "..", "Resources", name),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c

    # 3. In system PATH
    found = shutil.which(name) or shutil.which(exe_name)
    if found:
        return found

    # 4. Common fallback directories
    fallbacks = []
    if sys.platform == "darwin":
        fallbacks = [
            f"/opt/homebrew/bin/{name}",
            f"/usr/local/bin/{name}",
            f"/usr/bin/{name}",
            os.path.expanduser(f"~/bin/{name}"),
            os.path.expanduser(f"~/.local/bin/{name}"),
        ]
    elif sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA", "")
        user_prof = os.environ.get("USERPROFILE", "")
        fallbacks = [
            f"C:\\ffmpeg\\bin\\{exe_name}",
            f"C:\\ProgramData\\chocolatey\\bin\\{exe_name}",
            os.path.join(local_app, "Microsoft", "WinGet", "Links", exe_name),
            os.path.join(user_prof, "ffmpeg", "bin", exe_name),
        ]
    for c in fallbacks:
        if c and os.path.isfile(c):
            return c

    return None


# ── colours ──────────────────────────────────────────────────────────────────
BG       = "#0f1117"
BG2      = "#1a1d27"
BG3      = "#242736"
ACCENT   = "#6c63ff"
ACCENT2  = "#a89cff"
SUCCESS  = "#4ade80"
WARNING  = "#fbbf24"
ERROR    = "#f87171"
TEXT     = "#e8e8f0"
TEXT_DIM = "#6b7280"
BORDER   = "#2e3147"

FONT_TITLE = ("Segoe UI", 18, "bold")
FONT_HEAD  = ("Segoe UI", 11, "bold")
FONT_BODY  = ("Segoe UI", 10)
FONT_MONO  = ("Cascadia Code", 10) if sys.platform == "win32" else ("Menlo", 10)
FONT_SMALL = ("Segoe UI", 9)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".mxf", ".m4v"}

# ══════════════════════════════════════════════════════════════════════════════
# Project-folder scanning
# ══════════════════════════════════════════════════════════════════════════════

def _walk_limited(root, max_depth=3):
    """Yield (dirpath, filenames) down to max_depth, skipping hidden folders."""
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if depth >= max_depth:
            dirnames[:] = []
        yield dirpath, filenames


def find_videos(root, max_depth=3):
    """All video files under root (excluding our own *_trimmed outputs)."""
    out = []
    if not root or not os.path.isdir(root):
        return out
    for dirpath, files in _walk_limited(root, max_depth):
        for fn in files:
            stem, ext = os.path.splitext(fn)
            if fn.startswith("."): continue
            if ext.lower() in VIDEO_EXTS and not stem.endswith("_trimmed"):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def find_drps(root, max_depth=2):
    out = []
    for dirpath, files in _walk_limited(root, max_depth):
        for fn in files:
            if fn.lower().endswith(".drp") and not fn.startswith("."):
                out.append(os.path.join(dirpath, fn))
    # shallowest first, then alphabetical
    return sorted(out, key=lambda p: (p.count(os.sep), p.lower()))


def cam_number_from_name(filename):
    """'Talisman CAM 3.mp4' / 'Camera_3.mov' / 'cam3.mp4'  →  3"""
    m = re.search(r'cam(?:era)?[\s_\-]*0*(\d+)', os.path.basename(filename), re.I)
    return int(m.group(1)) if m else None


def camera_index(sources):
    """{source_index: camera_number} for 'Camera N' video sources."""
    out = {}
    for idx, s in sources.items():
        name = s.get("name", "")
        if s.get("type") == "Video" and name.startswith("Camera "):
            try: out[idx] = int(name.split("Camera ")[1])
            except ValueError: pass
    return out


def scan_project_folder(folder):
    """Describe what is inside a project folder, for either layout.

    Also works with no .drp at all: cameras are still detected so the caller
    can fall back to stacked-camera mode.
    """
    folder = os.path.abspath(folder)
    drps = find_drps(folder)
    info = {"folder": folder, "drps": drps, "drp": None, "program": None,
            "cams": {}, "layout": "unknown", "has_audio_dir": False}
    info["has_audio_dir"] = os.path.isdir(os.path.join(folder, "Audio Source Files"))

    stem = None
    drp_dir = folder
    if drps:
        # prefer a .drp whose stem matches the folder name, else the shallowest
        match = [d for d in drps if Path(d).stem.lower() == Path(folder).name.lower()]
        info["drp"] = match[0] if match else drps[0]
        stem = Path(info["drp"]).stem
        drp_dir = os.path.dirname(info["drp"])

    vids = find_videos(folder)
    for v in vids:
        if stem and Path(v).stem.lower() == stem.lower():
            info["program"] = v
            continue
        n = cam_number_from_name(v)
        if n is None: continue
        cur = info["cams"].get(n)
        # prefer files in the same folder as the .drp, then in an "ISO" folder
        def score(p):
            return (0 if os.path.dirname(p) == drp_dir else 1,
                    0 if "iso" in p.lower() else 1, len(p))
        if cur is None or score(v) < score(cur):
            info["cams"][n] = v
    if info["cams"]:
        dirs = {os.path.dirname(p) for p in info["cams"].values()}
        if dirs == {drp_dir}:
            info["layout"] = "siblings"
        else:
            sub = os.path.basename(sorted(dirs)[0])
            info["layout"] = f"subfolder '{sub}'"
    return info


def resolve_media_map(cam_idx, sources, media_root):
    """
    Work out the real file for each camera regardless of folder layout.
    Order: DRP relative path → same filename anywhere under root → 'CAM N' match.
    """
    result = {}
    vids = find_videos(media_root) if media_root else []
    by_base = {os.path.basename(v).lower(): v for v in vids}
    for idx, cam in cam_idx.items():
        rel = sources.get(idx, {}).get("file")
        if rel and media_root:
            p = os.path.join(media_root, rel)
            if os.path.isfile(p):
                result[cam] = p; continue
        if rel:
            b = os.path.basename(rel).lower()
            if b in by_base:
                result[cam] = by_base[b]; continue
        matches = [v for v in vids if cam_number_from_name(v) == cam]
        if matches:
            matches.sort(key=lambda p: (0 if "iso" in p.lower() else 1, len(p)))
            result[cam] = matches[0]
    return result


def cam_file(cam, sources, name_to_idx, media_root, media_map):
    if media_map and cam in media_map:
        return media_map[cam]
    rel = sources.get(name_to_idx.get(cam), {}).get("file", f"CAM {cam}.mp4")
    return os.path.join(media_root, rel) if media_root else rel


def synthetic_project(cams):
    """No DRP: build the structures the exporters expect from {cam_no: path}."""
    sources = {n: {"name": f"Camera {n}", "type": "Video",
                   "file": os.path.basename(p)} for n, p in cams.items()}
    cam_idx = {n: n for n in cams}
    return sources, cam_idx, dict(cams)


def probe_video(path):
    """(fps, duration_sec) via ffprobe, or (None, None) if unavailable."""
    tool = find_tool("ffprobe")
    if not tool:
        return None, None
    try:
        r = subprocess.run(
            [tool, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate:format=duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=20)
        d = json.loads(r.stdout)
        return (float(Fraction(d["streams"][0]["r_frame_rate"])),
                float(d["format"]["duration"]))
    except Exception:
        return None, None


def path_to_url(full):
    url = full.replace(os.sep, "/")
    if not url.startswith("/"): url = "/" + url
    return "file://" + quote(url, safe="/:")


# ══════════════════════════════════════════════════════════════════════════════
# Core logic
# ══════════════════════════════════════════════════════════════════════════════

def parse_drp(path):
    sources, cur_meb, records, meb_events, video_mode = {}, {}, [], [], None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            obj = json.loads(line)
            if "videoMode" in obj: video_mode = obj["videoMode"]
            if "sources" in obj:
                for s in obj["sources"]:
                    sources.setdefault(s["_index_"], {}).update(s)
            meb = obj.get("mixEffectBlocks")
            if meb and len(meb) > 0:
                changed = {k: v for k, v in meb[0].items()
                           if k in ("source","transitionActive","transitionType")}
                if changed:
                    cur_meb.update(changed)
                    meb_events.append((obj["masterTimecode"], dict(cur_meb)))
            records.append((obj["masterTimecode"], cur_meb.get("source")))
    return records, sources, video_mode, meb_events


def fps_from_video_mode(vm):
    if not vm: return 30.0
    digits = ""
    for ch in reversed(vm):
        if ch.isdigit() or ch == ".": digits = ch + digits
        else: break
    try: return float(digits)
    except ValueError: return 30.0


def tc_to_frames(tc, fps):
    h, m, s, f = (int(x) for x in tc.split(":"))
    return int(round(((h*3600)+(m*60)+s)*fps)) + f


def parse_time_input(text, fps):
    text = text.strip()
    m = re.match(r'^(\d+):(\d+):(\d+)[:\.](\d+)$', text)
    if m:
        h,mi,s,f = int(m.group(1)),int(m.group(2)),int(m.group(3)),int(m.group(4))
        if len(m.group(4)) >= 3:
            return int(round((h*3600+mi*60+s+int(m.group(4))/(10**len(m.group(4))))*fps))
        return int(round((h*3600+mi*60+s)*fps)) + f
    m = re.match(r'^(\d+):(\d+):(\d+)$', text)
    if m:
        h,mi,s = int(m.group(1)),int(m.group(2)),int(m.group(3))
        return int(round((h*3600+mi*60+s)*fps))
    m = re.match(r'^(\d+):(\d+)$', text)
    if m:
        mi,s = int(m.group(1)),int(m.group(2))
        return int(round((mi*60+s)*fps))
    return int(round(float(text)*fps))


def build_segments(records, sources, fps, carry_forward=True):
    cam_of_idx = {}
    for idx, s in sources.items():
        name = s.get("name","")
        if s.get("type") == "Video" and name.startswith("Camera "):
            try: cam_of_idx[idx] = int(name.split("Camera ")[1])
            except ValueError: pass
    frames = [(tc_to_frames(tc,fps), src) for tc, src in records]
    last_cam, effective = None, []
    for f, src in frames:
        cam = cam_of_idx.get(src)
        if cam is None:
            cam = last_cam if (carry_forward and last_cam) else None
        else: last_cam = cam
        effective.append((f, cam))
    segs, cur_cam, seg_start = [], effective[0][1], effective[0][0]
    for i in range(1, len(effective)):
        f, cam = effective[i]
        if cam != cur_cam:
            segs.append((seg_start, f, cur_cam))
            seg_start, cur_cam = f, cam
    segs.append((seg_start, effective[-1][0], cur_cam))
    return segs, cam_of_idx


def clip_segments(segs, in_frame, out_frame):
    result = []
    for s, e, cam in segs:
        s2, e2 = max(s, in_frame), min(e, out_frame)
        if e2 > s2: result.append((s2-in_frame, e2-in_frame, cam))
    return result


def extract_dissolves(meb_events, fps, lookahead=10):
    dissolves, n = [], len(meb_events)
    for i in range(n):
        tc, c = meb_events[i]
        if c.get("transitionActive") is not True: continue
        outgoing, ttype = c.get("source"), c.get("transitionType")
        for j in range(i+1, min(i+1+lookahead, n)):
            tc2, c2 = meb_events[j]
            if c2.get("transitionActive") is False:
                incoming = c2.get("source")
                if ttype == "Mix" and incoming and incoming != outgoing:
                    dissolves.append((tc_to_frames(tc,fps), tc_to_frames(tc2,fps), outgoing, incoming))
                break
    return dissolves


def keep_ranges_for_track(segs, track_cam, first_frame, last_frame):
    ranges, cur_start = [], None
    for start, end, active_cam in segs:
        show = (active_cam is not None) and (active_cam >= track_cam)
        if show and cur_start is None: cur_start = start
        if not show and cur_start is not None:
            ranges.append((cur_start, start)); cur_start = None
    if cur_start is not None: ranges.append((cur_start, last_frame))
    return ranges


def xml_escape(s):
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")


def frac_time(frames, fps_num, fps_den):
    t = Fraction(frames*fps_den, fps_num)
    return f"{t.numerator}/{t.denominator}s"


def fps_rational(fps):
    if float(fps).is_integer(): return int(fps), 1
    from math import gcd
    num, den = int(round(fps*1000)), 1000
    g = gcd(num, den); return num//g, den//g


def _media_url(cam, sources, name_to_idx, media_root, media_map, trim_footage):
    """Full file:// URL for a camera, either the original or the trimmed copy."""
    orig = cam_file(cam, sources, name_to_idx, media_root, media_map)
    if trim_footage:
        base = os.path.splitext(os.path.basename(orig))[0] + "_trimmed.mp4"
        full = os.path.join(media_root, base) if media_root else base
    else:
        full = orig
    return path_to_url(full)


def build_fcpxml(norm_segs, sources, cam_idx, fps, media_root, project_name,
                 total_frames, dissolves=None, trim_footage=False, in_frame=0,
                 audio_cam=None, media_map=None):
    cameras = sorted(cam_idx.values())
    if audio_cam is None or audio_cam not in cameras:
        audio_cam = cameras[0]
    fps_num, fps_den = fps_rational(fps)
    name_to_idx = {v: k for k, v in cam_idx.items()}

    def asset_path(cam):
        return _media_url(cam, sources, name_to_idx, media_root, media_map, trim_footage)

    fd = f"{fps_den}/{fps_num}s"
    total_dur = frac_time(total_frames, fps_num, fps_den)
    L = []
    L.append('<?xml version="1.0" encoding="UTF-8"?>')
    L.append("<!DOCTYPE fcpxml>")
    L.append('<fcpxml version="1.9">')
    L.append("  <resources>")
    L.append(f'    <format id="r1" frameDuration="{fd}" width="1920" height="1080"/>')
    aid_of_cam = {}
    for i, cam in enumerate(cameras, 1):
        idx = name_to_idx[cam]
        s = sources.get(idx, {})
        aid = f"a{i}"; aid_of_cam[cam] = aid
        has_aud = "1" if cam == audio_cam else "0"
        L.append(f'    <asset id="{aid}" name="{xml_escape(s.get("name",f"Camera {cam}"))}" '
                 f'src="{xml_escape(asset_path(cam))}" start="0s" duration="{total_dur}" '
                 f'hasVideo="1" hasAudio="{has_aud}" format="r1"/>')
    L.append('    <effect id="trDissolve" name="Cross Dissolve" '
             'uid=".../Transitions.localized/Dissolves.localized/'
             'Cross Dissolve.localized/Cross Dissolve.moti"/>')
    L.append("  </resources>")
    L.append("  <library>")
    L.append(f'    <event name="{xml_escape(project_name)}">')
    L.append(f'      <project name="{xml_escape(project_name)}">')
    L.append(f'        <sequence format="r1" duration="{total_dur}" tcStart="0s">')
    L.append("          <spine>")
    bc = cameras[0]; ba = aid_of_cam[bc]
    spine_audio = ' hasAudio="1"' if audio_cam == bc else ' hasAudio="0"'
    L.append(f'            <asset-clip ref="{ba}" offset="0s" name="Camera {bc}" '
             f'start="0s" duration="{total_dur}" format="r1"{spine_audio}>')
    if audio_cam != bc:
        aa = aid_of_cam[audio_cam]
        L.append(f'              <asset-clip ref="{aa}" lane="-1" offset="0s" '
                 f'name="Camera {audio_cam} (Audio)" start="0s" duration="{total_dur}" '
                 f'format="r1" hasVideo="0" hasAudio="1"/>')
    for lane, cam in enumerate(cameras[1:], 1):
        aid = aid_of_cam[cam]
        for start, end in keep_ranges_for_track(norm_segs, cam, 0, total_frames):
            if end <= start: continue
            off = frac_time(start, fps_num, fps_den)
            dur = frac_time(end-start, fps_num, fps_den)
            L.append(f'              <asset-clip ref="{aid}" lane="{lane}" '
                     f'offset="{off}" name="Camera {cam}" start="{off}" '
                     f'duration="{dur}" format="r1" hasAudio="0"/>')
    dl = len(cameras)
    if dissolves:
        for tf, ff, oi, ii in dissolves:
            oc, ic = cam_idx.get(oi), cam_idx.get(ii)
            if oc is None or ic is None: continue
            t0, t1 = tf-in_frame, ff-in_frame
            if t1<=t0 or t0<0 or t1>total_frames: continue
            mid = t0+(t1-t0)//2
            L.append(f'              <asset-clip ref="{aid_of_cam[oc]}" lane="{dl}" '
                     f'offset="{frac_time(t0,fps_num,fps_den)}" name="Camera {oc} (out)" '
                     f'start="{frac_time(t0,fps_num,fps_den)}" '
                     f'duration="{frac_time(mid-t0,fps_num,fps_den)}" format="r1" hasAudio="0"/>')
            L.append(f'              <transition name="Cross Dissolve" '
                     f'offset="{frac_time(t0,fps_num,fps_den)}" '
                     f'duration="{frac_time(t1-t0,fps_num,fps_den)}">')
            L.append('                <filter-video ref="trDissolve"/>')
            L.append('              </transition>')
            L.append(f'              <asset-clip ref="{aid_of_cam[ic]}" lane="{dl}" '
                     f'offset="{frac_time(mid,fps_num,fps_den)}" name="Camera {ic} (in)" '
                     f'start="{frac_time(mid,fps_num,fps_den)}" '
                     f'duration="{frac_time(t1-mid,fps_num,fps_den)}" format="r1" hasAudio="0"/>')
    L.append("            </asset-clip>")
    L.append("          </spine>")
    L.append("        </sequence>")
    L.append("      </project>")
    L.append("    </event>")
    L.append("  </library>")
    L.append("</fcpxml>")
    return "\n".join(L)


def build_premiere_xml(norm_segs, sources, cam_idx, fps, media_root, project_name,
                       total_frames, dissolves=None, trim_footage=False, in_frame=0,
                       audio_cam=None, media_map=None):
    cameras = sorted(cam_idx.values())
    if audio_cam is None or audio_cam not in cameras:
        audio_cam = cameras[0]
    name_to_idx = {v: k for k, v in cam_idx.items()}
    is_ntsc = abs(fps - round(fps)) > 0.001
    timebase = int(round(fps))

    def rate_block(indent):
        p = " " * indent
        return (f"{p}<rate>\n{p}  <timebase>{timebase}</timebase>\n"
                f"{p}  <ntsc>{'TRUE' if is_ntsc else 'FALSE'}</ntsc>\n{p}</rate>")

    def file_url(cam):
        return _media_url(cam, sources, name_to_idx, media_root, media_map, trim_footage)

    file_defined = set()
    clip_counter = [0]

    def file_block(cam, indent, audio=False):
        p = " " * indent
        fid = f"file-cam{cam}"
        if fid in file_defined: return f'{p}<file id="{fid}"/>'
        file_defined.add(fid)
        idx = name_to_idx[cam]
        name = sources.get(idx, {}).get("name", f"Camera {cam}")
        # Only the chosen audio camera carries audio
        media_tag = "<video/><audio/>" if cam == audio_cam else "<video/>"
        out = [f'{p}<file id="{fid}">',
               f"{p}  <name>{xml_escape(name)}</name>",
               f"{p}  <pathurl>{xml_escape(file_url(cam))}</pathurl>",
               rate_block(indent+2),
               f"{p}  <duration>{total_frames}</duration>",
               f"{p}  <media>{media_tag}</media>",
               f"{p}</file>"]
        return "\n".join(out)

    def clipitem(cam, start, end, indent, suffix=""):
        clip_counter[0] += 1
        p = " " * indent
        cid = f"clipitem-{clip_counter[0]}"
        idx = name_to_idx[cam]
        name = sources.get(idx, {}).get("name", f"Camera {cam}") + suffix
        out = [f'{p}<clipitem id="{cid}">',
               f"{p}  <name>{xml_escape(name)}</name>",
               f"{p}  <duration>{total_frames}</duration>",
               rate_block(indent+2),
               f"{p}  <start>{start}</start>",
               f"{p}  <end>{end}</end>",
               f"{p}  <in>{start}</in>",
               f"{p}  <out>{end}</out>",
               file_block(cam, indent+2),
               f"{p}</clipitem>"]
        return "\n".join(out)

    def audio_clipitem(cam, start, end, indent, channel, suffix=""):
        clip_counter[0] += 1
        p = " " * indent
        cid = f"clipitem-{clip_counter[0]}"
        idx = name_to_idx[cam]
        name = sources.get(idx, {}).get("name", f"Camera {cam}") + suffix
        out = [f'{p}<clipitem id="{cid}">',
               f"{p}  <name>{xml_escape(name)}</name>",
               f"{p}  <duration>{total_frames}</duration>",
               rate_block(indent+2),
               f"{p}  <start>{start}</start>",
               f"{p}  <end>{end}</end>",
               f"{p}  <in>{start}</in>",
               f"{p}  <out>{end}</out>",
               f"{p}  <channelcount>2</channelcount>",
               file_block(cam, indent+2, audio=True),
               f"{p}  <sourcetrack>",
               f"{p}    <mediatype>audio</mediatype>",
               f"{p}    <trackindex>{channel}</trackindex>",
               f"{p}  </sourcetrack>",
               f"{p}</clipitem>"]
        return "\n".join(out)

    L = []
    L.append('<?xml version="1.0" encoding="UTF-8"?>')
    L.append("<!DOCTYPE xmeml>")
    L.append('<xmeml version="5">')
    L.append("  <sequence>")
    L.append(f"    <name>{xml_escape(project_name)}</name>")
    L.append(f"    <duration>{total_frames}</duration>")
    L.append(rate_block(4))
    L.append("    <media><video>")
    L.append("        <format><samplecharacteristics>")
    L.append(rate_block(12))
    L.append("            <width>1920</width><height>1080</height>")
    L.append("          </samplecharacteristics></format>")
    bc = cameras[0]
    L.append("        <track>")
    L.append(clipitem(bc, 0, total_frames, 10))
    L.append("        </track>")
    for cam in cameras[1:]:
        L.append("        <track>")
        for start, end in keep_ranges_for_track(norm_segs, cam, 0, total_frames):
            if end <= start: continue
            L.append(clipitem(cam, start, end, 10))
        L.append("        </track>")
    if dissolves:
        tl = ["        <track>"]
        has_any = False
        for tf, ff, oi, ii in dissolves:
            oc, ic = cam_idx.get(oi), cam_idx.get(ii)
            if oc is None or ic is None: continue
            t0, t1 = tf-in_frame, ff-in_frame
            if t1<=t0 or t0<0 or t1>total_frames: continue
            has_any = True; mid = t0+(t1-t0)//2
            tl.extend([clipitem(oc,t0,mid,10," (out)"),
                        "          <transitionitem>",
                        f"            <start>{t0}</start>",
                        f"            <end>{t1}</end>",
                        "            <alignment>center</alignment>",
                        "            <effect>",
                        "              <name>Cross Dissolve</name>",
                        "              <effectid>Cross Dissolve</effectid>",
                        "              <effectcategory>Dissolve</effectcategory>",
                        "              <effecttype>transition</effecttype>",
                        "              <mediatype>video</mediatype>",
                        "            </effect>",
                        "          </transitionitem>",
                        clipitem(ic,mid,t1,10," (in)")])
        tl.append("        </track>")
        if has_any: L.extend(tl)
    L.append("      </video>")
    # Audio section
    L.append("      <audio>")
    L.append("        <format><samplecharacteristics>")
    L.append("            <depth>16</depth>")
    L.append("            <samplerate>48000</samplerate>")
    L.append("          </samplecharacteristics></format>")
    for ch in (1, 2):
        L.append("        <track>")
        L.append(audio_clipitem(audio_cam, 0, total_frames, 10, channel=ch))
        L.append("        </track>")
    L.append("      </audio>")
    L.extend(["    </media>", "  </sequence>", "</xmeml>"])
    return "\n".join(L)


def ffmpeg_available():
    return find_tool("ffmpeg") is not None


def trim_video(src, dst, start_sec, end_sec, log_cb=None):
    tool = find_tool("ffmpeg") or "ffmpeg"
    cmd = [tool,"-y","-ss",str(start_sec),"-to",str(end_sec),
           "-i",src,"-c","copy","-avoid_negative_ts","make_zero",dst]
    if log_cb: log_cb("$ " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0: raise RuntimeError(r.stderr[-2000:])


# ══════════════════════════════════════════════════════════════════════════════
# GUI
# ══════════════════════════════════════════════════════════════════════════════

class FlatButton(tk.Label):
    """Label-based button: honours custom colours on macOS (tk.Button doesn't)."""
    def __init__(self, parent, text, command, bg, fg, font,
                 padx=10, pady=4, hover_bg=ACCENT2, hover_fg="white"):
        super().__init__(parent, text=text, bg=bg, fg=fg, font=font,
                         padx=padx, pady=pady, cursor="hand2")
        self._bg, self._fg = bg, fg
        self._hbg, self._hfg = hover_bg, hover_fg
        self._cmd = command
        self._enabled = True
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda e: self._enabled and
                  self.config(bg=self._hbg, fg=self._hfg))
        self.bind("<Leave>", lambda e: self.config(bg=self._bg, fg=self._fg))

    def _click(self, _e):
        if self._enabled and self._cmd:
            self._cmd()

    def set_enabled(self, on):
        self._enabled = on
        self.config(bg=self._bg if on else BG3,
                    fg=self._fg if on else TEXT_DIM,
                    cursor="hand2" if on else "arrow")


class App:
    def __init__(self, root):
        self.root = root
        root.title("DRP Trimmer")
        root.configure(bg=BG)
        root.minsize(860, 680)
        self._center(920, 800)

        self.drp_path    = tk.StringVar()
        self.media_root  = tk.StringVar()
        self.out_dir     = tk.StringVar()
        self.in_time     = tk.StringVar(value="00:00:00:00")
        self.out_time    = tk.StringVar(value="")
        self.fmt_var     = tk.StringVar(value="premiere")
        self.audio_cam_var = tk.StringVar(value="Camera 1")
        self.trim_ftr    = tk.BooleanVar(value=True)
        self.carry_fwd   = tk.BooleanVar(value=True)
        self.fps_ov      = tk.StringVar(value="")
        self.proj_name   = tk.StringVar(value="")

        self._drp_data   = None
        self._manual_cams = None      # {cam_no: path} when there is no .drp
        self._fps        = None
        self._audio_cam_combo = None

        self._build()

    def _center(self, w, h):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self):
        # header
        hdr = tk.Frame(self.root, bg=ACCENT)
        hdr.pack(fill="x")
        tk.Label(hdr, text="⚡  DRP Trimmer", font=FONT_TITLE,
                 bg=ACCENT, fg="white", padx=24, pady=14).pack(side="left")
        tk.Label(hdr, text="Open project folder → set In/Out → export trimmed XML + footage",
                 font=FONT_SMALL, bg=ACCENT, fg="#ccc8ff",
                 padx=8, pady=14).pack(side="left")

        # scrollable area
        cv = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        vsb = tk.Scrollbar(self.root, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        sf = tk.Frame(cv, bg=BG)
        sf.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        win_id = cv.create_window((0,0), window=sf, anchor="nw")

        # keep content as wide as the window and the scrollregion up to date
        def _on_canvas_cfg(e):
            cv.itemconfigure(win_id, width=e.width)
            cv.configure(scrollregion=cv.bbox("all"))
        cv.bind("<Configure>", _on_canvas_cfg)

        # 1 scroll "unit" = 1 pixel, so we control speed exactly
        cv.configure(yscrollincrement=1)

        def _scroll_px(px):
            px = int(px)
            if px == 0:
                return
            if cv.yview() == (0.0, 1.0):      # content fits, nothing to scroll
                return
            cv.yview_scroll(px, "units")

        def _on_wheel(e):
            """Mouse wheel, and trackpads on Tk 8.6 (Mac + Windows)."""
            d = e.delta
            if sys.platform == "darwin":
                px = -d * 4 if abs(d) < 60 else -d / 3
            else:
                px = -d / 120 * 40             # Windows: multiples of 120
            px = int(round(px))
            if px == 0:
                px = -1 if d > 0 else 1        # never swallow tiny deltas
            _scroll_px(px)

        def _on_touchpad(e):
            """Tk 9.x on macOS sends trackpad scrolling as <TouchpadScroll>,
            NOT <MouseWheel>, which is why the old binding did nothing."""
            try:
                _dx, dy = self.root.tk.call("tk::PreciseScrollDeltas", e.delta)
                _scroll_px(-float(dy))
            except (tk.TclError, ValueError):
                pass

        # bind_all so scrolling works over any child widget, not just empty canvas
        self.root.bind_all("<MouseWheel>", _on_wheel)
        try:
            self.root.bind_all("<TouchpadScroll>", _on_touchpad)   # Tk 9+ only
        except tk.TclError:
            pass
        self.root.bind_all("<Button-4>", lambda e: _scroll_px(-40))  # Linux
        self.root.bind_all("<Button-5>", lambda e: _scroll_px(40))

        self._build_files(sf)
        self._build_timecode(sf)
        self._build_options(sf)
        self._build_actions(sf)
        self._build_log(sf)

    def _card(self, parent, title, icon=""):
        outer = tk.Frame(parent, bg=BG2,
                         highlightbackground=BORDER, highlightthickness=1)
        outer.pack(fill="x", padx=20, pady=(12,0))
        head = tk.Frame(outer, bg=BG3)
        head.pack(fill="x")
        tk.Label(head, text=f"{icon}  {title}" if icon else title,
                 font=FONT_HEAD, bg=BG3, fg=ACCENT2, padx=16, pady=8).pack(side="left")
        body = tk.Frame(outer, bg=BG2)
        body.pack(fill="x", padx=16, pady=12)
        return body

    def _lbl(self, parent, text, w=18):
        tk.Label(parent, text=text, font=FONT_BODY, bg=BG2, fg=TEXT,
                 width=w, anchor="w").pack(side="left")

    def _entry(self, parent, var, width=40):
        e = tk.Entry(parent, textvariable=var, width=width,
                     bg=BG3, fg=TEXT, insertbackground=ACCENT,
                     relief="flat", font=FONT_BODY,
                     highlightbackground=BORDER, highlightthickness=1)
        e.pack(side="left", ipady=5, ipadx=4)
        return e

    def _btn(self, parent, text, cmd, accent=False):
        b = FlatButton(parent, text, cmd,
                       bg=ACCENT if accent else BG3,
                       fg="white" if accent else ACCENT2,
                       font=FONT_SMALL if not accent else ("Segoe UI", 11, "bold"),
                       padx=10 if not accent else 16,
                       pady=4 if not accent else 10)
        b.pack(side="left", padx=(6 if not accent else 0, 0))
        return b

    # ── file section ──────────────────────────────────────────────────────────

    def _build_files(self, parent):
        body = self._card(parent, "Files & Paths", "📁")

        # one-click project folder loader
        top = tk.Frame(body, bg=BG2); top.pack(fill="x", pady=(0, 8))
        self._btn(top, "📂  Open Project Folder…", self._browse_project, accent=True)
        tk.Label(top, text="Finds the .drp and camera files automatically — "
                           "no .drp? cameras are stacked instead",
                 font=FONT_SMALL, bg=BG2, fg=TEXT_DIM).pack(side="left", padx=10)

        for label, var, cmd, hint in [
            ("DRP File (.drp)", self.drp_path, self._browse_drp, "ATEM switcher log (optional)"),
            ("Media Root Folder", self.media_root, self._browse_media, "Searched recursively for camera ISOs"),
            ("Output Folder", self.out_dir, self._browse_out, "Where XML & clips are saved"),
        ]:
            row = tk.Frame(body, bg=BG2); row.pack(fill="x", pady=4)
            self._lbl(row, label)
            self._entry(row, var, 46)
            self._btn(row, "Browse…", cmd)
            if hint:
                tk.Label(row, text=hint, font=FONT_SMALL, bg=BG2, fg=TEXT_DIM).pack(side="left", padx=6)

        self._info = tk.Label(body, text="No DRP loaded",
                              font=FONT_SMALL, bg=BG2, fg=TEXT_DIM, anchor="w")
        self._info.pack(fill="x", pady=(8,0))
        self._media_info = tk.Label(body, text="", font=FONT_SMALL, bg=BG2,
                                    fg=TEXT_DIM, anchor="w", justify="left")
        self._media_info.pack(fill="x", pady=(2,0))

    def _browse_project(self):
        folder = filedialog.askdirectory(title="Select project folder")
        if folder:
            self._open_project(folder)

    def _open_project(self, folder):
        """Scan a folder and fill in everything for either layout."""
        self._log("─"*60)
        self._log(f"📂 Scanning {folder}", "dim")
        info = scan_project_folder(folder)
        if not info["drp"]:
            if info["cams"]:
                self._open_without_drp(info)
                return
            self._log("✗ No .drp file and no camera files found in that folder.", "err")
            messagebox.showerror("Nothing found",
                                 "Couldn't find a .drp file or camera files "
                                 "(names like '… CAM 1.mp4') in that folder.")
            return
        drp = info["drp"]
        if len(info["drps"]) > 1:
            self._log(f"⚠ {len(info['drps'])} .drp files found; using "
                      f"{os.path.basename(drp)}", "warn")
            picked = filedialog.askopenfilename(
                title="Multiple .drp files found — pick one",
                initialdir=os.path.dirname(drp),
                filetypes=[("DRP files", "*.drp"), ("All files", "*.*")])
            if picked:
                drp = picked
                info = scan_project_folder(folder)
                info["drp"] = drp

        stem = Path(drp).stem
        self.drp_path.set(drp)
        self.media_root.set(info["folder"])
        self.out_dir.set(os.path.join(info["folder"], "Trimmed"))
        self.proj_name.set(stem)
        self.in_time.set("00:00:00:00")
        self.out_time.set("")
        self._drp_data = None

        found = sorted(info["cams"])
        self._log(f"✓ Project: {stem}", "ok")
        self._log(f"✓ Layout: ISOs in {info['layout']}" if found else
                  "⚠ No camera ISO files found (need names like '… CAM 1.mp4')",
                  "ok" if found else "warn")
        if found:
            self._log(f"✓ Cameras found: {found}", "ok")
        if info["program"]:
            self._log(f"✓ Program mix: {os.path.basename(info['program'])} (not used as an ISO)", "dim")
        if info["has_audio_dir"]:
            self._log("• 'Audio Source Files' folder present", "dim")
        self._load_drp(drp)

    def _open_without_drp(self, info):
        """No .drp: stack every camera found, on its own track, for the In/Out window."""
        cams, folder = info["cams"], info["folder"]
        self._log("⚠ No .drp found. Stacking all cameras (no cuts).", "warn")
        self.drp_path.set("")
        self.media_root.set(folder)
        self.out_dir.set(os.path.join(folder, "Trimmed"))
        self.proj_name.set(Path(folder).name)
        self.in_time.set("00:00:00:00")
        self.out_time.set("")
        self._drp_data = None
        self._manual_cams = dict(cams)

        probes = [probe_video(p) for p in cams.values()]
        fpss = [f for f, _ in probes if f]
        durs = [d for _, d in probes if d]
        fps_s = self.fps_ov.get().strip()
        self._fps = float(fps_s) if fps_s else (fpss[0] if fpss else 30.0)
        if not fps_s and not fpss:
            self._log("⚠ Couldn't probe fps (no ffprobe); assuming 30. "
                      "Set FPS Override if that's wrong.", "warn")
        if durs:
            s = int(min(durs))
            self.out_time.set(f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}:00")
        else:
            self._log("⚠ Couldn't read durations; type an Out time.", "warn")

        cam_nums = sorted(cams)
        cam_vals = [f"Camera {c}" for c in cam_nums]
        if self._audio_cam_combo:
            self._audio_cam_combo["values"] = cam_vals
            if self.audio_cam_var.get() not in cam_vals:
                self.audio_cam_var.set(cam_vals[0])

        self._info.config(text=f"No DRP · stacked mode · {self._fps} fps · "
                               f"Cameras {cam_nums}", fg=WARNING)
        lines = []
        for c in cam_nums:
            try: shown = os.path.relpath(cams[c], folder)
            except ValueError: shown = cams[c]
            lines.append(f"Camera {c} → {shown}")
        self._media_info.config(text="\n".join(lines), fg=SUCCESS)
        self._log(f"✓ Cameras found: {cam_nums}  ({info['layout']})", "ok")
        for ln in lines:
            self._log("  " + ln, "dim")
        self._log("• Camera 1 on V1, Camera 2 on V2, … all assumed to start in sync", "dim")

    def _browse_drp(self):
        p = filedialog.askopenfilename(
            title="Select .drp file",
            filetypes=[("DRP files","*.drp"),("All files","*.*")])
        if p:
            self.drp_path.set(p)
            parent = str(Path(p).parent)
            if not self.media_root.get(): self.media_root.set(parent)
            if not self.out_dir.get():    self.out_dir.set(parent)
            if not self.proj_name.get():  self.proj_name.set(Path(p).stem)
            self._load_drp(p)

    def _browse_media(self):
        p = filedialog.askdirectory(title="Media root folder")
        if p:
            self.media_root.set(p)
            if self._drp_data:
                self._report_media()

    def _browse_out(self):
        p = filedialog.askdirectory(title="Output folder")
        if p: self.out_dir.set(p)

    def _report_media(self):
        """Show which camera files were located for the current DRP + media root."""
        if not self._drp_data: return
        _, sources, _, _ = self._drp_data
        cam_idx = camera_index(sources)
        mmap = resolve_media_map(cam_idx, sources, self.media_root.get())
        cams = sorted(cam_idx.values())
        lines, missing = [], []
        for c in cams:
            if c in mmap:
                root = self.media_root.get()
                try: shown = os.path.relpath(mmap[c], root)
                except ValueError: shown = mmap[c]
                lines.append(f"Camera {c} → {shown}")
            else:
                missing.append(c)
        if missing:
            lines.append(f"⚠ Not found: Camera {', '.join(map(str, missing))}")
        self._media_info.config(text="\n".join(lines),
                                fg=WARNING if missing else SUCCESS)
        for ln in lines:
            self._log("  " + ln, "warn" if ln.startswith("⚠") else "dim")

    def _load_drp(self, path):
        self._log("Loading DRP…", "dim")
        self._manual_cams = None      # a real DRP overrides stacked mode
        try:
            data = parse_drp(path)
            self._drp_data = data
            records, sources, video_mode, meb_events = data
            fps_s = self.fps_ov.get().strip()
            self._fps = float(fps_s) if fps_s else fps_from_video_mode(video_mode)
            ff = tc_to_frames(records[0][0], self._fps)
            lf = tc_to_frames(records[-1][0], self._fps)
            total = lf - ff
            dur_s = total / self._fps
            h,m,s = int(dur_s//3600), int((dur_s%3600)//60), dur_s%60
            cams = sorted(set(camera_index(sources).values()))
            info = (f"Loaded · {self._fps} fps · Duration {h:02d}:{m:02d}:{s:05.2f} "
                    f"· Cameras {cams} · {len(records)} records")
            self._info.config(text=info, fg=SUCCESS)
            if cams and hasattr(self, "_audio_cam_combo") and self._audio_cam_combo:
                cam_vals = [f"Camera {c}" for c in cams]
                self._audio_cam_combo["values"] = cam_vals
                if self.audio_cam_var.get() not in cam_vals:
                    self.audio_cam_var.set(cam_vals[0])
            if not self.out_time.get():
                self.out_time.set(f"{h:02d}:{m:02d}:{int(s):02d}:00")
            self._log(f"✓ {info}", "ok")
            self._report_media()
        except Exception as ex:
            self._info.config(text=f"Error: {ex}", fg=ERROR)
            self._log(f"✗ {ex}", "err")

    # ── timecode section ──────────────────────────────────────────────────────

    def _build_timecode(self, parent):
        body = self._card(parent, "In / Out Window", "✂️")
        hint = "HH:MM:SS:FF  ·  HH:MM:SS  ·  MM:SS  ·  seconds"
        for label, var in [("In Time", self.in_time), ("Out Time", self.out_time)]:
            row = tk.Frame(body, bg=BG2); row.pack(fill="x", pady=4)
            self._lbl(row, label)
            self._entry(row, var, 20)
            tk.Label(row, text=hint, font=FONT_SMALL, bg=BG2, fg=TEXT_DIM).pack(side="left", padx=6)

        # presets
        pr = tk.Frame(body, bg=BG2); pr.pack(fill="x", pady=(6,0))
        tk.Label(pr, text="Quick Out preset:", font=FONT_SMALL,
                 bg=BG2, fg=TEXT_DIM).pack(side="left")
        for lbl, dur in [("5 min",300),("10 min",600),("15 min",900),("30 min",1800)]:
            FlatButton(pr, lbl, lambda d=dur: self._preset(d),
                       bg=BG3, fg=ACCENT2, font=FONT_SMALL,
                       padx=8, pady=3, hover_bg=ACCENT).pack(side="left", padx=4)

        self._dur_lbl = tk.Label(body, text="", font=FONT_SMALL, bg=BG2, fg=SUCCESS, anchor="w")
        self._dur_lbl.pack(fill="x", pady=(6,0))
        self.in_time.trace_add("write", self._update_dur)
        self.out_time.trace_add("write", self._update_dur)

    def _preset(self, dur_secs):
        try:
            fps = self._fps or 30.0
            inf = parse_time_input(self.in_time.get(), fps) if self.in_time.get() else 0
            outf = inf + int(dur_secs * fps)
            s = outf / fps
            h,m,sec = int(s//3600), int((s%3600)//60), s%60
            self.out_time.set(f"{h:02d}:{m:02d}:{int(sec):02d}:00")
        except Exception: pass

    def _update_dur(self, *_):
        try:
            fps = self._fps or 30.0
            inf  = parse_time_input(self.in_time.get(), fps)
            outf = parse_time_input(self.out_time.get(), fps)
            ds = (outf - inf) / fps
            if ds < 0:
                self._dur_lbl.config(text="⚠ Out must be after In", fg=WARNING); return
            h,m,s = int(ds//3600), int((ds%3600)//60), ds%60
            self._dur_lbl.config(text=f"Window: {h:02d}:{m:02d}:{s:05.2f}  ({ds:.1f} s)", fg=SUCCESS)
        except Exception:
            self._dur_lbl.config(text="", fg=TEXT_DIM)

    # ── options section ───────────────────────────────────────────────────────

    def _build_options(self, parent):
        body = self._card(parent, "Export Options", "⚙️")

        # format
        fr = tk.Frame(body, bg=BG2); fr.pack(fill="x", pady=4)
        self._lbl(fr, "Output Format")
        for val, lbl in [("premiere","Premiere XML  (.xml)"),("fcpxml","FCPXML  (.fcpxml)")]:
            tk.Radiobutton(fr, text=lbl, variable=self.fmt_var, value=val,
                           bg=BG2, fg=TEXT, selectcolor=ACCENT,
                           activebackground=BG2, font=FONT_BODY, cursor="hand2").pack(side="left", padx=8)

        # audio camera source
        ar = tk.Frame(body, bg=BG2); ar.pack(fill="x", pady=4)
        self._lbl(ar, "Audio Source")
        self._audio_cam_combo = ttk.Combobox(ar, textvariable=self.audio_cam_var,
                                             values=["Camera 1", "Camera 2", "Camera 3", "Camera 4"],
                                             state="readonly", width=14, font=FONT_BODY)
        self._audio_cam_combo.pack(side="left")
        tk.Label(ar, text="(sustained unbroken audio track on A1/A2)", font=FONT_SMALL,
                 bg=BG2, fg=TEXT_DIM).pack(side="left", padx=6)

        # trim footage
        tr = tk.Frame(body, bg=BG2); tr.pack(fill="x", pady=4)
        self._lbl(tr, "Trim Footage")
        tk.Checkbutton(tr, text="Cut camera files to In/Out window  (FFmpeg lossless stream-copy — fast, no re-encode)",
                       variable=self.trim_ftr, bg=BG2, fg=TEXT, selectcolor=ACCENT2,
                       activebackground=BG2, font=FONT_BODY, cursor="hand2",
                       command=self._trim_toggle).pack(side="left")
        self._ffwarn = tk.Label(body, text="", font=FONT_SMALL, bg=BG2, fg=WARNING, anchor="w")
        self._ffwarn.pack(fill="x")
        self._trim_toggle()

        # carry-forward
        cr = tk.Frame(body, bg=BG2); cr.pack(fill="x", pady=4)
        self._lbl(cr, "Graphics on Air")
        tk.Checkbutton(cr, text="Hold last camera during stills / black / media players",
                       variable=self.carry_fwd, bg=BG2, fg=TEXT, selectcolor=ACCENT2,
                       activebackground=BG2, font=FONT_BODY, cursor="hand2").pack(side="left")

        # fps override
        fp = tk.Frame(body, bg=BG2); fp.pack(fill="x", pady=4)
        self._lbl(fp, "FPS Override")
        self._entry(fp, self.fps_ov, 10)
        tk.Label(fp, text="(blank = auto-detect from DRP / ffprobe)", font=FONT_SMALL,
                 bg=BG2, fg=TEXT_DIM).pack(side="left", padx=6)

        # project name
        pn = tk.Frame(body, bg=BG2); pn.pack(fill="x", pady=4)
        self._lbl(pn, "Project Name")
        self._entry(pn, self.proj_name, 36)
        tk.Label(pn, text="(default: DRP filename / folder name)", font=FONT_SMALL,
                 bg=BG2, fg=TEXT_DIM).pack(side="left", padx=6)

    def _trim_toggle(self):
        if self.trim_ftr.get() and not ffmpeg_available():
            self._ffwarn.config(text="⚠  FFmpeg not found in PATH — install it to enable footage trimming")
        else:
            self._ffwarn.config(text="")

    # ── actions ───────────────────────────────────────────────────────────────

    def _build_actions(self, parent):
        row = tk.Frame(parent, bg=BG, pady=12)
        row.pack(fill="x", padx=20)
        self._exp_btn = FlatButton(row, "▶  Export", self._on_export,
                                   bg=ACCENT, fg="white",
                                   font=("Segoe UI", 12, "bold"),
                                   padx=28, pady=10)
        self._exp_btn.pack(side="left")
        self._prog = ttk.Progressbar(row, mode="indeterminate", length=240)
        self._prog.pack(side="left", padx=16)
        self._status = tk.Label(row, text="Ready", font=FONT_BODY, bg=BG, fg=TEXT_DIM)
        self._status.pack(side="left")
        s = ttk.Style(); s.theme_use("clam")
        s.configure("TProgressbar", troughcolor=BG3, background=ACCENT, borderwidth=0)
        s.configure("TCombobox", fieldbackground=BG3, background=BG3, foreground=TEXT,
                    arrowcolor=ACCENT2, bordercolor=BORDER, darkcolor=BG3, lightcolor=BG3)
        s.map("TCombobox", fieldbackground=[("readonly", BG3)], foreground=[("readonly", TEXT)])

    def _on_export(self):
        if not self.drp_path.get() and not self._manual_cams:
            messagebox.showerror("Missing", "Open a project folder or select a .drp file first."); return
        if not self.out_dir.get():
            messagebox.showerror("Missing", "Select an output folder."); return
        if self._drp_data is None and not self._manual_cams:
            self._load_drp(self.drp_path.get())
            if self._drp_data is None: return
        try:
            fps = float(self.fps_ov.get()) if self.fps_ov.get().strip() else self._fps
            inf  = parse_time_input(self.in_time.get(), fps)
            outf = parse_time_input(self.out_time.get(), fps)
        except Exception as ex:
            messagebox.showerror("Bad time", str(ex)); return
        if outf <= inf:
            messagebox.showerror("Bad range", "Out must be after In."); return
        try:
            os.makedirs(self.out_dir.get(), exist_ok=True)
        except OSError as ex:
            messagebox.showerror("Output folder", str(ex)); return
        self._exp_btn.set_enabled(False)
        self._prog.start(12)
        self._status.config(text="Working…", fg=ACCENT2)
        self._log("─"*60)
        self._log(f"▶ Export  In={self.in_time.get()}  Out={self.out_time.get()}")
        threading.Thread(target=self._do_export, args=(fps, inf, outf), daemon=True).start()

    def _do_export(self, fps, in_frame, out_frame):
        try:
            if self._manual_cams:
                # No DRP: every camera on its own track for the whole window.
                sources, cam_idx, media_map = synthetic_project(self._manual_cams)
                total_frames = out_frame - in_frame
                abs_in = in_frame
                # One segment where the top camera is "live" -> keep_ranges_for_track
                # keeps every lower camera visible for the entire window.
                norm = [(0, total_frames, max(cam_idx.values()))]
                clipped_dissolves = []
                self._ui(self._log, "No DRP: stacking all cameras, no cuts.", "dim")
            else:
                records, sources, vm, meb_events = self._drp_data
                segs, cam_idx = build_segments(records, sources, fps, self.carry_fwd.get())
                dissolves = extract_dissolves(meb_events, fps)
                first_f = segs[0][0]
                abs_in, abs_out = first_f+in_frame, first_f+out_frame
                norm = clip_segments(segs, abs_in, abs_out)
                if not norm:
                    self._ui(self._log, "✗ No segments in that window.", "err")
                    self._finish(False); return
                total_frames = out_frame - in_frame
                clipped_dissolves = [(tf,ff,oi,ii) for tf,ff,oi,ii in dissolves
                                     if tf>=abs_in and ff<=abs_out]
                media_map = None      # resolved below

            project_name = (self.proj_name.get().strip()
                            or (Path(self.drp_path.get()).stem if self.drp_path.get()
                                else Path(self.media_root.get()).name)
                            or "Project")
            out_dir  = self.out_dir.get()
            mroot    = self.media_root.get()
            fmt      = self.fmt_var.get()
            do_trim  = self.trim_ftr.get() and ffmpeg_available()
            in_sec, out_sec = in_frame/fps, out_frame/fps
            name_to_idx = {v: k for k, v in cam_idx.items()}

            # locate each camera file (works for subfolder or sibling layouts)
            if media_map is None:
                media_map = resolve_media_map(cam_idx, sources, mroot)

            if do_trim:
                self._ui(self._log, f"Trimming footage  {in_sec:.2f}s → {out_sec:.2f}s …", "dim")
                for cam in sorted(cam_idx.values()):
                    src = cam_file(cam, sources, name_to_idx, mroot, media_map)
                    base = os.path.splitext(os.path.basename(src))[0]
                    dst = os.path.join(out_dir, f"{base}_trimmed.mp4")
                    if not os.path.exists(src):
                        self._ui(self._log, f"  ⚠ Cam {cam}: not found → {src}", "warn"); continue
                    self._ui(self._log, f"  → Cam {cam}: {os.path.basename(src)}", "dim")
                    try:
                        trim_video(src, dst, in_sec, out_sec,
                                   log_cb=lambda m: self._ui(self._log, "    "+m, "dim"))
                        sz = os.path.getsize(dst)/1024/1024
                        self._ui(self._log, f"    ✓ {os.path.basename(dst)}  ({sz:.1f} MB)", "ok")
                    except Exception as ex:
                        self._ui(self._log, f"    ✗ FFmpeg: {ex}", "err")
                xml_media_root = out_dir
            else:
                xml_media_root = mroot
                if self._manual_cams and in_frame > 0:
                    self._ui(self._log,
                             "⚠ In ≠ 0 with Trim Footage off: clips will point at the wrong "
                             "part of the original files. Enable Trim Footage.", "warn")

            # parse chosen audio camera
            audio_cam_str = self.audio_cam_var.get()
            try:
                audio_cam_num = int("".join(filter(str.isdigit, audio_cam_str)))
            except Exception:
                audio_cam_num = sorted(cam_idx.values())[0]
            if audio_cam_num not in cam_idx.values():
                audio_cam_num = sorted(cam_idx.values())[0]
            self._ui(self._log, f"Audio source: Camera {audio_cam_num} (unbroken track on A1/A2)", "dim")

            self._ui(self._log, f"Building {fmt.upper()} XML…", "dim")
            if fmt == "fcpxml":
                ext = ".fcpxml"
                xml_c = build_fcpxml(norm, sources, cam_idx, fps, xml_media_root,
                                     project_name, total_frames, clipped_dissolves,
                                     do_trim, abs_in, audio_cam=audio_cam_num,
                                     media_map=media_map)
            else:
                ext = ".xml"
                xml_c = build_premiere_xml(norm, sources, cam_idx, fps, xml_media_root,
                                           project_name, total_frames, clipped_dissolves,
                                           do_trim, abs_in, audio_cam=audio_cam_num,
                                           media_map=media_map)

            out_file = os.path.join(out_dir, f"{project_name}_trimmed{ext}")
            with open(out_file, "w", encoding="utf-8") as f: f.write(xml_c)
            sz = os.path.getsize(out_file)/1024
            self._ui(self._log, f"✓ XML: {out_file}  ({sz:.1f} KB)", "ok")
            self._ui(self._log,
                     f"✓ Done!  {total_frames} frames  ({total_frames/fps:.1f}s)  Cameras: {sorted(cam_idx.values())}",
                     "ok")
            self._finish(True)
        except Exception as ex:
            import traceback
            self._ui(self._log, f"✗ {ex}", "err")
            self._ui(self._log, traceback.format_exc(), "err")
            self._finish(False)

    def _ui(self, fn, *a, **kw):
        self.root.after(0, fn, *a, **kw)

    def _finish(self, ok):
        def _done():
            self._prog.stop()
            self._exp_btn.set_enabled(True)
            if ok:
                self._status.config(text="✓ Done", fg=SUCCESS)
                messagebox.showinfo("Export complete", f"Files saved to:\n{self.out_dir.get()}")
            else:
                self._status.config(text="✗ Failed", fg=ERROR)
        self.root.after(0, _done)

    # ── log ───────────────────────────────────────────────────────────────────

    def _build_log(self, parent):
        body = self._card(parent, "Log", "📋")
        body.pack_configure(pady=(0,20))
        self._log_w = tk.Text(body, height=10, bg="#0a0c12", fg="#9da8c0",
                              font=FONT_MONO, relief="flat",
                              highlightbackground=BORDER, highlightthickness=1,
                              state="disabled", wrap="word")
        self._log_w.pack(fill="both", expand=True)
        self._log_w.tag_config("ok",   foreground=SUCCESS)
        self._log_w.tag_config("warn", foreground=WARNING)
        self._log_w.tag_config("err",  foreground=ERROR)
        self._log_w.tag_config("dim",  foreground=TEXT_DIM)
        FlatButton(body, "Clear", self._log_clear, bg=BG3, fg=TEXT_DIM,
                   font=FONT_SMALL, padx=8, pady=3).pack(anchor="e", pady=(4,0))

    def _log(self, msg, tag=None):
        w = self._log_w
        w.configure(state="normal")
        w.insert("end", msg+"\n", tag or "")
        w.see("end")
        w.configure(state="disabled")

    def _log_clear(self):
        self._log_w.configure(state="normal")
        self._log_w.delete("1.0","end")
        self._log_w.configure(state="disabled")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()