#!/usr/bin/env python3
"""
drp_to_stacked_xml.py

Converts a Blackmagic ATEM switcher recording log (.drp — newline-delimited
JSON of switcher state) into a stacked-camera-track timeline you can import
into either DaVinci Resolve (FCPXML) or Premiere Pro / Resolve (the classic
Premiere-compatible "Final Cut Pro 7 XML" / XMEML), instead of a single
sequentially-cut track:

    V1 = Camera 1 (bottom, full length, unbroken)
    V2 = Camera 2 (full length, with the moments Camera 1 was live cut out)
    V3 = Camera 3 (full length, with the moments Camera 1 or 2 were live cut out)
    ...
    VN = Camera N (full length, with every moment a lower camera number
         was live cut out)

The rule: for a track to show camera M, every track ABOVE it must have a
gap wherever a LOWER-numbered camera was the one actually selected on the
switcher. That gap is what lets the layer below "punch through" — exactly
the editing style described: build every camera as its own full track,
then remove the layers above to reveal the one that should be showing.

Track 1 (the lowest camera number used) therefore ends up as one
unbroken clip — nothing is ever below it, so it's never punched with
holes. Every track above it is only ever present during the spans where
it (or something even higher) was actually live.

USAGE
-----
    # For Resolve (or FCPX):
    python3 drp_to_stacked_xml.py input.drp output.fcpxml \
        --media-root "/path/to/09202026_2nd Service"

    # For Premiere Pro (or Resolve, both read this format too):
    python3 drp_to_stacked_xml.py input.drp output.xml \
        --media-root "/path/to/09202026_2nd Service"

Format is picked from the output extension (.fcpxml vs .xml), or force it
with --format fcpxml / --format premiere.

In Premiere: File > Import... and pick the .xml, or drag it into a bin.
In Resolve: File > Import > Timeline... and pick either file.
It'll come in as a new timeline/sequence with camera 1..N stacked on
V1..VN, gaps already punched.

NOTES / ASSUMPTIONS (read before you run this on a real service)
------------------------------------------------------------------
* Frame rate is parsed from the .drp's "videoMode" (e.g. "1080p30" -> 30).
  If your show is actually 29.97 drop-frame, pass --fps 29.97 explicitly.
* All camera ISO files are assumed to share the SAME embedded start
  timecode as the switcher's masterTimecode at record start (this is
  true for genlocked/timecode-synced multicam ISO recordings, which is
  what the .drp's "startTimecode" field on each camera source confirms).
  If your cameras are NOT synced this way, the in-points will be wrong
  and you'll need to nudge each track manually after import.
* Non-camera sources going to air (Black, Media Player/stills, Color
  Bars, Color generators) are treated as "no change" — the stack keeps
  showing whatever real camera was live before the graphic, on the
  assumption your bumpers/lower-thirds/logo already live on a separate
  overlay track above this whole stack (like your current V3/V4). Pass
  --no-carry-forward if you'd rather those spans just go to a gap on
  every camera track instead.
* Camera files are matched to the switcher log by camera number, e.g.
  "Camera 3" -> Video ISO Files/<...> CAM 3 <NN>.mp4, taken verbatim
  from the "file" path recorded in the .drp's sources list, joined onto
  --media-root. If Resolve shows clips offline after import, just
  relink the bin — file names will still match.
"""

import argparse
import json
import os
import sys
from fractions import Fraction


# --------------------------------------------------------------------------
# .drp parsing
# --------------------------------------------------------------------------

