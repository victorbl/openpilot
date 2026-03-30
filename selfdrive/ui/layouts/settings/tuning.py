from openpilot.common.params import Params
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.lib.application import FontWeight
from openpilot.system.ui.widgets.list_view import simple_item, option_item
from openpilot.system.ui.widgets.scroller_tici import Scroller
from openpilot.system.ui.lib.multilang import tr

PERSONALITY_CONFIGS = [
  {
    "name": "Aggressive",
    "params": {
      "follow": "TFollowAggressive",
      "steer": "LatAggrAggressive",
      "accel": "AccelRespAggressive",
    },
    "defaults": {"follow": 1.25, "steer": 5, "accel": 8},
  },
  {
    "name": "Standard",
    "params": {
      "follow": "TFollowStandard",
      "steer": "LatAggrStandard",
      "accel": "AccelRespStandard",
    },
    "defaults": {"follow": 1.45, "steer": 5, "accel": 5},
  },
  {
    "name": "Relaxed",
    "params": {
      "follow": "TFollowRelaxed",
      "steer": "LatAggrRelaxed",
      "accel": "AccelRespRelaxed",
    },
    "defaults": {"follow": 1.75, "steer": 5, "accel": 5},
  },
]


class TuningLayout(Widget):
  def __init__(self):
    super().__init__()
    self._params = Params()
    self._items = []

    for config in PERSONALITY_CONFIGS:
      defaults = config["defaults"]
      param_keys = config["params"]

      # Section header
      header = simple_item(lambda name=config["name"]: name, font_weight=FontWeight.BOLD)
      self._items.append(header)

      # Follow Distance
      follow_val = self._read_float(param_keys["follow"], defaults["follow"])
      follow_item = option_item(
        title=lambda: tr("Follow Distance"),
        value=follow_val, min_val=0.8, max_val=2.0, step=0.05,
        format_fn=lambda v: f"{v:.2f}s",
        callback=self._make_float_callback(param_keys["follow"]),
        description=lambda: tr("Time gap behind the lead car. Lower = closer following, higher = more space."),
        indent=40,
      )
      self._items.append(follow_item)

      # Steering Response
      steer_val = self._read_int(param_keys["steer"], defaults["steer"])
      steer_item = option_item(
        title=lambda: tr("Steering Response"),
        value=float(steer_val), min_val=1, max_val=10, step=1,
        format_fn=lambda v: str(int(v)),
        callback=self._make_int_callback(param_keys["steer"]),
        description=lambda: tr("How firmly the car holds its line in curves. Lower = gentle corrections, higher = tighter tracking."),
        indent=40,
      )
      self._items.append(steer_item)

      # Acceleration Response
      accel_val = self._read_int(param_keys["accel"], defaults["accel"])
      accel_item = option_item(
        title=lambda: tr("Acceleration Response"),
        value=float(accel_val), min_val=1, max_val=10, step=1,
        format_fn=lambda v: str(int(v)),
        callback=self._make_int_callback(param_keys["accel"]),
        description=lambda: tr("How sharply the car accelerates and brakes. Lower = smooth transitions, higher = snappier response."),
        indent=40,
      )
      self._items.append(accel_item)

    self._scroller = Scroller(self._items, line_separator=True, spacing=0)

  def _read_float(self, key: str, default: float) -> float:
    try:
      val = self._params.get(key, return_default=True)
      return float(val) if val is not None else default
    except (ValueError, TypeError):
      return default

  def _read_int(self, key: str, default: int) -> int:
    try:
      val = self._params.get(key, return_default=True)
      return int(val) if val is not None else default
    except (ValueError, TypeError):
      return default

  def _make_float_callback(self, key: str):
    def callback(value: float):
      self._params.put_nonblocking(key, value)
    return callback

  def _make_int_callback(self, key: str):
    def callback(value: float):
      self._params.put_nonblocking(key, int(value))
    return callback

  def _render(self, rect):
    self._scroller.render(rect)

  def show_event(self):
    super().show_event()
    self._scroller.show_event()

  def hide_event(self):
    super().hide_event()
    self._scroller.hide_event()
