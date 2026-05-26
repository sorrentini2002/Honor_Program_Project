import os
import math
import mwntr
from .base_agent import BaseAgent


class HeuristicAgent(BaseAgent):
    """
    Parsimonious Hydraulic Control Agent.

    Design principles
    -----------------
    1. SOFT ZONE  – mild crisis  → valve barely cracks open (proportional,
                                    continuous, no bang-bang).
    2. HARD ZONE  – deep crisis  → opening ramps up along a power curve so the
                                    response is non-linear but still smooth.
    3. TIME BUDGET               → target opening is attenuated by how much of
                                    the reserve must last until end-of-simulation,
                                    preventing early exhaustion.
    4. HERMETIC MUTUAL EXCLUSION → valve open  ⟹ pump is CLOSED (no local loop);
                                    valve closed ⟹ TCV setting forced to 1e7
                                    (WNTR quirk: small settings ≠ zero flow).
    5. LIVE TANK READING         → tank level is always read from the active
                                    sim.node_res dict, never from the frozen
                                    static network object.
    6. SLEW-RATE LIMITER         → valve position changes at most `max_move`
                                    per step, preventing chattering.
    """

    # ------------------------------------------------------------------ #
    #  Tuning knobs – override in __init__ kwargs if needed               #
    # ------------------------------------------------------------------ #
    VALVE_MAX_OPENING   = 0.20   # Hard ceiling: never open beyond 20 % (era 70%)
    VALVE_MIN_OPENING   = 0.005  # Dead-band: below this → hermetically closed
    SLEW_RATE           = 0.003  # Max Δ per step (≈ 0.3 % / step -> molto più graduale)
    POWER_CURVE_EXP     = 2.5    # Exponent of the opening curve (> 1 → concave)
    SOFT_ZONE_FRACTION  = 0.15   # First 15 % of deficit handled in "trickle" mode
    SOFT_ZONE_CAP       = 0.04   # Trickle-mode opening never exceeds 4 %
    TIME_DECAY_K        = 0.05   # Attenuation strength with remaining hours (più forte per preservare le scorte)
    TX_INTERVAL_ALERT   = 300    # LoRa TX interval during crisis  [s]
    TX_INTERVAL_NOMINAL = 3600   # LoRa TX interval at rest        [s]
    LOSS_COEFF_OPEN_BASE = 10.0  # Minimum head-loss coeff (fully open valve)
    LOSS_COEFF_CUBIC_K   = 8000.0  # Cubic term that makes TCV resist at low opening
    LOSS_COEFF_CLOSED    = 1e7   # Hermetic seal value for WNTR
    # ------ Per-time-window maximum allowed volume fraction to release ------
    # Fractions of the tank volume (0..1)
    MAX_DROP_PER_MIN_FRAC   = 0.002   # max fraction of tank volume per minute
    MAX_DROP_PER_30MIN_FRAC = 0.03    # max fraction per 30 minutes
    MAX_DROP_PER_HOUR_FRAC  = 0.05    # max fraction per hour
    # Estimated fraction of tank volume emptied per hour at FULL valve opening
    FULL_OPEN_EMPTY_RATE_PER_HOUR = 0.6

    # ------------------------------------------------------------------ #

    def __init__(self, water_net, lora_net,
                 threshold: float = 0.90,
                 aggression: float = 4.0,
                 alpha: float = 0.80):
        super().__init__(water_net, lora_net, threshold, aggression, alpha)

        # Exponential-moving-average state for satisfaction signal
        self.smoothed_s: float = 1.0

        # Valve position integrator  [0, 1]
        # Per-valve current levels (lazy-initialised when first applying)
        self.current_valve_level: float = 0.0
        self.current_valve_levels = {}

        # ---- Log setup ---- #
        self.log_path = "Log_review/agent_performance.txt"
        log_dir = os.path.dirname(self.log_path)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        with open(self.log_path, "w", encoding="utf-8") as f:
            header = (
                "STEP | SAT_RAW | SAT_EMA | DEFICIT | "
                "RES_IDX | REM_H | TARGET_LVL | CURR_LVL | "
                "TX_INT | REWARD\n"
            )
            f.write(header)
            f.write("-" * len(header.rstrip()) + "\n")

    # ================================================================== #
    #  PUBLIC INTERFACE                                                    #
    # ================================================================== #

    def decide_action(self, step: int, t, s_current: float) -> dict:
        """
        Called once per simulation step.

        Parameters
        ----------
        step      : current time-step index (0-based)
        t         : current simulation time  [s]  (not used directly but
                    kept for API compatibility with BaseAgent)
        s_current : satisfaction ratio supplied by main.py  (may be stale)

        Returns
        -------
        dict with keys: tx_interval, level, pump_speed
        """
        # ---- 0. Re-compute satisfaction from live sim if available ---- #
        s_current = self._refresh_satisfaction(s_current)

        # ---- 1. Smooth the satisfaction signal (EMA) ---- #
        self.smoothed_s = (
            self.alpha * s_current + (1.0 - self.alpha) * self.smoothed_s
        )

        # ---- 2. Read live tank resource index ---- #
        resource_index = self._compute_resource_index()

        # ---- 3. Compute remaining simulation time [hours] ---- #
        remaining_hours = self._remaining_hours(step)

        # ---- 4. Compute target valve opening ---- #
        target_level, tx_interval = self._compute_target_opening(
            resource_index, remaining_hours
        )

        # ---- 5. Apply slew-rate limiter ---- #
        self.current_valve_level = self._slew(
            self.current_valve_level, target_level
        )

        # ---- 6. Apply dead-band → hermetic closure ---- #
        if self.current_valve_level < self.VALVE_MIN_OPENING:
            self.current_valve_level = 0.0

        # ---- 7. Compute reward (informational) ---- #
        reward = self.compute_objective(self.smoothed_s, tx_interval)

        # ---- 8. Log ---- #
        deficit = max(0.0, self.threshold - self.smoothed_s)
        self._log_step(
            step, s_current, self.smoothed_s, deficit,
            resource_index, remaining_hours,
            target_level, self.current_valve_level,
            tx_interval, reward
        )

        return {
            "tx_interval": int(tx_interval),
            "level":       float(self.current_valve_level),
            "pump_speed":  0.0,   # pumps are always managed in apply_mitigation
            "step":        int(step),
        }

    def apply_mitigation(self, action: dict, sim, lora_net, t=None) -> None:
        """
        Push the decided action onto the live hydraulic model.

        Hermetic rules
        --------------
        • level > VALVE_MIN_OPENING  → valve OPEN with computed loss-coeff,
                                        pump CLOSED & speed = 0.
        • level == 0                 → valve CLOSED with loss-coeff = 1e7,
                                        pump CLOSED & speed = 0.
        Both branches always shut the pump to prevent local recirculation loops.
        """
        lora_net.tx_interval_s = action.get("tx_interval", self.TX_INTERVAL_NOMINAL)
        overall_level = float(action.get("level", 0.0))
        step = int(action.get("step", 0))

        model_changed = False
        opened_valves_count = 0

        # Lazy init per-valve state
        valve_names = list(self.water_net.iot_valves)
        n_valves = max(1, len(valve_names))
        if not self.current_valve_levels:
            for v in valve_names:
                self.current_valve_levels[v] = 0.0

        # Temporal window (number of steps ~ 1 hour)
        timestep_s = getattr(self.water_net, 'timestep_s', 300)
        steps_per_hour = max(1, int(3600 // timestep_s))
        window_steps = steps_per_hour  # shift active valve each hour

        # Decide which valve gets the main opening this window (staggered)
        active_idx = (step // window_steps) % n_valves if n_valves > 0 else 0

        # Per-valve target: active valve gets the majority, others get a small trickle
        for idx, v_name in enumerate(valve_names):
            # compute target fraction
            if n_valves == 0:
                target_frac = 0.0
            elif idx == active_idx:
                # active valve: most of the requested opening
                target_frac = 1.0
            else:
                # background trickle to keep network stable
                target_frac = 0.15

            target_level = max(0.0, min(self.VALVE_MAX_OPENING, overall_level * target_frac))

            # Apply per-valve slew limiter
            current = self.current_valve_levels.get(v_name, 0.0)
            new_level = self._slew(current, target_level)

            # Dead-band
            if new_level < self.VALVE_MIN_OPENING:
                new_level = 0.0
            # --- Enforce volumetric caps per time window ---
            # Determine timestep in minutes
            timestep_s = getattr(self.water_net, 'timestep_s', 300)
            dt_min = max(1e-6, timestep_s / 60.0)

            # allowed fraction of tank volume this step (min/30min/hour scaled)
            allowed_frac_min = self.MAX_DROP_PER_MIN_FRAC * dt_min
            allowed_frac_30 = self.MAX_DROP_PER_30MIN_FRAC * (dt_min / 30.0)
            allowed_frac_hour = self.MAX_DROP_PER_HOUR_FRAC * (dt_min / 60.0)
            allowed_fraction_per_step = min(allowed_frac_min, allowed_frac_30, allowed_frac_hour)

            # Try to associate valve to a tank to be conservative; fallback to global cap
            tank_fraction_cap = allowed_fraction_per_step
            try:
                link_obj = sim._wn.get_link(v_name)
                # find connected tank node if any
                tank_name = None
                start = getattr(link_obj, 'start_node_name', None)
                end = getattr(link_obj, 'end_node_name', None)
                if start in sim._wn.tank_name_list:
                    tank_name = start
                elif end in sim._wn.tank_name_list:
                    tank_name = end
                else:
                    # try objects
                    start_obj = getattr(link_obj, 'start_node', None)
                    end_obj = getattr(link_obj, 'end_node', None)
                    if start_obj is not None and getattr(start_obj, '_name', None) in sim._wn.tank_name_list:
                        tank_name = getattr(start_obj, '_name')
                    elif end_obj is not None and getattr(end_obj, '_name', None) in sim._wn.tank_name_list:
                        tank_name = getattr(end_obj, '_name')
            except Exception:
                tank_name = None

            # Map valve opening to estimated fraction of tank volume emptied per step
            # fraction_per_hour at opening = (new_level / VALVE_MAX_OPENING) * FULL_OPEN_EMPTY_RATE_PER_HOUR
            # fraction_per_step = fraction_per_hour * (dt_min/60)
            if new_level > 0.0 and self.FULL_OPEN_EMPTY_RATE_PER_HOUR > 0:
                est_frac_per_step = (new_level / max(1e-6, self.VALVE_MAX_OPENING)) * self.FULL_OPEN_EMPTY_RATE_PER_HOUR * (dt_min / 60.0)
                if est_frac_per_step > tank_fraction_cap:
                    # reduce opening so that est_frac_per_step == tank_fraction_cap
                    max_allowed_level = self.VALVE_MAX_OPENING * (tank_fraction_cap * 60.0) / (self.FULL_OPEN_EMPTY_RATE_PER_HOUR * dt_min)
                    new_level = min(new_level, max_allowed_level)

            # Compute loss coeff and status for this valve
            if new_level >= self.VALVE_MIN_OPENING:
                loss_coeff = (
                    self.LOSS_COEFF_OPEN_BASE
                    + self.LOSS_COEFF_CUBIC_K * math.pow(1.0 - new_level, 3.0)
                )
                valve_status = mwntr.network.elements.LinkStatus.Open
            else:
                loss_coeff = self.LOSS_COEFF_CLOSED
                valve_status = mwntr.network.elements.LinkStatus.Closed

            valve = sim._wn.get_link(v_name)
            current_coeff = getattr(valve, "initial_setting", -1.0)
            if (
                abs(current_coeff - loss_coeff) > 0.1
                or valve.initial_status != valve_status
            ):
                valve.initial_setting = loss_coeff
                valve.initial_status  = valve_status
                model_changed = True

            # update tracked level and opened counter
            self.current_valve_levels[v_name] = new_level
            if valve_status == mwntr.network.elements.LinkStatus.Open and new_level > 0.0:
                opened_valves_count += 1

        # ---- Always shut IoT pumps (mutual exclusion) ---- #
        for p_name in self.water_net.iot_pumps:
            pump = sim._wn.get_link(p_name)
            if pump.initial_status != mwntr.network.elements.LinkStatus.Closed:
                pump.initial_status = mwntr.network.elements.LinkStatus.Closed
                model_changed = True

        # ---- Update opened count for statistics ---- #
        self.opened_count = opened_valves_count

        # ---- Signal the simulator to rebuild its matrix if needed ---- #
        if model_changed and hasattr(sim, "rebuild_hydraulic_model"):
            sim.rebuild_hydraulic_model = True

    # ================================================================== #
    #  PRIVATE HELPERS                                                     #
    # ================================================================== #

    def _refresh_satisfaction(self, s_fallback: float) -> float:
        """Re-read satisfaction from the live sim to avoid stale values."""
        if hasattr(self.water_net, "sim"):
            try:
                s_live = self.calculate_current_satisfaction(self.water_net.sim)
                if 0.0 <= s_live <= 1.0:
                    return s_live
            except Exception:
                pass
        return s_fallback

    def _compute_resource_index(self) -> float:
        """
        Compute how full the emergency tanks are  [0, 1].

        Always reads from sim.node_res['pressure'] (live data).
        Falls back to tank.init_level only if the live dict is unavailable.
        """
        # Prefer the active simulation WN object
        if hasattr(self.water_net, "sim") and hasattr(self.water_net.sim, "_wn"):
            active_wn = self.water_net.sim._wn
        else:
            active_wn = self.water_net.wn

        stored   = 0.0
        capacity = 0.0

        for t_name in active_wn.tank_name_list:
            try:
                tank = active_wn.get_node(t_name)
                min_lvl = tank.min_level
                max_lvl = tank.max_level
                range_  = max(0.1, max_lvl - min_lvl)

                # Prefer live pressure/level reading
                current_level = None
                if (
                    hasattr(self.water_net, "sim")
                    and "pressure" in self.water_net.sim.node_res
                    and t_name in self.water_net.sim.node_res["pressure"]
                ):
                    data = self.water_net.sim.node_res["pressure"][t_name]
                    if data:
                        current_level = data[-1]

                if current_level is None:
                    current_level = getattr(tank, "level", tank.init_level)

                stored   += max(0.0, current_level - min_lvl)
                capacity += range_

            except Exception:
                # Neutral fallback: assume half-full tank of unit range
                stored   += 1.5
                capacity += 3.0

        return stored / capacity if capacity > 0 else 0.5

    def _remaining_hours(self, step: int) -> float:
        """Return the number of hours left in the simulation."""
        total_steps  = getattr(self.water_net, "n_steps",    180)
        timestep_s   = getattr(self.water_net, "timestep_s", 300)
        steps_left   = max(0, total_steps - step)
        return (steps_left * timestep_s) / 3600.0

    def _compute_target_opening(
        self, resource_index: float, remaining_hours: float
    ) -> tuple:
        """
        Map (smoothed satisfaction, resource index, remaining time) →
        (target_valve_level ∈ [0, VALVE_MAX_OPENING], tx_interval).

        Opening curve
        -------------
        deficit  = threshold − smoothed_s  (clamped to [0, threshold])

        Normalised error  e = deficit / threshold  ∈ [0, 1]

        Soft zone  (e ≤ SOFT_ZONE_FRACTION):
            opening = (e / SOFT_ZONE_FRACTION) * SOFT_ZONE_CAP
            → linear, very small, trickle mode

        Hard zone  (e > SOFT_ZONE_FRACTION):
            e_hard  = (e − SOFT_ZONE_FRACTION) / (1 − SOFT_ZONE_FRACTION)
            opening = SOFT_ZONE_CAP + e_hard^EXP * (VALVE_MAX_OPENING − SOFT_ZONE_CAP)
            → non-linear ramp, still continuous at the soft/hard boundary

        Time-budget attenuation
        -----------------------
        opening *= 1 / (1 + TIME_DECAY_K * remaining_hours)
        → if many hours remain we trickle even less to preserve reserve

        Resource-index scaling
        ----------------------
        opening *= resource_index
        → tanks almost empty → spontaneously reduce opening even further
        """
        deficit = max(0.0, self.threshold - self.smoothed_s)

        if deficit <= 0.0:
            return 0.0, self.TX_INTERVAL_NOMINAL

        # Normalised error on [0, 1]
        e = min(1.0, deficit / self.threshold)

        if e <= self.SOFT_ZONE_FRACTION:
            # --- Soft (trickle) zone: linear micro-opening ---
            raw_opening = (e / self.SOFT_ZONE_FRACTION) * self.SOFT_ZONE_CAP
        else:
            # --- Hard zone: concave power ramp ---
            e_hard = (e - self.SOFT_ZONE_FRACTION) / (1.0 - self.SOFT_ZONE_FRACTION)
            hard_contribution = (
                math.pow(e_hard, self.POWER_CURVE_EXP)
                * self.aggression / 10.0   # aggression scales the hard-zone ceiling
                * (self.VALVE_MAX_OPENING - self.SOFT_ZONE_CAP)
            )
            raw_opening = self.SOFT_ZONE_CAP + hard_contribution

        # Time-budget attenuation
        time_factor = 1.0 / (1.0 + self.TIME_DECAY_K * remaining_hours)
        raw_opening *= time_factor

        # Reserve-level scaling
        raw_opening *= max(0.0, min(1.0, resource_index))

        # Clip to physical limits
        target = max(0.0, min(self.VALVE_MAX_OPENING, raw_opening))

        tx_interval = self.TX_INTERVAL_ALERT if target > 0 else self.TX_INTERVAL_NOMINAL
        return target, tx_interval

    def _slew(self, current: float, target: float) -> float:
        """Rate-limiter: move current toward target at most SLEW_RATE per step."""
        delta = target - current
        if abs(delta) <= self.SLEW_RATE:
            return target
        return current + math.copysign(self.SLEW_RATE, delta)

    def _log_step(
        self, step, s_raw, s_ema, deficit,
        res_idx, rem_h, target_lvl, curr_lvl,
        tx_int, reward
    ) -> None:
        """Append a one-line summary to the performance log."""
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(
                    f"{step:4d} | {s_raw:.4f} | {s_ema:.4f} | {deficit:.4f} | "
                    f"{res_idx:.3f} | {rem_h:6.2f} | {target_lvl:.4f} | "
                    f"{curr_lvl:.4f} | {tx_int:5d} | {reward:+.4f}\n"
                )
        except Exception:
            pass   # logging must never crash the simulation