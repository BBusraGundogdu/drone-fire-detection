# -*- coding: utf-8 -*-
"""
================================================================================
WARM_START.PY  --  V25 Aggregate ciktisini Solution objesi olarak yukle
================================================================================
Amac:
  V25 spatial decomposition matheuristic'in 3 zone ciktilarini ALNS'in
  baslangic cozumu olarak Solution objesine ceviren modul.

Girdiler:
  - aggregate_hareketler.csv : (zone, dron, blok, t, kaynak, hedef)
  - aggregate_denetimler.csv : (zone, dron, blok, t, hex, risk, max_interval)
  - aggregate_hex_ziyaret.csv: hex bazli ozet
  - Altigen_Verileri_Duzenli.csv: 340 hex risk + koordinat verisi
  - 340hex_{S1,S2,S3}_zone.csv: zone atamasi

Cikti:
  Solution objesi (drones, hex_visits, hex_info doldurulmus)

Yapilanlar:
  1) hex_info dict'i kurulur (340 hex, zone atamasi + risk + max_interval)
  2) 31 dron tanimlanir (S1: 10, S2: 11+D12 ekstra, S3: 10)
  3) Her dron icin 144 dilim schedule + 145 nokta energy doldurulur
  4) hex_visits sozlugu doldurulur

Notlar:
  - D12-S2 V25'te kullanilmadi ama ALNS'e dahil edilir (idle dron olarak baslar)
  - Schedule dolarken: hareket suresince at[d,j,t_arr] = 1, ara dilimlerde
    drone yolda kabul edilir (visit DEGIL)
================================================================================
"""

import os
import math
import pandas as pd
from collections import defaultdict

from solution import Solution, Config, STATIONS, compute_max_interval


# ==============================================================================
# YAPILANDIRMA
# ==============================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Aggregate klasor yolu (sezgisel/aggregate/ varsayilani)
DEFAULT_AGGREGATE_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "aggregate")
DEFAULT_HEX_CSV = os.path.join(os.path.dirname(SCRIPT_DIR),
                                "Altigen_Verileri_Duzenli.csv")
DEFAULT_ZONE_CSV_DIR = os.path.dirname(SCRIPT_DIR)


# Zone bazli dron sayilari (V25 v25.2 konfigurasyonu)
ZONE_DRONE_COUNTS = {
    "S1": 10,
    "S2": 12,   # D12 dahil (V25'te kullanilmadi ama ALNS'e dahil)
    "S3": 10,
}


# ==============================================================================
# YARDIMCI FONKSIYONLAR
# ==============================================================================
def load_hex_info(hex_csv_path, zone_csv_dir, cfg):
    """340 hex icin (zone, risk, max_interval, koordinat) bilgisini yukle."""
    df_main = pd.read_csv(hex_csv_path)
    df_main.columns = df_main.columns.str.strip().str.replace("\ufeff", "")

    # Risk min/max (max_interval icin)
    risk_min = df_main["TOPLAM_RISK"].min()
    risk_max = df_main["TOPLAM_RISK"].max()

    # Hex -> zone atamasi
    hex_to_zone = {}
    for z in ["S1", "S2", "S3"]:
        zpath = os.path.join(zone_csv_dir, f"340hex_{z}_zone.csv")
        if not os.path.exists(zpath):
            raise FileNotFoundError(f"Zone CSV yok: {zpath}")
        zdf = pd.read_csv(zpath)
        zdf.columns = zdf.columns.str.strip().str.replace("\ufeff", "")
        for _, row in zdf.iterrows():
            hex_to_zone[f"H{int(row['id'])}"] = z

    # Lokal projeksiyon
    lat_ref = df_main["Latitude"].mean()
    lon_ref = df_main["Longitude"].mean()
    m_per_lon = 111_000 * math.cos(math.radians(lat_ref))

    hex_info = {}
    coords = {}
    for _, row in df_main.iterrows():
        hid = f"H{int(row['id'])}"
        if hid not in hex_to_zone:
            continue  # zone atamasi olmayan hex'i atla (olmamali ama)
        risk = float(row["TOPLAM_RISK"])
        hex_info[hid] = {
            "zone": hex_to_zone[hid],
            "risk": risk,
            "max_interval": compute_max_interval(risk, risk_min, risk_max, cfg),
            "lat": row["Latitude"],
            "lon": row["Longitude"],
        }
        coords[hid] = (
            (row["Longitude"] - lon_ref) * m_per_lon,
            (row["Latitude"]  - lat_ref) * 111_000,
        )

    # Istasyon koordinatlari
    for sid, st in STATIONS.items():
        coords[sid] = (
            (st["lon"] - lon_ref) * m_per_lon,
            (st["lat"] - lat_ref) * 111_000,
        )

    return hex_info, coords, lat_ref, lon_ref, m_per_lon


