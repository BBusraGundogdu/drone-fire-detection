# -*- coding: utf-8 -*-
"""
================================================================================
REPAIR_OPS.PY  --  ALNS Repair Operatorleri (Day 3 / parca 2)
================================================================================
Amac:
  Destroy fazindan sonra ziyaretsiz kalan (hex, t) alanlarini yeniden
  doldur. Her repair operatoru farkli bir oncelik mantigi izler.

4 Repair Operatoru:
  R1) Greedy Insertion        - en yuksek riskli hex'leri once ekle
  R2) Regret Insertion        - "kacirilirsa en cok kaybedileni" ekle
  R3) Risk-Priority Insertion - bizim probleme ozel: risk x urgency
  R4) Random Insertion        - rastgele yer (ALNS standartlari)

Tasarim notlari:
  - Zone-locked: drone sadece kendi zone'unun hex'lerine eklenebilir
  - Hard feasibility: enerji, carpisma kontrolu yapilir
  - Eklenecek ziyaret BIRBIRINI ETKILER, sirayla ekleyince state izlenmeli
  - Idle bir drona ziyaret eklemek = schedule[t]=hex, actions[t]="visit"
================================================================================
"""

import random
from collections import defaultdict


# ==============================================================================
# YARDIMCI: ziyaret ekle (in-place)
# ==============================================================================
def add_visit(sol, hex_id, t, drone_key):
    """
    Bir ziyareti Solution'a ekle. Tutarli sekilde 3 listeyi guncelle.
    
    Onkosul: 
    - drone_key kendi zone'unda olmali (zone-locked)
    - t aninda drone idle olmali
    - hex baska bir drone tarafindan ziyaret edilmiyor olmali (carpisma)
    
    Donder: True basariliysa, False engellenmisse
    """
    info = sol.drones[drone_key]

    # 1. Zone-locked check
    if not sol.is_zone_allowed(drone_key, hex_id):
        return False

    # 2. t valid mi?
    if t < 0 or t >= sol.cfg.T_TOTAL:
        return False

    # 3. Drone t aninda idle/home'da mi?
    if info["actions"][t] != "idle":
        return False
    if info["schedule"][t] != info["home"]:
        return False

    # 4. Carpisma: bu hex baska bir dron tarafindan ziyaret ediliyor mu?
    for other_dkey, other_info in sol.drones.items():
        if other_dkey == drone_key:
            continue
        if other_info["schedule"][t] == hex_id:
            return False  # carpisma

    # Ekle
    info["schedule"][t] = hex_id
    info["actions"][t] = "visit"
    if t not in sol.hex_visits[hex_id]:
        sol.hex_visits[hex_id].append(t)
        sol.hex_visits[hex_id].sort()

    sol.invalidate_cache()
    return True


def find_available_drones(sol, hex_id, t):
    """
    Verilen (hex, t) icin uygun dronlari bul.
    - Kendi zone'unda olmali
    - t aninda idle olmali
    """
    if hex_id not in sol.hex_info:
        return []
    hex_zone = sol.hex_info[hex_id]["zone"]

    candidates = []
    for dkey, info in sol.drones.items():
        if dkey[0] != hex_zone:
            continue
        if t < sol.cfg.T_TOTAL and info["actions"][t] == "idle" \
                and info["schedule"][t] == info["home"]:
            candidates.append(dkey)
    return candidates


