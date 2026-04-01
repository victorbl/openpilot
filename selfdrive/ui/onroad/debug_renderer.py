import numpy as np
import pyray as rl
from cereal import log
from openpilot.common.params import Params
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

AlertSize = log.SelfdriveState.AlertSize

# Inlined from longitudinal_planner to avoid pulling casadi/acados/radard into UI process
_FOLLOW_PARAM_KEYS = {
  log.LongitudinalPersonality.aggressive: "TFollowAggressive",
  log.LongitudinalPersonality.standard: "TFollowStandard",
  log.LongitudinalPersonality.relaxed: "TFollowRelaxed",
}

def _follow_scale_to_t_follow(scale: int) -> float:
  return 0.41 + (scale - 1) * 0.149

BG_COLOR = rl.Color(0, 0, 0, 150)
TEXT_COLOR = rl.Color(255, 255, 255, 220)
FONT_SIZE = 40
PADDING_H = 20
PADDING_V = 12
BORDER_RADIUS = 0.4


class DebugRenderer(Widget):
  def __init__(self):
    super().__init__()
    self._font = gui_app.font(FontWeight.MEDIUM)
    self._params = Params()

    self._target_t_follow = 1.45  # stock standard default
    self._actual_gap = None
    self._cached_personality = None
    self._param_read_counter = 0

    self.set_visible(lambda: (
      ui_state.sm["selfdriveState"].enabled and
      ui_state.sm["selfdriveState"].alertSize == AlertSize.none
    ))

  def _update_state(self) -> None:
    if not self.is_visible:
      return

    sm = ui_state.sm
    if sm.recv_frame["carState"] < ui_state.started_frame:
      return

    # Refresh target t_follow from params (~1s at 60fps)
    personality = sm['selfdriveState'].personality
    self._param_read_counter += 1
    if personality != self._cached_personality or self._param_read_counter >= 60:
      self._param_read_counter = 0
      self._cached_personality = personality
      key = _FOLLOW_PARAM_KEYS.get(personality.raw)
      if key:
        try:
          raw = self._params.get(key, return_default=True)
          scale = max(1, min(int(raw), 10))
          self._target_t_follow = _follow_scale_to_t_follow(scale)
        except (ValueError, TypeError):
          self._target_t_follow = 1.45


    # Compute actual time gap to lead car
    v_ego = sm['carState'].vEgo
    lead = sm['radarState'].leadOne if sm.valid['radarState'] else None
    if lead and lead.status and v_ego > 0.5 and lead.dRel < 100.0:
      self._actual_gap = lead.dRel / v_ego
    else:
      self._actual_gap = None

  def _render(self, rect: rl.Rectangle) -> None:
    # Build display text
    target_str = f"{self._target_t_follow:.1f}s"
    if self._actual_gap is not None:
      text = f"Follow: {target_str} target | {self._actual_gap:.1f}s actual"
    else:
      text = f"Follow: {target_str} target"

    # Measure text for background pill
    text_size = measure_text_cached(self._font, text, FONT_SIZE)
    pill_w = text_size.x + PADDING_H * 2
    pill_h = text_size.y + PADDING_V * 2

    # Position: bottom-left, above DM icon area
    x = rect.x + 30
    y = rect.y + rect.height - 350

    # Draw background pill
    pill_rect = rl.Rectangle(x, y, pill_w, pill_h)
    rl.draw_rectangle_rounded(pill_rect, BORDER_RADIUS, 10, BG_COLOR)

    # Draw text
    rl.draw_text_ex(self._font, text, rl.Vector2(x + PADDING_H, y + PADDING_V), FONT_SIZE, 0, TEXT_COLOR)