def init_drone_schedules(zone_drone_counts, cfg):
    """31 dron icin baslangic schedule yapisini kur (istasyonda, dolu batarya)."""
    drones = {}
    for zone, n_drones in zone_drone_counts.items():
        for i in range(1, n_drones + 1):
            dkey = (zone, f"D{i}")
            drones[dkey] = {
                "home": zone,
                # Schedule: 145 nokta (t=0..144). Baslangic: tum dilimler istasyonda
                "schedule": [zone] * (cfg.T_TOTAL + 1),
                # Energy: 145 nokta. Baslangic: hep E_CAP
                "energy":   [cfg.E_CAP] * (cfg.T_TOTAL + 1),
                # Actions: 144 dilim (t=0..143). Baslangic: idle
                "actions":  ["idle"] * cfg.T_TOTAL,
            }
    return drones


def parse_aggregate_hareketler(har_df, drones, cfg):
    """
    Hareketler CSV'sini schedule'a yansit.
    Bir hareket: (zone, dron, blok, t_blok_ici, kaynak, hedef)
    Global t = (blok-1) * BLOCK_LEN + t_blok_ici

    NOT: hareket suresi V19'da tau_step ile hesaplaniyordu. Aggregate'te
    sadece kaynak/hedef var. Mesafeyi koordinattan hesaplayip dilim sayisini
    cikariyoruz.
    """
    # Once siralayalim
    har_df = har_df.sort_values(['zone', 'dron', 'blok', 't_dilim_blok_ici'])

    for _, row in har_df.iterrows():
        zone = row['zone']
        drone_id = row['dron']
        dkey = (zone, drone_id)
        if dkey not in drones:
            continue

        blok = int(row['blok'])
        t_blok = int(row['t_dilim_blok_ici'])
        t_global = (blok - 1) * cfg.BLOCK_LEN + t_blok
        kaynak = row['kaynak']
        hedef = row['hedef']

        # Hareket: t_global aninda kaynak'tan ayrilir, t_global+tau'da hedef'e varir
        # tau'yu hesaplayamadigimiz icin VARSAYIM:
        #   Aggregate sadece ziyaret/durus bilgisi verir, hareket arasi
        #   bilgi yok. Bu yuzden t_global aninda hareket basliyor, bir sonraki
        #   blok_ici dilimde hedef'e varilmis varsayariz.
        # Bu, schedule'i basit tutar, repair operatorleri istenirse netletir.

        if t_global < cfg.T_TOTAL:
            drones[dkey]["actions"][t_global] = "move"
            # Schedule: t_global'da kaynak, t_global+1'de hedef
            if t_global + 1 <= cfg.T_TOTAL:
                # Schedule'i guncellemek icin: ara dilimlerde hedef
                # (gercek tau'yu burada hesaplayamadik, kabaca 1 dilim varsayariz)
                drones[dkey]["schedule"][t_global] = kaynak
                drones[dkey]["schedule"][t_global + 1] = hedef