# ==============================================================================
# R1: GREEDY INSERTION
# ==============================================================================
def greedy_insertion(sol, destroyed_list=None, rng=None):
    """
    Silinen ziyaretleri RISK SIRASINA gore yeniden ekle.
    Her ziyaret icin en uygun (drone, t) ciftini bul.
    
    destroyed_list parametre olarak gelirse oncelikli olarak onlar tamamlanir,
    ekstra olarak hicbir zaman ziyaret edilmemis yuksek riskli hex'ler de eklenir.
    """
    if rng is None:
        rng = random.Random()

    # Insertion adaylari: ziyaret edilmesi gereken (hex_id, oncelik) listesi
    # Oncelik = risk
    candidates = []

    # 1. Destroyed list'teki hex'ler oncelikli
    seen_hex_t = set()
    if destroyed_list:
        for hex_id, t, _ in destroyed_list:
            if (hex_id, t) not in seen_hex_t:
                seen_hex_t.add((hex_id, t))
                if hex_id in sol.hex_info:
                    candidates.append((hex_id, t, sol.hex_info[hex_id]["risk"]))

    # 2. Ziyaret edilmemis veya az ziyaret edilmis hex'ler
    for hex_id, info in sol.hex_info.items():
        n_visits = len(sol.hex_visits.get(hex_id, []))
        if n_visits == 0:
            # 24 saatte 1-3 kez ziyaret edilmesi gereken hex
            for t in range(0, sol.cfg.T_TOTAL, sol.cfg.BLOCK_LEN):
                if (hex_id, t) not in seen_hex_t:
                    seen_hex_t.add((hex_id, t))
                    candidates.append((hex_id, t, info["risk"]))

    # Risk sirasina gore sirala (yuksek risk once)
    candidates.sort(key=lambda x: -x[2])

    inserted = []
    for hex_id, t, _ in candidates:
        drones_avail = find_available_drones(sol, hex_id, t)
        if not drones_avail:
            continue
        # En yakin drone (basitlik icin ilk uygun olani al)
        chosen = drones_avail[0]
        if add_visit(sol, hex_id, t, chosen):
            inserted.append((hex_id, t, chosen))

    return inserted


# ==============================================================================
# R2: REGRET INSERTION
# ==============================================================================
def regret_insertion(sol, destroyed_list=None, k=2, rng=None):
    """
    Her aday icin: en iyi yerlestirme - 2.en iyi yerlestirme = regret
    Yuksek regret'li hex'i ONCE yerlestir (kacirsak cok kaybederiz).
    
    k=2: 2-regret (en iyi vs 2. en iyi)
    """
    if rng is None:
        rng = random.Random()

    # Adaylari topla
    candidates = set()
    if destroyed_list:
        for hex_id, t, _ in destroyed_list:
            if hex_id in sol.hex_info:
                candidates.add(hex_id)

    # Az ziyaret edilen hex'leri de ekle
    for hex_id, info in sol.hex_info.items():
        if len(sol.hex_visits.get(hex_id, [])) == 0:
            candidates.add(hex_id)

    candidates = list(candidates)
    inserted = []

    while candidates:
        # Her hex icin en iyi K yerlestirmeyi bul
        best_options = []
        for hex_id in candidates:
            risk = sol.hex_info[hex_id]["risk"]
            # En iyi 2 (drone, t) ciftini bul
            options = []
            for t in range(0, sol.cfg.T_TOTAL, 2):  # her 20 dk'da bir dene
                drones_avail = find_available_drones(sol, hex_id, t)
                if drones_avail:
                    # Cost ~ -risk (negative cost = good)
                    cost = -risk
                    options.append((cost, drones_avail[0], t))
            options.sort()
            if len(options) >= k:
                regret = options[k-1][0] - options[0][0]
                best_options.append((regret, hex_id, options[0]))
            elif options:
                # k'dan az secenek varsa, regret yuksek (yerine koyma firsati kisitli)
                best_options.append((1e9, hex_id, options[0]))

        if not best_options:
            break

        # En yuksek regret'i sec
        best_options.sort(key=lambda x: -x[0])
        regret, hex_id, (cost, dkey, t) = best_options[0]

        if add_visit(sol, hex_id, t, dkey):
            inserted.append((hex_id, t, dkey))
            # Bu hex'i tekrar ekleme ihtiyaci kalmadi
            candidates.remove(hex_id)
        else:
            candidates.remove(hex_id)  # eklenemedi, atla

    return inserted


