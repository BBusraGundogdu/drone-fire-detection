# -*- coding: utf-8 -*-
"""
================================================================================
DESTROY_OPS.PY  --  ALNS Destroy Operatorleri (Day 3 / parca 1)
================================================================================
Amac:
  ALNS'in 'destroy' fazinda cozumden bir kismi kaldiran operatorler.
  Her operator BIR LISTESI uretir: silinen ziyaretlerin (hex, t) tuple'lari.

Cikti formati:
  destroyed_visits = [(hex_id, t, drone_key), ...]
  
  Solution objesi modifiye edilir (in-place):
  - schedule[t] = home (drone eve doner)
  - actions[t] = "idle"
  - hex_visits[hex_id].remove(t)

4 Destroy Operatoru:
  D1) Random Destroy        - rastgele %r ziyareti sil (en basit)
  D2) Worst Removal         - en cok slack ureten ziyaretleri sil
  D3) Spatial (Shaw) Destroy- birbirine yakin bir grup hex'i sil
  D4) Time Window Destroy   - belirli bir 2-saatlik blogun ziyaretlerini sil

Tasarim notlari:
  - destroy_rate: Cozumun %10-40'i silinir (parametre)
  - Zone-locked korunur: her operator zone-aware
  - Operatorler Solution'i degil, FONKSIYON olarak yazildi (state tutmazlar)
================================================================================
"""

import random
from collections import defaultdict


# ==============================================================================
# YARDIMCI: ziyaret silme islemi
# ==============================================================================
def remove_visit(sol, hex_id, t, drone_key):
    """
    Bir ziyareti Solution'dan sil. Tutarli sekilde 3 listeyi guncelle:
    schedule[t], actions[t], hex_visits[hex_id]
    """
    info = sol.drones[drone_key]
    home = info["home"]

    # Schedule: drone evine gonder
    info["schedule"][t] = home

    # Action: idle
    if info["actions"][t] == "visit":
        info["actions"][t] = "idle"

    # Hex_visits: bu zamani sil
    if t in sol.hex_visits[hex_id]:
        sol.hex_visits[hex_id].remove(t)

    sol.invalidate_cache()


# ==============================================================================
# D1: RANDOM DESTROY
# ==============================================================================
def random_destroy(sol, destroy_rate=0.20, rng=None):
    """
    Cozumdeki tum ziyaretlerin yaklasik %destroy_rate kadarini rastgele sil.
    En basit operator, baseline. Lokal optimumdan cikmak icin guclu.
    """
    if rng is None:
        rng = random.Random()

    # Tum ziyaretleri topla: (hex, t, drone_key)
    all_visits = []
    for dkey, info in sol.drones.items():
        for t in range(sol.cfg.T_TOTAL):
            if info["actions"][t] == "visit":
                hex_id = info["schedule"][t]
                if hex_id in sol.hex_info:
                    all_visits.append((hex_id, t, dkey))

    if not all_visits:
        return []

    n_to_destroy = max(1, int(len(all_visits) * destroy_rate))
    selected = rng.sample(all_visits, min(n_to_destroy, len(all_visits)))

    for hex_id, t, dkey in selected:
        remove_visit(sol, hex_id, t, dkey)

    return selected


# ==============================================================================
# D2: WORST REMOVAL
# ==============================================================================
def worst_removal(sol, destroy_rate=0.20, rng=None, noise_factor=0.3):
    """
    En 'kotu' ziyaretleri sil. Kotuluk olcusu:
    
      cost(hex, t) = hover_cost (sabit)
                   + travel_cost (yakindaki istasyon mesafesi)
                   - risk_value (riski yuksekse iyi)
                   - urgency (ihlal yaklasiyorsa iyi)
    
    Yuksek cost = kotu ziyaret = silmeye aday.
    
    noise_factor: deterministik degil, biraz rastgelilik ekle (ALNS standartlari)
    """
    if rng is None:
        rng = random.Random()

    # Tau profilini bir kere hesapla
    tau = sol.compute_tau_profile()

    visit_costs = []
    for dkey, info in sol.drones.items():
        home = info["home"]
        for t in range(sol.cfg.T_TOTAL):
            if info["actions"][t] == "visit":
                hex_id = info["schedule"][t]
                if hex_id not in sol.hex_info:
                    continue

                hex_data = sol.hex_info[hex_id]
                risk = hex_data["risk"]
                mi = hex_data["max_interval"]

                # 1) Hover sabit
                hover_cost = sol.cfg.D_HOVER * sol.cfg.HOVER_TIME_MIN

                # 2) Risk yuksekse iyi (silme cazibesi azalir)
                risk_bonus = risk * sol.cfg.GAMMA

                # 3) Urgency: ihlal yaklasiyorsa ziyaret cok degerli, silmeyelim
                urgency = 0.0
                if t < sol.cfg.T_TOTAL:
                    urgency = max(0, mi - tau[hex_id][t]) / mi  # 0..1
                    # 0 = ihlal var (acil), 1 = taze ziyaret (silinebilir)

                cost = hover_cost - risk_bonus + urgency * 100

                # Noise (deterministik degil)
                cost *= (1 + rng.uniform(-noise_factor, noise_factor))

                visit_costs.append((cost, hex_id, t, dkey))

    if not visit_costs:
        return []

    # En kotu (cost en buyuk) %destroy_rate kadarini sec
    visit_costs.sort(key=lambda x: -x[0])
    n_to_destroy = max(1, int(len(visit_costs) * destroy_rate))
    selected_full = visit_costs[:n_to_destroy]
    selected = [(h, t, d) for c, h, t, d in selected_full]

    for hex_id, t, dkey in selected:
        remove_visit(sol, hex_id, t, dkey)

    return selected