def parse_aggregate_denetimler(den_df, drones, hex_visits, cfg):
    """Denetimler CSV'sini schedule + hex_visits'e yansit."""
    den_df = den_df.sort_values(['zone', 'dron', 'blok', 't_dilim_blok_ici'])

    for _, row in den_df.iterrows():
        zone = row['zone']
        drone_id = row['dron']
        dkey = (zone, drone_id)
        if dkey not in drones:
            continue

        blok = int(row['blok'])
        t_blok = int(row['t_dilim_blok_ici'])
        t_global = (blok - 1) * cfg.BLOCK_LEN + t_blok
        hex_id = row['hex']

        if t_global < cfg.T_TOTAL:
            # Visit action olarak isaretle (move uzerine yazma)
            if drones[dkey]["actions"][t_global] == "idle":
                drones[dkey]["actions"][t_global] = "visit"
            drones[dkey]["schedule"][t_global] = hex_id

        hex_visits[hex_id].append(t_global)


def compute_energy_from_schedule(drones, coords, cfg):
    """V25'in matematigine gore enerji bilancosunu yeniden hesapla."""
    m_per_lon_approx = 87_858  # ~38.18 latitude
    hover_per_visit = cfg.D_HOVER * cfg.HOVER_TIME_MIN

    for dkey, info in drones.items():
        E = cfg.E_CAP  # Baslangic
        info["energy"][0] = E
        for t in range(cfg.T_TOTAL):
            action = info["actions"][t]
            delta = 0.0
            if action == "move":
                src = info["schedule"][t]
                dst = info["schedule"][t + 1] if t + 1 < len(info["schedule"]) else src
                if src in coords and dst in coords and src != dst:
                    xa, ya = coords[src]
                    xb, yb = coords[dst]
                    d_m = math.hypot(xa - xb, ya - yb)
                    t_min = d_m / cfg.V_CRUISE / 60.0
                    delta = -cfg.D_FLIGHT * t_min
            elif action == "visit":
                delta = -hover_per_visit
            elif action == "charge":
                delta = cfg.C_RATE * cfg.DELTA_T

            E = max(cfg.E_MIN, min(cfg.E_CAP, E + delta))
            info["energy"][t + 1] = E


def add_charging_actions(drones, hex_visits, cfg):
    """
    V19'da explicit charging variable var. Aggregate'te bu bilgi yok
    (sarjlar.csv ayri ama bagimsiz). Basit yaklasim:

    Eger bir dron istasyonda durup hicbir hareket yapmiyorsa "idle".
    ALNS sirasinda bu "idle"lari "charge"a cevirip enerji geri yukleyecek.

    Su anki implementasyon: ALL idle while at home = charge.
    """
    for dkey, info in drones.items():
        home = info["home"]
        for t in range(cfg.T_TOTAL):
            if info["actions"][t] == "idle" and info["schedule"][t] == home:
                # Eger sonraki dilimde de ev'de degilse muhtemelen carj
                # Basit kural: home'da idle = potansiyel charge
                # Ama enerji E_CAP'tan dusukse charge yap
                if info["energy"][t] < cfg.E_CAP - 0.5:
                    info["actions"][t] = "charge"


