"""Where the crop sits, frame by frame — the camera operator, not the detector.

Split out of main.py so it can be imported without mediapipe, torch,
ultralytics and scenedetect. That is not tidiness: tests/test_speaker_tracker.py
opens with ``pytest.importorskip("main")``, and CI installs none of that stack,
so every regression test written for this logic — including the one guarding
the 26-jul-2026 swing fix — has never actually run anywhere but a dev machine.
The same reasoning already put clip_selection, active_speaker, punch_in,
split_layout and panel_layout in their own modules.

Two objects, two jobs:

  SmoothedCameraman — given a target, decides where the crop actually is this
  frame. Owns all the smoothing.
  SpeakerTracker    — given the frame's face candidates, decides which one is
  the target. Owns identity and switching.

Both are pure state machines over numbers. Nothing here decodes a frame.
"""
import os

ASPECT_RATIO = 9 / 16

# Consecutive detections a large target move must survive before the camera
# follows it (see SmoothedCameraman.update_target). Env-overridable so the
# damping can be dialled back without a deploy; 1 restores the old behaviour.
JUMP_CONFIRM_FRAMES = max(int(os.environ.get("JUMP_CONFIRM_FRAMES", "3")), 1)


class SmoothedCameraman:
    """
    Handles smooth camera movement.
    Simplified Logic: "Heavy Tripod"
    Only moves if the subject leaves the center safe zone.
    Moves slowly and linearly.
    """
    def __init__(self, output_width, output_height, video_width, video_height, aspect_ratio=ASPECT_RATIO):
        self.output_width = output_width
        self.output_height = output_height
        self.video_width = video_width
        self.video_height = video_height
        self.aspect_ratio = aspect_ratio

        # Initial State
        self.current_center_x = video_width / 2
        self.target_center_x = video_width / 2

        # Calculate crop dimensions once
        self.crop_height = video_height
        self.crop_width = int(self.crop_height * aspect_ratio)
        if self.crop_width > video_width:
             self.crop_width = video_width
             self.crop_height = int(self.crop_width / aspect_ratio)

        # Safe Zone: 20% of the video width
        # As long as the target is within this zone relative to current center, DO NOT MOVE.
        self.safe_zone_radius = self.crop_width * 0.25

        # A target that teleports further than the safe zone in one detection is
        # far more often a detector error — a second face, a false positive, a
        # box snapping to a different body part — than a person who actually
        # moved that far. Committing to it immediately is what made the camera
        # swing: measured on real user footage, 22% of target updates jumped
        # more than the entire safe zone. So a big move has to REPEAT this many
        # times before the camera follows it; a wrong reading disappears on the
        # next detection and never moves the frame.
        #
        # The cost is latency on a genuinely fast move: at DETECT_STRIDE=4 and
        # 30fps, three confirmations is ~0.4s. That reads as an operator being
        # unhurried, which is the look we want, and it is far cheaper than the
        # whip-panning it replaces.
        #
        # Measured over 262s of TRACK footage from two real user videos
        # (26-jul-2026), confirm=1 -> 3: in-scene reversals 0.41/s -> 0.13/s
        # (-69%), camera travel 91px/s -> 60px/s (-34%). Per scene, 54 of 84 get
        # calmer and 23 are unchanged — but 7 get BUSIER, up to 59 -> 108px/s,
        # because committing later can leave the camera further to travel. Net
        # strongly positive, not universally so.
        self.jump_confirm_frames = JUMP_CONFIRM_FRAMES
        self._pending_target = None
        self._pending_count = 0

    def update_target(self, face_box):
        """Update the target centre from a detection, ignoring lone big jumps."""
        if not face_box:
            return
        x, y, w, h = face_box
        new_center = x + w / 2

        if abs(new_center - self.target_center_x) > self.safe_zone_radius:
            # Same big move as last time? Count it. Otherwise start counting
            # afresh — two contradictory outliers must not confirm each other.
            if (self._pending_target is not None
                    and abs(new_center - self._pending_target) <= self.safe_zone_radius):
                self._pending_count += 1
            else:
                self._pending_target = new_center
                self._pending_count = 1
            if self._pending_count < self.jump_confirm_frames:
                return  # not convinced yet — hold the frame

        self._pending_target = None
        self._pending_count = 0
        self.target_center_x = new_center

    def get_crop_box(self, force_snap=False):
        """
        Returns the (x1, y1, x2, y2) for the current frame.
        """
        if force_snap:
            self.current_center_x = self.target_center_x
        else:
            diff = self.target_center_x - self.current_center_x

            # SIMPLIFIED LOGIC:
            # 1. Is the target outside the safe zone?
            #
            # Stopping at the deadzone EDGE rather than on the subject looks
            # like a hunting bug, and a two-threshold (Schmitt) version that
            # settles on the target was written and measured: it made things
            # worse on real footage — 54.5 -> 79.1 px/s of in-scene travel,
            # 0 scenes calmer, 4 busier. Reversals did not move at all
            # (0.21/s either way), which says the camera was not hunting in
            # the first place. Left as it is on purpose; see the commit that
            # demotes short TRACK scenes for where the motion actually comes
            # from.
            if abs(diff) > self.safe_zone_radius:
                # 2. If yes, move towards it slowly (Linear Speed)
                # Determine direction
                direction = 1 if diff > 0 else -1

                # Speed: 2 pixels per frame (Slow pan)
                # If the distance is HUGE (scene change or fast movement), speed up slightly
                if abs(diff) > self.crop_width * 0.5:
                    speed = 15.0 # Fast re-frame
                else:
                    speed = 3.0  # Slow, steady pan

                self.current_center_x += direction * speed

                # Check if we overshot (prevent oscillation)
                new_diff = self.target_center_x - self.current_center_x
                if (direction == 1 and new_diff < 0) or (direction == -1 and new_diff > 0):
                    self.current_center_x = self.target_center_x

            # If inside safe zone, DO NOTHING (Stationary Camera)

        # Clamp center
        half_crop = self.crop_width / 2

        if self.current_center_x - half_crop < 0:
            self.current_center_x = half_crop
        if self.current_center_x + half_crop > self.video_width:
            self.current_center_x = self.video_width - half_crop

        x1 = int(self.current_center_x - half_crop)
        x2 = int(self.current_center_x + half_crop)

        x1 = max(0, x1)
        x2 = min(self.video_width, x2)

        y1 = 0
        y2 = self.video_height

        return x1, y1, x2, y2