def parse_drp(path):
    """Read the newline-delimited JSON .drp log and reconstruct, frame by
    frame, which source was on program (mixEffectBlocks[0].source), plus
    the full (merged) source table (camera names/files), plus every
    transition event (dissolve start/end) so real crossfades aren't
    collapsed into hard cuts.

    IMPORTANT quirk of this log format: mixEffectBlocks[0].source only
    updates to the NEW camera once a running transition *completes*
    (transitionActive flips back to False). While transitionActive is
    True, "source" still reports the OUTGOING camera. So a naive read of
    "source" alone treats every dissolve as an instantaneous hard cut at
    the end of the fade -- that's the bug from the first version of this
    script. We track transitionActive/transitionType alongside source so
    real Mix dissolves can be rebuilt as an actual crossfade instead.
    """
    sources = {}          # idx -> merged source dict
    cur_meb = {}           # running merged state of mixEffectBlocks[0]
    records = []          # (masterTimecode str, on-air source idx)
    meb_events = []        # (masterTimecode str, merged meb0 dict) whenever it changes
    video_mode = None

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)

            if "videoMode" in obj:
                video_mode = obj["videoMode"]

            if "sources" in obj:
                for s in obj["sources"]:
                    idx = s["_index_"]
                    sources.setdefault(idx, {}).update(s)

            meb = obj.get("mixEffectBlocks")
            if meb and len(meb) > 0:
                changed = {
                    k: v for k, v in meb[0].items()
                    if k in ("source", "transitionActive", "transitionType")
                }
                if changed:
                    cur_meb.update(changed)
                    meb_events.append((obj["masterTimecode"], dict(cur_meb)))

            records.append((obj["masterTimecode"], cur_meb.get("source")))

    return records, sources, video_mode, meb_events


def extract_dissolves(meb_events, fps, lookahead=10):
    """Pair up transitionActive True -> False brackets into dissolve
    events: (start_frame, end_frame, outgoing_source_idx, incoming_source_idx).
    Only 'Mix' transitions are returned (Wipe/DVE stingers are left as
    hard cuts for now -- reproducing those shapes isn't attempted here)."""
    dissolves = []
    n = len(meb_events)
    for i in range(n):
        tc, c = meb_events[i]
        if c.get("transitionActive") is not True:
            continue
        outgoing = c.get("source")
        ttype = c.get("transitionType")
        for j in range(i + 1, min(i + 1 + lookahead, n)):
            tc2, c2 = meb_events[j]
            if c2.get("transitionActive") is False:
                incoming = c2.get("source")
                if ttype == "Mix" and incoming is not None and incoming != outgoing:
                    dissolves.append((
                        tc_to_frames(tc, fps),
                        tc_to_frames(tc2, fps),
                        outgoing,
                        incoming,
                    ))
                break
    return dissolves


def fps_from_video_mode(video_mode):
    """'1080p30' -> 30.0, '1080p59.94' -> 59.94, '1080p29.97' -> 29.97 ..."""
    if not video_mode:
        return 30.0
    digits = ""
    for ch in reversed(video_mode):
        if ch.isdigit() or ch == ".":
            digits = ch + digits
        else:
            break
    try:
        return float(digits)
    except ValueError:
        return 30.0


def tc_to_frames(tc, fps):
    h, m, s, f = (int(x) for x in tc.split(":"))
    return int(round(((h * 3600) + (m * 60) + s) * fps)) + f


# --------------------------------------------------------------------------
# Build segments: contiguous frame ranges with a single "effective" active
# camera number, after resolving non-camera sources via carry-forward.
# --------------------------------------------------------------------------

def build_segments(records, sources, fps, carry_forward_non_camera=True):
    camera_of_source_idx = {}
    for idx, s in sources.items():
        name = s.get("name", "")
        if s.get("type") == "Video" and name.startswith("Camera "):
            try:
                camera_of_source_idx[idx] = int(name.split("Camera ")[1])
            except ValueError:
                pass

    frames = [(tc_to_frames(tc, fps), src) for tc, src in records]

    last_camera = None
    effective = []
    for f, src in frames:
        cam = camera_of_source_idx.get(src)
        if cam is None:
            if carry_forward_non_camera and last_camera is not None:
                cam = last_camera
            else:
                cam = None  # explicit "no camera" gap
        else:
            last_camera = cam
        effective.append((f, cam))

    segments = []
    cur_cam = effective[0][1]
    seg_start = effective[0][0]
    for i in range(1, len(effective)):
        f, cam = effective[i]
        if cam != cur_cam:
            segments.append((seg_start, f, cur_cam))
            seg_start = f
            cur_cam = cam
    segments.append((seg_start, effective[-1][0], cur_cam))

    return segments, camera_of_source_idx