# ==============================================================================
# D3: SPATIAL (SHAW) DESTROY
# ==============================================================================
def spatial_destroy(sol, destroy_rate=0.20, rng=None):
    """
    Rastgele bir hex sec, ona en yakin K hex'i bul, tum bu hex'lerin
    ziyaretlerini sil. Ayni cografi bolgenin rotalarini yeniden planlama
    firsati yaratir.
    
    Shaw (1997) onerisinin coverage path planning versiyonu.
    """
    if rng is None:
        rng = random.Random()

    # Toplam silinecek ziyaret hedefi
    total_visits = sum(1 for dkey, info in sol.drones.items()
                       for t in range(sol.cfg.T_TOTAL)
                       if info["actions"][t] == "visit")
    n_target = max(1, int(total_visits * destroy_rate))

    # Rastgele bir hex sec (ziyaret edilmis olanlardan)
    visited_hex_ids = [h for h, visits in sol.hex_visits.items() if visits]
    if not visited_hex_ids:
        return []

    seed_hex = rng.choice(visited_hex_ids)
    if seed_hex not in sol.coords:
        return []

    # Seed'in zone'undaki tum hex'leri mesafeye gore sirala
    seed_zone = sol.hex_info[seed_hex]["zone"]
    seed_x, seed_y = sol.coords[seed_hex]

    same_zone_hex = []
    for h, info in sol.hex_info.items():
        if info["zone"] == seed_zone and h in sol.coords:
            x, y = sol.coords[h]
            d = ((x - seed_x) ** 2 + (y - seed_y) ** 2) ** 0.5
            same_zone_hex.append((d, h))

    same_zone_hex.sort()

    # En yakin hex'lerden baslayarak destroy_rate'e kadar topla
    destroyed = []
    for _, h in same_zone_hex:
        if len(destroyed) >= n_target:
            break
        # Bu hex'in tum ziyaretlerini topla
        for dkey, info in sol.drones.items():
            for t in range(sol.cfg.T_TOTAL):
                if info["actions"][t] == "visit" and info["schedule"][t] == h:
                    destroyed.append((h, t, dkey))

    # Hedefe ulastiysak fazlaligi kirp
    if len(destroyed) > n_target:
        destroyed = destroyed[:n_target]

    for hex_id, t, dkey in destroyed:
        remove_visit(sol, hex_id, t, dkey)

    return destroyed


# ==============================================================================
# D4: TIME WINDOW DESTROY
# ==============================================================================
def time_window_destroy(sol, destroy_rate=None, rng=None):
    """
    Rastgele bir 2-saatlik blok sec, o bloktaki TUM ziyaretleri sil.
    destroy_rate burada blok seciminden tetiklenir (~%8 = 1/12 blok).
    
    Blok geciaslerindeki verimsizlikleri cozer.
    """
    if rng is None:
        rng = random.Random()

    # Rastgele bir blok sec (1..12)
    selected_block = rng.randint(1, sol.cfg.N_BLOCKS)
    t_start = (selected_block - 1) * sol.cfg.BLOCK_LEN
    t_end = selected_block * sol.cfg.BLOCK_LEN

    destroyed = []
    for dkey, info in sol.drones.items():
        for t in range(t_start, min(t_end, sol.cfg.T_TOTAL)):
            if info["actions"][t] == "visit":
                hex_id = info["schedule"][t]
                if hex_id in sol.hex_info:
                    destroyed.append((hex_id, t, dkey))

    for hex_id, t, dkey in destroyed:
        remove_visit(sol, hex_id, t, dkey)

    return destroyed


# ==============================================================================
# OPERATOR KAYIT
# ==============================================================================
DESTROY_OPERATORS = {
    "random":       random_destroy,
    "worst":        worst_removal,
    "spatial":      spatial_destroy,
    "time_window":  time_window_destroy,
}


def get_destroy_op(name):
    """Operatoru ismi ile getir."""
    if name not in DESTROY_OPERATORS:
        raise ValueError(f"Bilinmeyen destroy operatoru: {name}. "
                         f"Mevcut: {list(DESTROY_OPERATORS.keys())}")
    return DESTROY_OPERATORS[name]