# ==============================================================================
# ANA YUKLEME FONKSIYONU
# ==============================================================================
def load_v25_warmstart(aggregate_dir=None, hex_csv=None, zone_csv_dir=None,
                       cfg=None, verbose=True):
    """
    V25 aggregate ciktilarindan Solution objesi insa et.

    Returns:
        Solution objesi (warmstart icin hazir)
    """
    if cfg is None:
        cfg = Config()
    if aggregate_dir is None:
        aggregate_dir = DEFAULT_AGGREGATE_DIR
    if hex_csv is None:
        hex_csv = DEFAULT_HEX_CSV
    if zone_csv_dir is None:
        zone_csv_dir = DEFAULT_ZONE_CSV_DIR

    if verbose:
        print("=" * 78)
        print(" V25 WARMSTART YUKLEME")
        print("=" * 78)
        print(f"  Aggregate dir : {aggregate_dir}")
        print(f"  Hex CSV       : {hex_csv}")
        print(f"  Zone CSV dir  : {zone_csv_dir}")

    # 1. Hex info yukle
    hex_info, coords, lat_ref, lon_ref, m_per_lon = load_hex_info(
        hex_csv, zone_csv_dir, cfg
    )
    if verbose:
        print(f"\n[1/5] Hex bilgisi yuklendi: {len(hex_info)} hex")
        z_counts = defaultdict(int)
        for h, info in hex_info.items():
            z_counts[info["zone"]] += 1
        for z, c in sorted(z_counts.items()):
            print(f"      {z}: {c} hex")

    # 2. Dron yapilarini kur (idle baslangic)
    drones = init_drone_schedules(ZONE_DRONE_COUNTS, cfg)
    if verbose:
        print(f"\n[2/5] Dron yapilari kuruldu: {len(drones)} dron")
        for z, n in ZONE_DRONE_COUNTS.items():
            print(f"      {z}: {n} dron")

    # 3. Aggregate CSV'leri oku
    har_path = os.path.join(aggregate_dir, "aggregate_hareketler.csv")
    den_path = os.path.join(aggregate_dir, "aggregate_denetimler.csv")
    har_df = pd.read_csv(har_path)
    den_df = pd.read_csv(den_path)
    if verbose:
        print(f"\n[3/5] Aggregate CSV'leri okundu:")
        print(f"      Hareketler: {len(har_df)} satir")
        print(f"      Denetimler: {len(den_df)} satir")

    # 4. Schedule'lara parse et
    hex_visits = defaultdict(list)
    parse_aggregate_hareketler(har_df, drones, cfg)
    parse_aggregate_denetimler(den_df, drones, hex_visits, cfg)
    if verbose:
        n_visits = sum(len(v) for v in hex_visits.values())
        print(f"\n[4/5] Schedule'lar dolduruldu:")
        print(f"      Toplam ziyaret: {n_visits}")
        print(f"      Ziyaret edilen hex: {len(hex_visits)}")

    # 5. Enerji + charging hesapla
    add_charging_actions(drones, hex_visits, cfg)
    compute_energy_from_schedule(drones, coords, cfg)
    if verbose:
        print(f"\n[5/5] Enerji + charging hesaplandi.")

    # Solution objesini olustur
    sol = Solution(cfg)
    sol.drones = drones
    sol.hex_visits = hex_visits
    sol.hex_info = hex_info
    sol.coords = coords

    if verbose:
        print(f"\n" + "=" * 78)
        print(" WARMSTART OZETI")
        print("=" * 78)
        print(f"  {sol.summary()}")
        kpis = sol.get_kpis()
        for z, zc in kpis["zone_coverage"].items():
            print(f"  {z}: {zc['visited']}/{zc['total']} ({zc['pct']:.1f}%) kapsama")

    return sol


# ==============================================================================
# CLI / TEST
# ==============================================================================
if __name__ == "__main__":
    import sys
    aggregate_dir = sys.argv[1] if len(sys.argv) > 1 else None
    hex_csv = sys.argv[2] if len(sys.argv) > 2 else None
    zone_csv_dir = sys.argv[3] if len(sys.argv) > 3 else None

    sol = load_v25_warmstart(aggregate_dir, hex_csv, zone_csv_dir, verbose=True)

    print("\n" + "=" * 78)
    print(" FEASIBILITY KONTROLU")
    print("=" * 78)
    feasible, errors = sol.is_feasible(verbose=True)
    if not feasible:
        print(f"\n>> NOT: warmstart'in feasibility hatalari V19'un tam matematigini")
        print(f"   Python'da yeniden insa etmenin yumusakliklarindan kaynaklanir.")
        print(f"   ALNS bunlari iterasyonlarda duzeltecek (Day 3 sonrasi).")