# --------------------------------------------------------------------------
# For each camera track, compute the "keep" ranges: spans where that
# camera is the one on air, OR a higher-numbered camera is on air (in
# which case this track is hidden underneath it anyway, so it's fine
# and simpler to leave the footage in place). Everything else becomes
# a hole (no clip placed).
# --------------------------------------------------------------------------

def keep_ranges_for_track(segments, track_cam, first_frame, last_frame):
    ranges = []
    cur_start = None
    for start, end, active_cam in segments:
        show = (active_cam is not None) and (active_cam >= track_cam)
        if show and cur_start is None:
            cur_start = start
        if not show and cur_start is not None:
            ranges.append((cur_start, start))
            cur_start = None
    if cur_start is not None:
        ranges.append((cur_start, last_frame))
    return ranges


# --------------------------------------------------------------------------
# FCPXML generation
# --------------------------------------------------------------------------

def frac_time(frames, fps_num, fps_den):
    """Return an FCPXML rational time string 'N/Ds' for a frame count."""
    t = Fraction(frames * fps_den, fps_num)
    return f"{t.numerator}/{t.denominator}s"


def xml_escape(s):
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_fcpxml(segments, sources, camera_of_source_idx, fps, media_root,
                  project_name, dissolves=None):
    cameras = sorted(camera_of_source_idx.values())
    if not cameras:
        raise SystemExit("No 'Camera N' sources found in this .drp file.")

    first_frame = segments[0][0]
    last_frame = segments[-1][1]
    total_frames = last_frame - first_frame

    # normalize segments to be relative to first_frame, and drop the
    # camera reference back down to source-idx -> file path
    name_to_idx = {v: k for k, v in camera_of_source_idx.items()}
    norm_segments = [
        (s - first_frame, e - first_frame, cam) for s, e, cam in segments
    ]

    if float(fps).is_integer():
        fps_num, fps_den = int(fps), 1
    else:
        # e.g. 29.97 -> 30000/1001, 59.94 -> 60000/1001
        fps_num, fps_den = int(round(fps * 1000)), 1000
        # reduce
        from math import gcd
        g = gcd(fps_num, fps_den)
        fps_num, fps_den = fps_num // g, fps_den // g

    frame_duration = f"{fps_den}/{fps_num}s"

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append("<!DOCTYPE fcpxml>")
    lines.append('<fcpxml version="1.9">')
    lines.append("  <resources>")
    lines.append(
        f'    <format id="r1" name="FFVideoFormatCustom" '
        f'frameDuration="{frame_duration}" width="1920" height="1080"/>'
    )

    asset_id_of_cam = {}
    for i, cam in enumerate(cameras, start=1):
        idx = name_to_idx[cam]
        s = sources.get(idx, {})
        rel_file = s.get("file", f"CAM {cam}.mp4")
        full_path = os.path.join(media_root, rel_file) if media_root else rel_file
        # Build a file:// URL, keeping it simple/portable.
        url_path = full_path.replace(os.sep, "/")
        if not url_path.startswith("/"):
            url_path = "/" + url_path
        file_url = "file://" + url_path.replace(" ", "%20")
        aid = f"a{i}"
        asset_id_of_cam[cam] = aid
        dur = frac_time(total_frames, fps_num, fps_den)
        lines.append(
            f'    <asset id="{aid}" name="{xml_escape(s.get("name","Camera %d"%cam))}" '
            f'src="{xml_escape(file_url)}" start="0s" duration="{dur}" '
            f'hasVideo="1" hasAudio="1" format="r1"/>'
        )
    # A generic cross-dissolve transition resource. Resolve's FCPXML
    # importer doesn't need a real Motion template behind this -- a
    # <transition> element in the timeline is enough for it to insert its
    # own native Cross Dissolve of the matching duration. Worst case, if
    # it isn't recognized, it lands as a plain edit point you can select
    # and apply Resolve's default transition to in one click.
    lines.append(
        '    <effect id="trDissolve" name="Cross Dissolve" '
        'uid=".../Transitions.localized/Dissolves.localized/'
        'Cross Dissolve.localized/Cross Dissolve.moti"/>'
    )
    lines.append("  </resources>")

    lines.append("  <library>")
    lines.append(f'    <event name="{xml_escape(project_name)}">')
    lines.append(f'      <project name="{xml_escape(project_name)}">')
    total_dur = frac_time(total_frames, fps_num, fps_den)
    lines.append(
        f'        <sequence format="r1" duration="{total_dur}" tcStart="0s">'
    )
    lines.append("          <spine>")

    bottom_cam = cameras[0]
    bottom_aid = asset_id_of_cam[bottom_cam]
    lines.append(
        f'            <asset-clip ref="{bottom_aid}" offset="0s" '
        f'name="Camera {bottom_cam}" start="0s" duration="{total_dur}" format="r1">'
    )

    # every camera above the bottom one is a connected (lane) clip,
    # nested inside the bottom clip, only present in its "keep" ranges
    for lane, cam in enumerate(cameras[1:], start=1):
        aid = asset_id_of_cam[cam]
        ranges = keep_ranges_for_track(norm_segments, cam, 0, total_frames)
        for start, end in ranges:
            if end <= start:
                continue
            off = frac_time(start, fps_num, fps_den)
            stt = frac_time(start, fps_num, fps_den)
            dur = frac_time(end - start, fps_num, fps_den)
            # hasAudio="0" — only the bottom Cam 1 spine clip carries audio
            lines.append(
                f'              <asset-clip ref="{aid}" lane="{lane}" '
                f'offset="{off}" name="Camera {cam}" start="{stt}" '
                f'duration="{dur}" format="r1" hasAudio="0"/>'
            )

    # Dissolve overlay: one lane above every camera track, used ONLY
    # during the ~1 second windows where the switcher actually did a Mix
    # (crossfade) instead of a hard cut. A transition between two
    # different cameras can't be expressed as a plain gap in the stack
    # (that only reproduces hard cuts), so those specific windows get a
    # real two-clip + <transition> crossfade sitting on top of everything
    # else. Outside those windows this lane is empty and the normal
    # camera stack underneath shows through exactly as before.
    dissolve_lane = len(cameras)
    skipped = 0
    if dissolves:
        for true_f, false_f, out_idx, in_idx in dissolves:
            out_cam = camera_of_source_idx.get(out_idx)
            in_cam = camera_of_source_idx.get(in_idx)
            if out_cam is None or in_cam is None:
                skipped += 1
                continue
            t0 = true_f - first_frame
            t1 = false_f - first_frame
            if t1 <= t0 or t0 < 0 or t1 > total_frames:
                continue
            mid = t0 + (t1 - t0) // 2
            a_id = asset_id_of_cam[out_cam]
            b_id = asset_id_of_cam[in_cam]
            off0 = frac_time(t0, fps_num, fps_den)
            offmid = frac_time(mid, fps_num, fps_den)
            durA = frac_time(mid - t0, fps_num, fps_den)
            durB = frac_time(t1 - mid, fps_num, fps_den)
            durT = frac_time(t1 - t0, fps_num, fps_den)
            lines.append(
                f'              <asset-clip ref="{a_id}" lane="{dissolve_lane}" '
                f'offset="{off0}" name="Camera {out_cam} (out)" start="{off0}" '
                f'duration="{durA}" format="r1"/>'
            )
            lines.append(
                f'              <transition name="Cross Dissolve" '
                f'offset="{off0}" duration="{durT}">'
            )
            lines.append('                <filter-video ref="trDissolve"/>')
            lines.append('              </transition>')
            lines.append(
                f'              <asset-clip ref="{b_id}" lane="{dissolve_lane}" '
                f'offset="{offmid}" name="Camera {in_cam} (in)" start="{offmid}" '
                f'duration="{durB}" format="r1"/>'
            )

    lines.append("            </asset-clip>")
    lines.append("          </spine>")
    lines.append("        </sequence>")
    lines.append("      </project>")
    lines.append("    </event>")
    lines.append("  </library>")
    lines.append("</fcpxml>")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Premiere Pro compatible XML generation (the classic "Final Cut Pro 7 XML
# Interchange Format" / XMEML — this is the format Premiere Pro itself
# exports/imports as "Premiere XML", NOT Apple's newer FCPXML above. Resolve
# reads this format too, so the same file works for either app.
#
# Same track-stacking logic as the FCPXML build: track order = V1 (bottom,
# lowest camera number) up to VN (top). Gaps are simply the absence of a
# clipitem over that span — no explicit "gap" element needed. Dissolves get
# their own top track with a real <transitionitem>, since a crossfade
# between two different camera tracks can't be expressed any other way.
# --------------------------------------------------------------------------

def build_premiere_xml(segments, sources, camera_of_source_idx, fps,
                        media_root, project_name, dissolves=None):
    cameras = sorted(camera_of_source_idx.values())
    if not cameras:
        raise SystemExit("No 'Camera N' sources found in this .drp file.")

    first_frame = segments[0][0]
    last_frame = segments[-1][1]
    total_frames = last_frame - first_frame
    name_to_idx = {v: k for k, v in camera_of_source_idx.items()}
    norm_segments = [
        (s - first_frame, e - first_frame, cam) for s, e, cam in segments
    ]

    # NTSC-style rates (29.97, 59.94, 23.976) use a rounded integer
    # timebase with ntsc=TRUE; clean rates (25, 30, 50, 60) use ntsc=FALSE.
    is_ntsc = abs(fps - round(fps)) > 0.001
    timebase = int(round(fps))

    def rate_block(indent):
        pad = " " * indent
        return (
            f"{pad}<rate>\n{pad}  <timebase>{timebase}</timebase>\n"
            f"{pad}  <ntsc>{'TRUE' if is_ntsc else 'FALSE'}</ntsc>\n{pad}</rate>"
        )

    file_urls = {}
    for cam in cameras:
        idx = name_to_idx[cam]
        s = sources.get(idx, {})
        rel_file = s.get("file", f"CAM {cam}.mp4")
        full_path = os.path.join(media_root, rel_file) if media_root else rel_file
        url_path = full_path.replace(os.sep, "/")
        if not url_path.startswith("/"):
            url_path = "/" + url_path
        file_urls[cam] = "file://" + url_path.replace(" ", "%20")

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append("<!DOCTYPE xmeml>")
    lines.append('<xmeml version="5">')
    lines.append("  <sequence>")
    lines.append(f"    <name>{xml_escape(project_name)}</name>")
    lines.append(f"    <duration>{total_frames}</duration>")
    lines.append(rate_block(4))
    lines.append("    <media>")
    lines.append("      <video>")
    lines.append("        <format>")
    lines.append("          <samplecharacteristics>")
    lines.append(rate_block(12))
    lines.append("            <width>1920</width>")
    lines.append("            <height>1080</height>")
    lines.append("          </samplecharacteristics>")
    lines.append("        </format>")

    file_defined = set()

    bottom_cam = cameras[0]   # Camera 1 — sole audio source

    def file_block(cam, indent, audio=False):
        """Emit a <file> reference. On first use, includes full metadata.
        audio=True emits <media><video/><audio/></media> (for Cam 1 A1 track).
        """
        pad = " " * indent
        fid = f"file-cam{cam}"
        if fid in file_defined:
            return f'{pad}<file id="{fid}"/>'
        file_defined.add(fid)
        idx = name_to_idx[cam]
        name = sources.get(idx, {}).get("name", f"Camera {cam}")
        # Cam 1 file carries both video and audio; all others video-only.
        media_tag = "<video/><audio/>" if cam == bottom_cam else "<video/>"
        out = [f'{pad}<file id="{fid}">']
        out.append(f"{pad}  <name>{xml_escape(name)}</name>")
        out.append(f"{pad}  <pathurl>{xml_escape(file_urls[cam])}</pathurl>")
        out.append(rate_block(len(pad) + 2))
        out.append(f"{pad}  <duration>{total_frames}</duration>")
        out.append(f"{pad}  <media>{media_tag}</media>")
        out.append(f"{pad}</file>")
        return "\n".join(out)

    clip_counter = [0]

    def clipitem(cam, start, end, indent, name_suffix=""):
        clip_counter[0] += 1
        pad = " " * indent
        cid = f"clipitem-{clip_counter[0]}"
        idx = name_to_idx[cam]
        name = sources.get(idx, {}).get("name", f"Camera {cam}") + name_suffix
        out = [f'{pad}<clipitem id="{cid}">']
        out.append(f"{pad}  <name>{xml_escape(name)}</name>")
        out.append(f"{pad}  <duration>{total_frames}</duration>")
        out.append(rate_block(len(pad) + 2))
        out.append(f"{pad}  <start>{start}</start>")
        out.append(f"{pad}  <end>{end}</end>")
        out.append(f"{pad}  <in>{start}</in>")
        out.append(f"{pad}  <out>{end}</out>")
        out.append(file_block(cam, len(pad) + 2))
        out.append(f"{pad}</clipitem>")
        return "\n".join(out)

    def audio_clipitem(cam, start, end, indent, channel, name_suffix=""):
        """Build an audio-only clipitem for the <audio> track section."""
        clip_counter[0] += 1
        pad = " " * indent
        cid = f"clipitem-{clip_counter[0]}"
        idx = name_to_idx[cam]
        name = sources.get(idx, {}).get("name", f"Camera {cam}") + name_suffix
        out = [f'{pad}<clipitem id="{cid}">']
        out.append(f"{pad}  <name>{xml_escape(name)}</name>")
        out.append(f"{pad}  <duration>{total_frames}</duration>")
        out.append(rate_block(len(pad) + 2))
        out.append(f"{pad}  <start>{start}</start>")
        out.append(f"{pad}  <end>{end}</end>")
        out.append(f"{pad}  <in>{start}</in>")
        out.append(f"{pad}  <out>{end}</out>")
        out.append(f"{pad}  <channelcount>2</channelcount>")
        out.append(file_block(cam, len(pad) + 2))
        # link to the audio channel
        out.append(f"{pad}  <sourcetrack>")
        out.append(f"{pad}    <mediatype>audio</mediatype>")
        out.append(f"{pad}    <trackindex>{channel}</trackindex>")
        out.append(f"{pad}  </sourcetrack>")
        out.append(f"{pad}</clipitem>")
        return "\n".join(out)

    # track 1 = bottom = lowest camera number, one unbroken clip (video)
    lines.append("        <track>")
    lines.append(clipitem(bottom_cam, 0, total_frames, 10))
    lines.append("        </track>")

    # each higher camera gets its own track, clips only in its keep ranges
    for cam in cameras[1:]:
        lines.append("        <track>")
        ranges = keep_ranges_for_track(norm_segments, cam, 0, total_frames)
        for start, end in ranges:
            if end <= start:
                continue
            lines.append(clipitem(cam, start, end, 10))
        lines.append("        </track>")

    # top track: dissolve crossfades only, empty everywhere else
    if dissolves:
        track_lines = ["        <track>"]
        has_any = False
        for true_f, false_f, out_idx, in_idx in dissolves:
            out_cam = camera_of_source_idx.get(out_idx)
            in_cam = camera_of_source_idx.get(in_idx)
            if out_cam is None or in_cam is None:
                continue
            t0 = true_f - first_frame
            t1 = false_f - first_frame
            if t1 <= t0 or t0 < 0 or t1 > total_frames:
                continue
            has_any = True
            mid = t0 + (t1 - t0) // 2
            track_lines.append(clipitem(out_cam, t0, mid, 10, " (out)"))
            track_lines.append("          <transitionitem>")
            track_lines.append(f"            <start>{t0}</start>")
            track_lines.append(f"            <end>{t1}</end>")
            track_lines.append("            <alignment>center</alignment>")
            track_lines.append("            <effect>")
            track_lines.append("              <name>Cross Dissolve</name>")
            track_lines.append("              <effectid>Cross Dissolve</effectid>")
            track_lines.append("              <effectcategory>Dissolve</effectcategory>")
            track_lines.append("              <effecttype>transition</effecttype>")
            track_lines.append("              <mediatype>video</mediatype>")
            track_lines.append("            </effect>")
            track_lines.append("          </transitionitem>")
            track_lines.append(clipitem(in_cam, mid, t1, 10, " (in)"))
        track_lines.append("        </track>")
        if has_any:
            lines.extend(track_lines)

    lines.append("      </video>")

    # ── Audio section ────────────────────────────────────────────────────────
    # A1 + A2 = Camera 1 audio, full unbroken duration. No other camera audio
    # is included — Cam 1 ISO carries the program mix for the whole service.
    lines.append("      <audio>")
    lines.append("        <format>")
    lines.append("          <samplecharacteristics>")
    lines.append("            <depth>16</depth>")
    lines.append("            <samplerate>48000</samplerate>")
    lines.append("          </samplecharacteristics>")
    lines.append("        </format>")
    # Two mono tracks (L + R) for the stereo pair from Camera 1
    for ch in (1, 2):
        lines.append("        <track>")
        lines.append(audio_clipitem(bottom_cam, 0, total_frames, 10, channel=ch))
        lines.append("        </track>")
    lines.append("      </audio>")

    lines.append("    </media>")
    lines.append("  </sequence>")
    lines.append("</xmeml>")

    return "\n".join(lines)


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("drp_file", help="input .drp switcher log")
    ap.add_argument("xml_out", help="output timeline file to import "
                                     "(.fcpxml for Resolve/FCPX, .xml for Premiere Pro)")
    ap.add_argument("--format", choices=["fcpxml", "premiere"], default=None,
                     help="force output format instead of guessing from the "
                          "output file's extension")
    ap.add_argument("--media-root", default="",
                     help="folder that contains 'Video ISO Files/...' "
                          "(joined onto each camera's recorded file path)")
    ap.add_argument("--fps", type=float, default=None,
                     help="override frame rate instead of reading it from videoMode")
    ap.add_argument("--no-carry-forward", action="store_true",
                     help="treat graphics/black/stills on-air as a gap on "
                          "every camera track instead of holding the last camera")
    ap.add_argument("--project-name", default=None,
                     help="name for the sequence/project (default: input filename)")
    args = ap.parse_args()

    fmt = args.format
    if fmt is None:
        ext = os.path.splitext(args.xml_out)[1].lower()
        fmt = "fcpxml" if ext == ".fcpxml" else "premiere"

    records, sources, video_mode, meb_events = parse_drp(args.drp_file)
    fps = args.fps or fps_from_video_mode(video_mode)

    segments, camera_of_source_idx = build_segments(
        records, sources, fps, carry_forward_non_camera=not args.no_carry_forward
    )
    dissolves = extract_dissolves(meb_events, fps)

    project_name = args.project_name or os.path.splitext(
        os.path.basename(args.drp_file))[0]

    if fmt == "fcpxml":
        xml = build_fcpxml(segments, sources, camera_of_source_idx, fps,
                            args.media_root, project_name, dissolves=dissolves)
    else:
        xml = build_premiere_xml(segments, sources, camera_of_source_idx, fps,
                                  args.media_root, project_name, dissolves=dissolves)

    with open(args.xml_out, "w") as f:
        f.write(xml)

    cams = sorted(camera_of_source_idx.values())
    total_frames = segments[-1][1] - segments[0][0]
    print(f"format: {fmt}")
    print(f"fps: {fps}")
    print(f"cameras found: {cams}  (V1={cams[0]} bottom ... V{len(cams)}={cams[-1]} top)")
    print(f"program segments parsed: {len(segments)}")
    print(f"dissolves detected (Mix transitions): {len(dissolves)} "
          f"-> reproduced as real crossfades on an overlay track")
    print(f"timeline length: {total_frames} frames (~{total_frames/fps/60:.1f} min)")
    print(f"wrote: {args.xml_out}")


if __name__ == "__main__":
    main()