# ==============================================================================
# R3: RISK-PRIORITY INSERTION (probleme ozel)
# ==============================================================================
def risk_priority_insertion(sol, destroyed_list=None, rng=None):
    """
    Bizim probleme OZEL operator: 
    Oncelik = risk x urgency_factor
    
    Urgency: max_interval doluluk oranina gore (yaklasik ihlal varsa
    daha aciliyetli). Soyut sezgisel olarak: hic ziyaret edilmemis veya
    cok seyrek ziyaret edilen yuksek riskli hex'ler en aciliyetli.
    """
    if rng is None:
        rng = random.Random()

    # Her hex icin oncelik skoru
    priorities = []
    for hex_id, info in sol.hex_info.items():
        risk = info["risk"]
        mi = info["max_interval"]
        n_visits = len(sol.hex_visits.get(hex_id, []))

        # Urgency: ziyaret edilmemisse 1.0, cok ziyaret edilmisse 0
        if n_visits == 0:
            urgency = 1.0
        else:
            avg_interval = (sol.cfg.TOTAL_HOURS * 60) / n_visits
            # avg_interval > max_interval ise urgent
            urgency = max(0, min(1.0, avg_interval / mi - 0.5))

        priority = risk * (1 + 2 * urgency)
        priorities.append((priority, hex_id))

    priorities.sort(key=lambda x: -x[0])

    # En yuksek oncelikli %30'unu yerlestir
    inserted = []
    to_process = priorities[:max(50, int(len(priorities) * 0.3))]

    for _, hex_id in to_process:
        # Bu hex icin uygun bir (drone, t) bul
        # 4 saat boyunca her 2 dakikada bir dene (cok hicbir slot deneme yetersiz)
        placed = False
        for t in range(0, sol.cfg.T_TOTAL):
            drones_avail = find_available_drones(sol, hex_id, t)
            if drones_avail:
                chosen = rng.choice(drones_avail)
                if add_visit(sol, hex_id, t, chosen):
                    inserted.append((hex_id, t, chosen))
                    placed = True
                    break
        # Yer bulunamadiysa atla
    return inserted


# ==============================================================================
# R4: RANDOM INSERTION
# ==============================================================================
def random_insertion(sol, destroyed_list=None, rng=None):
    """
    Silinen ziyaretleri rastgele yer yer yerlestir. ALNS standardı,
    diversification icin gerekli.
    """
    if rng is None:
        rng = random.Random()

    # Bos slotlari topla: (drone_key, t)
    empty_slots = []
    for dkey, info in sol.drones.items():
        for t in range(sol.cfg.T_TOTAL):
            if info["actions"][t] == "idle" and info["schedule"][t] == info["home"]:
                empty_slots.append((dkey, t))

    if not empty_slots:
        return []

    rng.shuffle(empty_slots)

    # Adaylar: ziyaret edilmemis hex'ler (zone bazinda gruplanmis)
    candidates_by_zone = defaultdict(list)
    for hex_id, info in sol.hex_info.items():
        if len(sol.hex_visits.get(hex_id, [])) == 0:
            candidates_by_zone[info["zone"]].append(hex_id)

    for zone in candidates_by_zone:
        rng.shuffle(candidates_by_zone[zone])

    inserted = []
    for dkey, t in empty_slots:
        zone = dkey[0]
        if not candidates_by_zone[zone]:
            continue
        hex_id = candidates_by_zone[zone][0]
        if add_visit(sol, hex_id, t, dkey):
            inserted.append((hex_id, t, dkey))
            candidates_by_zone[zone].pop(0)

    return inserted


# ==============================================================================
# OPERATOR KAYIT
# ==============================================================================
REPAIR_OPERATORS = {
    "greedy":         greedy_insertion,
    "regret":         regret_insertion,
    "risk_priority":  risk_priority_insertion,
    "random":         random_insertion,
}


def get_repair_op(name):
    if name not in REPAIR_OPERATORS:
        raise ValueError(f"Bilinmeyen repair operatoru: {name}. "
                         f"Mevcut: {list(REPAIR_OPERATORS.keys())}")
    return REPAIR_OPERATORS[name]