class SpeakerTracker:
    """
    Tracks speakers over time to prevent rapid switching and handle temporary obstructions.
    """
    def __init__(self, stabilization_frames=15, cooldown_frames=30):
        self.active_speaker_id = None
        self.speaker_scores = {}  # {id: score}
        self.last_seen = {}       # {id: frame_number}
        self.locked_counter = 0   # How long we've been locked on current speaker

        # Hyperparameters
        self.stabilization_threshold = stabilization_frames # Frames needed to confirm a new speaker
        self.switch_cooldown = cooldown_frames              # Minimum frames before switching again
        self.last_switch_frame = -1000

        # ID tracking
        self.next_id = 0
        self.known_faces = [] # [{'id': 0, 'center': x, 'last_frame': 123}]

    def get_target(self, face_candidates, frame_number, width):
        """
        Decides which face to focus on.
        face_candidates: list of {'box': [x,y,w,h], 'score': float}
        """
        current_candidates = []

        # 1. Match faces to known IDs (simple distance tracking)
        for face in face_candidates:
            x, y, w, h = face['box']
            center_x = x + w / 2

            best_match_id = -1
            min_dist = width * 0.15 # Reduced matching radius to avoid jumping in groups

            # Try to match with known faces seen recently
            for kf in self.known_faces:
                if frame_number - kf['last_frame'] > 30: # Forgot faces older than 1s (was 2s)
                    continue

                dist = abs(center_x - kf['center'])
                if dist < min_dist:
                    min_dist = dist
                    best_match_id = kf['id']

            # If no match, assign new ID
            if best_match_id == -1:
                best_match_id = self.next_id
                self.next_id += 1

            # Update known face
            self.known_faces = [kf for kf in self.known_faces if kf['id'] != best_match_id]
            self.known_faces.append({'id': best_match_id, 'center': center_x, 'last_frame': frame_number})

            current_candidates.append({
                'id': best_match_id,
                'box': face['box'],
                'score': face['score']
            })

        # 2. Update Scores with decay
        for pid in list(self.speaker_scores.keys()):
             self.speaker_scores[pid] *= 0.85 # Faster decay (was 0.9)
             if self.speaker_scores[pid] < 0.1:
                 del self.speaker_scores[pid]

        # Add new scores
        for cand in current_candidates:
            pid = cand['id']
            # Score is purely based on size (proximity) now that we don't have mouth
            raw_score = cand['score'] / (width * width * 0.05)
            self.speaker_scores[pid] = self.speaker_scores.get(pid, 0) + raw_score

        # 3. Determine Best Speaker
        if not current_candidates:
            # If no one found, maintain last active speaker if cooldown allows
            # to avoid black screen or jump to 0,0
            return None

        best_candidate = None
        max_score = -1

        for cand in current_candidates:
            pid = cand['id']
            total_score = self.speaker_scores.get(pid, 0)

            # Hysteresis: HUGE Bonus for current active speaker
            if pid == self.active_speaker_id:
                total_score *= 3.0 # Sticky factor

            if total_score > max_score:
                max_score = total_score
                best_candidate = cand

        # 4. Decide Switch
        if best_candidate:
            target_id = best_candidate['id']

            if target_id == self.active_speaker_id:
                self.locked_counter += 1
                return best_candidate['box']

            # New person. The cooldown must hold whether or not the current
            # speaker happens to be detected in THIS frame.
            #
            # It used to fall through and switch when the active speaker was
            # missing from the candidate list — a blink, a head turn or one
            # motion-blurred frame was enough. That is precisely when the
            # cooldown is needed, so it only ever fired when it wasn't: 3 of 7
            # target switches measured on a 12s clip (25-jul-2026) jumped the
            # cooldown this way, and every jump drags the camera across frame.
            #
            # Returning None holds instead: the caller only calls
            # update_target() on a truthy box, so the camera keeps its current
            # target and finishes whatever move it was making. The hold is
            # bounded by the cooldown itself — once it expires, a speaker who
            # really did leave the shot is switched away from normally.
            if frame_number - self.last_switch_frame < self.switch_cooldown:
                old_cand = next((c for c in current_candidates if c['id'] == self.active_speaker_id), None)
                return old_cand['box'] if old_cand else None

            self.active_speaker_id = target_id
            self.last_switch_frame = frame_number
            self.locked_counter = 0
            return best_candidate['box']

        return None
