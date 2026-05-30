# -*- coding: utf-8 -*-
"""
================================================================================
SOLUTION.PY  --  ALNS icin cozum gosterimi (Day 2 / parca 1)
================================================================================
Amac:
  ALNS algoritmasinin her iterasyonda manipule edecegi cozum yapisi.
  V19'un MILP formulasyonu ile birebir UYUMLU mantiksal yapi.

Tasarim kararlari:
  - Zone-locked: her dron sadece kendi zone'unun hex'lerine gider.
  - Drone ID semasi: (zone, drone_id) tuple, ornegin ("S1", "D1").
    V25 ciktilarinda zone bazli D1-D11 cakisiyor. Tuple ile coz.
  - D12-S2 dahil edildi: ALNS ona is bulabilir (V25 yapmadi).
  - Time grid: 144 dilim x 10 dk = 24 saat.
================================================================================
"""

import math
import pandas as pd
from collections import defaultdict
from copy import deepcopy


# ==============================================================================
# KONFIGURASYON (V19 + V25 ile birebir AYNI)
# ==============================================================================
class Config:
    # Bataryalar (DJI Matrice 30T)
    E_CAP    = 100.0
    E_MIN    = 20.0
    D_FLIGHT = 2.0    # %/dk
    D_HOVER  = 2.2    # %/dk
    C_RATE   = 1.6    # %/dk
    V_CRUISE = 15.0   # m/s
    E_DEP    = 60.0
    HOVER_TIME_MIN = 1.0
    E_BLOCK_END    = 60.0

    # Zaman
    DELTA_T     = 10
    BLOCK_HOURS = 2
    TOTAL_HOURS = 24
    BLOCK_LEN   = 12     # 2h * 60 / 10 dk = 12 dilim
    N_BLOCKS    = 12     # 24h / 2h
    T_TOTAL     = 144    # 12 blok x 12 dilim

    # Sarj
    CAP_S = 10
    S_WAIT = 1

    # Periyodik denetim
    MAX_INTERVAL_HIGH = 180
    MAX_INTERVAL_LOW  = 360
    TAU_BUFFER        = 240

    # Amac fonksiyonu
    # V25 v25.3 patch: BETA artirildi (10 -> 100) cunku ALNS'te kapsama
    # ek hareket (enerji) gerektiriyor, slack azalisi enerji artisini
    # gecisi siniyordu. BETA 100 ile slack azalisi 10x daha degerli.
    # DELTA eklendi: her benzersiz hex ziyaretine bonus, kapsama maksimizasyonu icin.
    ALPHA = 1.0
    BETA  = 100.0    # eski: 10.0
    GAMMA = 5.0
    DELTA = 1000.0   # YENI: benzersiz hex ziyareti basina bonus


# 3 istasyon (V25 ile birebir)
STATIONS = {
    "S1": {"lat": 38.188180, "lon": 26.779120},
    "S2": {"lat": 38.179069, "lon": 26.784845},
    "S3": {"lat": 38.188075, "lon": 26.791065},
}


# ==============================================================================
# YARDIMCI FONKSIYONLAR
# ==============================================================================
def compute_max_interval(risk, risk_min, risk_max, cfg):
    """V19 ile birebir: risk-temelli max_interval hesabi."""
    risk_span = risk_max - risk_min if risk_max > risk_min else 1.0
    norm = (risk_max - risk) / risk_span
    return cfg.MAX_INTERVAL_HIGH + (cfg.MAX_INTERVAL_LOW - cfg.MAX_INTERVAL_HIGH) * norm


def euclidean_dist_m(lat1, lon1, lat2, lon2, m_per_lon):
    """Yerel duzlemsel projeksiyon ile mesafe (m)."""
    dlat = (lat1 - lat2) * 111_000
    dlon = (lon1 - lon2) * m_per_lon
    return math.hypot(dlat, dlon)


