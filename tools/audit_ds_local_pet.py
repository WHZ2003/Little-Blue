"""Offline compatibility probes for ds-local-pet d78094b; not app tests.

Requires the reviewed upstream sources in .research/ds-local-pet.
No API keys, network requests, actual window sensing, or application changes.
Known-limit probes assert observed upstream behavior, not desired behavior.
Run: .venv/Scripts/python.exe tools/audit_ds_local_pet.py -v
"""
import os
from pathlib import Path
import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / '.research/ds-local-pet'
if not (UPSTREAM / 'animation/player.py').is_file():
    raise SystemExit('Missing reviewed upstream checkout: .research/ds-local-pet')
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(UPSTREAM))

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication
from animation.clip import ActionSpec, Anchor, AnimationClip, ClipFrame, PetAction
from animation.player import AnimationPlayer
from animation.state_machine import ActionStateMachine
from awareness.sensor import ContextSensor
from awareness.privacy import PrivacyPolicy
from awareness.fullscreen_detector import FullscreenDetector
from pet.interaction import InteractionController
from pet.physics import PhysicsBounds, ThrowPhysics
from pet.surfaces import DesktopSurfaceTracker
from little_blue.sprites import SpriteBank


class CompatibilityProbes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.bank = SpriteBank(ROOT / 'assets/blue/manifest.json')

    def clip(self, name='walk', action=PetAction.WALKING):
        source = self.bank.clips[name]
        return AnimationClip(
            action=action, asset_id=f'little_blue_{name}',
            frames=tuple(ClipFrame(Path(f'{name}_{i}.png'), p)
                         for i, p in enumerate(source.frames)),
            loop=source.loop, duration_ms=None,
            frame_duration_ms=round(1000 / source.fps), anchor=Anchor(),
            effect='micro_idle', quality='local', requires_real_frames=False)

    def test_existing_spritebank_bridges_seven_shared_actions(self):
        mapping = {'idle': PetAction.IDLE, 'walk': PetAction.WALKING,
                   'sleep': PetAction.SLEEPING, 'happy': PetAction.HAPPY,
                   'angry': PetAction.ANGRY, 'talk': PetAction.TALKING,
                   'drag': PetAction.DRAGGING}
        for name, action in mapping.items():
            with self.subTest(name=name):
                player = AnimationPlayer()
                clip = self.clip(name, action)
                player.play(clip)
                player.tick(clip.frame_duration_ms / 1000 * 2.1)
                layer = player.snapshot().current
                self.assertEqual(layer.frame.pixmap.cacheKey(), clip.frames[2].pixmap.cacheKey())
                self.assertFalse(layer.frame.pixmap.isNull())
        self.assertFalse(hasattr(PetAction, 'RUN'))
        self.assertFalse(hasattr(PetAction, 'JUMP'))

    def test_known_limit_turning_resets_frame_two_to_zero(self):
        player = AnimationPlayer()
        clip = self.clip()
        player.play(clip)
        player.tick(0.28)
        self.assertEqual(player.snapshot().current.frame.source, Path('walk_2.png'))
        player.play(replace(clip, mirrored=True))
        self.assertEqual(player.elapsed_seconds, 0)
        self.assertEqual(player.snapshot().current.frame.source, Path('walk_0.png'))

    def test_known_limit_changed_fps_is_not_in_clip_identity(self):
        player = AnimationPlayer()
        clip = self.clip()
        player.play(clip)
        self.assertFalse(player.play(replace(clip, frame_duration_ms=250)))
        self.assertEqual(player.clip.frame_duration_ms, 125)

    def test_loop_exit_requires_explicit_host_transition(self):
        def spec(action, priority):
            return ActionSpec(action, action.value, True, None, priority, True,
                              PetAction.IDLE, Anchor(), '', 'local', False)
        machine = ActionStateMachine({PetAction.IDLE: spec(PetAction.IDLE, 10),
                                      PetAction.SLEEPING: spec(PetAction.SLEEPING, 40)})
        machine.request(PetAction.SLEEPING, now=0, reason='rest')
        self.assertIsNone(machine.request(PetAction.IDLE, now=60, reason='wake'))
        self.assertIsNone(machine.update(now=600))
        self.assertIsNotNone(machine.request(PetAction.IDLE, now=600, reason='wake', force=True))

    def test_drag_velocity_pause_and_cancel(self):
        clock = [0.0]
        with patch('pet.interaction.time.monotonic', side_effect=lambda: clock[0]):
            control = InteractionController()
            def press():
                control.press(QPoint(0, 0), 50, 50, 0, 0, 100, 100)
            press()
            clock[0] = .1
            self.assertTrue(control.move(QPoint(100, 0)))
            clock[0] = .12
            out = control.release(QPoint(120, 0))
            self.assertAlmostEqual(out.velocity_x, 1000)
            self.assertEqual(out.kind, 'drag')
            press()
            clock[0] = .22
            control.move(QPoint(100, 0))
            clock[0] = .6
            self.assertEqual(control.release(QPoint(100, 0)).velocity_x, 0)
            press()
            control.cancel()
            self.assertFalse(control.active)
            self.assertIsNone(control.release(QPoint(0, 0)))

    def test_physics_adapter_exclusive_bounds_at_30_and_60_hz(self):
        # Our right/bottom are exclusive; upstream's are inclusive.
        bounds = PhysicsBounds(-800, 0, 799, 599)
        for hz in (30, 60):
            with self.subTest(hz=hz):
                physics = ThrowPhysics()
                physics.launch(-700, 50, 1000, -100)
                for _ in range(hz * 10):
                    step = physics.step(1 / hz, width=180, height=180, bounds=bounds)
                    self.assertGreaterEqual(step.x, -800)
                    self.assertLessEqual(step.x, 620)
                    if step.settled:
                        break
                self.assertTrue(step.settled)
                self.assertEqual(step.y, 420)

    def test_known_limit_disabled_surfaces_still_query_foreground_handle(self):
        reader = Mock()
        reader.foreground_handle.return_value = 42
        tracker = DesktopSurfaceTracker(reader, enabled=False)
        self.assertFalse(tracker.observe_foreground(now=1))
        reader.foreground_handle.assert_called_once()
        reader.read.assert_not_called()

    def test_known_limit_title_flag_only_redacts_output(self):
        sensor = ContextSensor.__new__(ContextSensor)
        sensor._reader = Mock()
        sensor._reader.read.return_value = SimpleNamespace(
            title='synthetic-private-title', process_name='test.exe', pid=-1, handle=42)
        sensor._ignored_pids = set()
        sensor._privacy_policy = Mock()
        sensor._privacy_policy.is_private.return_value = False
        sensor._classifier = Mock()
        sensor._classifier.classify.return_value = 'unknown'
        sensor._fullscreen_detector = Mock()
        sensor._fullscreen_detector.is_fullscreen.return_value = False
        snapshot = sensor.capture(read_window_title=False, idle_detection=False)
        self.assertEqual(snapshot.window_title, '')
        sensor._classifier.classify.assert_called_once_with('test.exe', 'synthetic-private-title')
        sensor._reader.read.assert_called_once()

    def test_known_limit_missing_privacy_rules_allow_unmatched_content(self):
        with patch('awareness.privacy.Path.read_text', side_effect=FileNotFoundError):
            policy = PrivacyPolicy(Path('synthetic-missing.json'))
        self.assertFalse(policy.is_private('test.exe', 'synthetic-private-title'))

    def test_fullscreen_uses_secondary_monitor_and_rejects_framed_window(self):
        detector = FullscreenDetector.__new__(FullscreenDetector)
        detector._available = True
        detector.tolerance = 2
        detector._get_window_style = Mock(return_value=0)
        detector._user32 = Mock()
        def window_rect(handle, pointer):
            rect = pointer._obj
            rect.left, rect.top, rect.right, rect.bottom = -1920, 0, 0, 1080
            return True
        def monitor_info(handle, pointer):
            rect = pointer._obj.rcMonitor
            rect.left, rect.top, rect.right, rect.bottom = -1920, 0, 0, 1080
            return True
        detector._user32.GetWindowRect.side_effect = window_rect
        detector._user32.MonitorFromWindow.return_value = 2
        detector._user32.GetMonitorInfoW.side_effect = monitor_info
        self.assertTrue(detector.is_fullscreen(42))
        detector._get_window_style.return_value = detector._WS_CAPTION
        self.assertFalse(detector.is_fullscreen(42))


if __name__ == '__main__':
    unittest.main()