# ==============================================================================
# SOLUTION SINIFI
# ==============================================================================
class Solution:
    """
    ALNS'in manipule edecegi cozum yapisi.

    Veri yapisi:
      drones[(zone, drone_id)] = {
          "home":     "S1",                                # istasyon
          "schedule": ["S1", "H123", "H123", ..., "S1"],   # 145 nokta (t=0..144)
          "energy":   [100.0, 98.0, ..., 100.0],           # 145 nokta
          "actions":  ["move"|"visit"|"charge"|"idle", ...]# 144 dilim (t=0..143)
      }

      hex_visits[hex_id] = [t1, t2, ...]   # ziyaret zamanlari (siralanmis)
      hex_info[hex_id]   = {"zone", "risk", "max_interval", "lat", "lon"}
    """

    def __init__(self, cfg=None):
        self.cfg = cfg or Config()
        self.drones = {}
        self.hex_visits = defaultdict(list)
        self.hex_info = {}
        self.coords = {}
        self._cached_objective = None
        self._cached_kpis = None

    # ----- Cache yonetimi -----
    def invalidate_cache(self):
        """Cozum degistirildikten sonra cagir."""
        self._cached_objective = None
        self._cached_kpis = None

    # ----- Temel sorgular -----
    def get_drone_keys(self):
        return list(self.drones.keys())

    def get_drone_zone(self, drone_key):
        return drone_key[0]

    def is_zone_allowed(self, drone_key, hex_id):
        """Zone-locked kontrol."""
        if hex_id in STATIONS:
            return self.drones[drone_key]["home"] == hex_id
        return drone_key[0] == self.hex_info[hex_id]["zone"]

    # ----- Tau profili (kritik metric) -----
    def compute_tau_profile(self):
        """Her hex icin (t, tau) profili. tau = son ziyaretten gecen dakika."""
        tau = {}
        for hex_id in self.hex_info:
            visits = sorted(self.hex_visits.get(hex_id, []))
            tau_series = []
            # V19: baslangic ofset (TAU_BUFFER kadar geri)
            last_visit_t = -self.cfg.TAU_BUFFER / self.cfg.DELTA_T
            v_iter = iter(visits)
            next_v = next(v_iter, None)
            for t in range(self.cfg.T_TOTAL + 1):
                while next_v is not None and next_v <= t:
                    last_visit_t = next_v
                    next_v = next(v_iter, None)
                tau_t = (t - last_visit_t) * self.cfg.DELTA_T
                tau_series.append(max(0.0, tau_t))
            tau[hex_id] = tau_series
        return tau

    # ----- KPI hesaplari -----
    def compute_slack_total(self):
        """Toplam slack = sum max(0, tau[hex,t] - max_interval[hex])."""
        tau = self.compute_tau_profile()
        total_slack = 0.0
        risk_weighted_slack = 0.0
        for hex_id, info in self.hex_info.items():
            mi = info["max_interval"]
            risk = info["risk"]
            for t in range(self.cfg.T_TOTAL):
                excess = max(0.0, tau[hex_id][t] - mi)
                total_slack += excess
                risk_weighted_slack += risk * excess
        return total_slack, risk_weighted_slack

    def compute_visit_reward(self):
        reward = 0.0
        for hex_id, visits in self.hex_visits.items():
            if hex_id in self.hex_info:
                risk = self.hex_info[hex_id]["risk"]
                reward += risk * len(visits)
        return reward

    def compute_energy_total(self):
        """V19: toplam ucus + hover enerjisi."""
        total = 0.0
        hover_per_visit = self.cfg.D_HOVER * self.cfg.HOVER_TIME_MIN
        for dkey, info in self.drones.items():
            for t in range(self.cfg.T_TOTAL):
                action = info["actions"][t]
                if action == "move":
                    delta = info["energy"][t] - info["energy"][t + 1]
                    total += max(0.0, delta)
                elif action == "visit":
                    total += hover_per_visit
        return total

    # ----- Amac fonksiyonu V19 (kiyaslama icin korunur) -----
    def evaluate_v19(self):
        """V19/V25 MILP orijinal amac fonksiyonu:
           obj = ALPHA * enerji + BETA * risk_slack - GAMMA * ziyaret_odulu
        
        Bu V25 ile karsilastirma icin korunur. V25 sonuclarini yeniden
        degerlendirirken bunu cagir.
        """
        energy_total = self.compute_energy_total()
        _, risk_weighted_slack = self.compute_slack_total()
        visit_reward = self.compute_visit_reward()

        obj = (self.cfg.ALPHA * energy_total
               + self.cfg.BETA  * risk_weighted_slack
               - self.cfg.GAMMA * visit_reward)
        return obj

    # ----- Amac fonksiyonu ALNS-tuned (kapsama-odakli) -----
    def evaluate(self):
        """ALNS-tuned objektif fonksiyon. Kapsama-odakli kalibrasyon.

        Akademik gerekce (tezde Bolum 4.3.3'te aciklanmali):
          V19/V25 MILP icin ALPHA=1, BETA=10, GAMMA=5 dengesi MILP solver'in
          enerji minimizasyonu egilimine uyum saglar. Ancak ALNS, V25
          decomposition'in S2 zone'unda yarattigi %71.5 kapsama sorununu
          hedef alir. Bu yuzden:
            - alpha' = 0.1   (V19'da 1.0,   10x azaltildi)
            - beta'  = 100   (V19'da 10,    10x arttirildi)
            - gamma' = 0     (V19'da 5,     kaldirildi - kapsama icin)
            - zeta'  = 1000  (YENI: ihlal eden hex sayisi cezasi)
          
          Bu, ALNS'in kapsama maksimizasyonu odakli olmasini saglar.
          Referans: Ropke & Pisinger 2006, Cabreira et al. 2019 - obj
          fonksiyon domain-tuning standartlari.
        """
        if self._cached_objective is not None:
            return self._cached_objective

        # ALNS-tuned parametreler (V19 ile farkli, bilincli kalibrasyon)
        ALPHA_ALNS = 0.1
        BETA_ALNS  = 100.0
        ZETA_ALNS  = 1000.0

        energy_total = self.compute_energy_total()
        _, risk_weighted_slack = self.compute_slack_total()

        # Ihlal eden hex sayisi (kapsama-odakli ek terim)
        n_violations = 0
        for hex_id, info in self.hex_info.items():
            visits = self.hex_visits.get(hex_id, [])
            if len(visits) == 0:
                n_violations += 1
                continue
            avg_interval = (self.cfg.TOTAL_HOURS * 60) / len(visits)
            if avg_interval > info["max_interval"]:
                n_violations += 1

        obj = (ALPHA_ALNS * energy_total
               + BETA_ALNS * risk_weighted_slack
               + ZETA_ALNS * n_violations)

        self._cached_objective = obj
        return obj

    # ----- KPI tablosu -----
    def get_kpis(self):
        if self._cached_kpis is not None:
            return self._cached_kpis

        total_hex = len(self.hex_info)
        visited_hex = sum(1 for h in self.hex_info
                          if len(self.hex_visits.get(h, [])) > 0)

        violations = 0
        for hex_id, info in self.hex_info.items():
            visits = self.hex_visits.get(hex_id, [])
            if len(visits) == 0:
                violations += 1
                continue
            avg_interval = (self.cfg.TOTAL_HOURS * 60) / len(visits)
            if avg_interval > info["max_interval"]:
                violations += 1

        slack_total, risk_weighted = self.compute_slack_total()

        # Zone bazli kapsama
        zone_coverage = {}
        for z in ["S1", "S2", "S3"]:
            zone_hex = [h for h, i in self.hex_info.items() if i["zone"] == z]
            visited = [h for h in zone_hex if len(self.hex_visits.get(h, [])) > 0]
            zone_coverage[z] = {
                "total": len(zone_hex),
                "visited": len(visited),
                "pct": 100 * len(visited) / max(1, len(zone_hex)),
            }

        kpis = {
            "total_hex": total_hex,
            "visited_hex": visited_hex,
            "coverage_pct": 100 * visited_hex / total_hex,
            "violations": violations,
            "slack_total": slack_total,
            "risk_weighted_slack": risk_weighted,
            "visit_reward": self.compute_visit_reward(),
            "objective": self.evaluate(),
            "zone_coverage": zone_coverage,
        }
        self._cached_kpis = kpis
        return kpis

    # ----- Feasibility check (HARD) -----
    def is_feasible(self, verbose=False):
        """V19'un tum kisitlarini Python'da kontrol et."""
        errors = []

        # 1. Enerji: E_MIN <= E[d,t] <= E_CAP
        for dkey, info in self.drones.items():
            for t in range(self.cfg.T_TOTAL + 1):
                e = info["energy"][t]
                if e < self.cfg.E_MIN - 0.01:
                    errors.append(f"E1: {dkey} t={t} E={e:.2f} < E_MIN")
                if e > self.cfg.E_CAP + 0.01:
                    errors.append(f"E2: {dkey} t={t} E={e:.2f} > E_CAP")

        # 2. Carpisma: ayni hex'te ayni t'de iki dron olamaz
        for t in range(self.cfg.T_TOTAL + 1):
            pos = defaultdict(list)
            for dkey, info in self.drones.items():
                loc = info["schedule"][t]
                if loc and loc not in STATIONS:
                    pos[loc].append(dkey)
            for hex_id, drones_here in pos.items():
                if len(drones_here) > 1:
                    errors.append(f"C1: t={t} hex={hex_id} dronlar={drones_here}")

        # 3. Soket kapasitesi
        for t in range(self.cfg.T_TOTAL):
            charging = defaultdict(int)
            for dkey, info in self.drones.items():
                if info["actions"][t] == "charge":
                    charging[info["home"]] += 1
            for stn, c in charging.items():
                if c > self.cfg.CAP_S:
                    errors.append(f"S1: t={t} stn={stn} c={c} > CAP_S")

        # 4. Zone-locked
        for dkey, info in self.drones.items():
            for t in range(self.cfg.T_TOTAL):
                if info["actions"][t] == "visit":
                    loc = info["schedule"][t]
                    if loc in self.hex_info:
                        if self.hex_info[loc]["zone"] != dkey[0]:
                            errors.append(f"Z1: {dkey} ziyaret {loc} (zone {self.hex_info[loc]['zone']})")

        # 5. Cycle close: final enerji E_CAP
        for dkey, info in self.drones.items():
            if info["energy"][self.cfg.T_TOTAL] < self.cfg.E_CAP - 0.5:
                errors.append(f"CC: {dkey} t=144 E={info['energy'][self.cfg.T_TOTAL]:.1f} < E_CAP")

        # 6. Blok sonu: istasyonda
        for dkey, info in self.drones.items():
            home = info["home"]
            for b in range(1, self.cfg.N_BLOCKS + 1):
                t_end = b * self.cfg.BLOCK_LEN
                if t_end <= self.cfg.T_TOTAL:
                    loc = info["schedule"][t_end]
                    if loc != home:
                        errors.append(f"B1: {dkey} blok {b} sonu {loc} != {home}")

        if verbose:
            if errors:
                print(f"[INFEASIBLE] {len(errors)} hata:")
                for e in errors[:15]:
                    print(f"  - {e}")
                if len(errors) > 15:
                    print(f"  ... ve {len(errors)-15} daha")
            else:
                print("[FEASIBLE] tum kisitlar saglandi.")

        return (len(errors) == 0, errors)

    # ----- Kopyalama -----
    def copy(self):
        """Derin kopya. ALNS destroy/repair'da kullanilir."""
        new_sol = Solution(self.cfg)
        new_sol.drones = deepcopy(self.drones)
        new_sol.hex_visits = defaultdict(list,
                                          {k: list(v) for k, v in self.hex_visits.items()})
        new_sol.hex_info = self.hex_info      # readonly, paylasilabilir
        new_sol.coords = self.coords          # readonly
        return new_sol

    # ----- Ozet -----
    def summary(self):
        kpis = self.get_kpis()
        return (f"Solution[drones={len(self.drones)}, "
                f"hex={kpis['total_hex']}, "
                f"kapsama={kpis['coverage_pct']:.1f}%, "
                f"ihlal={kpis['violations']}, "
                f"slack={kpis['slack_total']:.0f}, "
                f"obj={kpis['objective']:.0f}]")
