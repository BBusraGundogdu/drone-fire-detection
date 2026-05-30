# -*- coding: utf-8 -*-
"""
================================================================================
AGGREGATE_ZONES.PY  --  3 Zone Sonucunu Birlestirme + Global Rapor
================================================================================
Amac:
  v25_zone_runner.py 3 kez koşturulduktan sonra (S1, S2, S3 icin) zone-spesifik
  sonuclari birlestirip tek bir "340 hex matheuristic" sonucu uretmek.

Cikti:
  1) aggregate_blok_ozet.csv   - Tum zone'larin blok ozetleri birlikte
  2) aggregate_hex_ziyaret.csv - 340 hex'in ziyaret istatistikleri
  3) aggregate_summary.txt     - Tek sayfa savunma metni (yapistirilabilir)
  4) console: Tezde kullanilabilir KPI'lar

Akademik degeri:
  - Toplam slack, kapsama, ihlal sayisi -> 110 hex N_d=9 baseline'a kiyaslama
  - Ne kadar suboptimal? (matheuristic'in kalite kaybi)
  - Toplam runtime -> "saf MILP'in 340 hex'te yetmedigi bilinen alanda
    matheuristic dakikalar mertebesinde sonuc verdi" mesaji
================================================================================
"""

import os
import pandas as pd
from datetime import datetime


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ZONES_BASE_DIR = SCRIPT_DIR
ZONE_IDS = ["S1", "S2", "S3"]

BASELINE_110_ND9 = {
    "n_hex": 110,
    "n_drones": 9,
    "slack_total": 635_904,
    "violations": 80,
    "coverage_pct": 100.0,
    "runtime_min": 107,
}


def load_zone_results(base_dir, zone_id):
    """Bir zone'un tum CSV ciktilarini yukle."""
    zone_dir = os.path.join(base_dir, f"zone_{zone_id}_results")
    if not os.path.isdir(zone_dir):
        raise FileNotFoundError(f"Zone {zone_id} sonuc dizini yok: {zone_dir}\n"
                                f"v25_zone_runner.py'yi ZONE_ID='{zone_id}' "
                                f"ile koşturmadiniz mi?")
    files = {}
    for fname in ["blok_ozet.csv", "hareketler.csv", "denetimler.csv",
                  "sarjlar.csv", "hex_ziyaret_ozet.csv", "soket_kullanim.csv"]:
        fpath = os.path.join(zone_dir, fname)
        if os.path.exists(fpath):
            files[fname] = pd.read_csv(fpath)
        else:
            print(f"  [UYARI] {zone_id}/{fname} bulunamadi")
            files[fname] = pd.DataFrame()
    return files


def compute_zone_kpi(zone_id, files):
    """Bir zone icin temel KPI'lar."""
    blok_ozet = files["blok_ozet.csv"]
    hex_ziyaret = files["hex_ziyaret_ozet.csv"]
    soket = files["soket_kullanim.csv"]

    if blok_ozet.empty or hex_ziyaret.empty:
        return None

    total_slack = blok_ozet["toplam_slack_dk"].sum()
    total_runtime = blok_ozet["runtime_s"].sum()
    n_blocks_solved = len(blok_ozet)

    total_hex = len(hex_ziyaret)
    visited_hex = len(hex_ziyaret[hex_ziyaret["ziyaret_sayisi"] > 0])
    violations = len(hex_ziyaret[hex_ziyaret["ihlal_riski"] == "VAR"])

    # Pik soket
    if not soket.empty:
        global_peak = soket.groupby("istasyon")["peak_blok_ici"].max().to_dict()
    else:
        global_peak = {}

    return {
        "zone": zone_id,
        "n_hex": total_hex,
        "visited_hex": visited_hex,
        "coverage_pct": 100 * visited_hex / total_hex if total_hex else 0,
        "violations": violations,
        "slack_total_dk": total_slack,
        "runtime_s": total_runtime,
        "runtime_min": total_runtime / 60,
        "n_blocks_solved": n_blocks_solved,
        "n_drones": blok_ozet[[c for c in blok_ozet.columns
                                if c.startswith("peak_")]].shape[1]
                    if not blok_ozet.empty else 0,
        "global_peak": global_peak,
    }


def aggregate_all(base_dir, zone_ids):
    """3 zone sonuclarini birlestir + global KPI."""
    all_files = {}
    zone_kpis = []
    for zid in zone_ids:
        files = load_zone_results(base_dir, zid)
        all_files[zid] = files
        kpi = compute_zone_kpi(zid, files)
        if kpi:
            zone_kpis.append(kpi)

    # Birlestirilmis CSV'ler
    out_dir = os.path.join(base_dir, "aggregate")
    os.makedirs(out_dir, exist_ok=True)

    combined_blok = pd.concat([all_files[z]["blok_ozet.csv"] for z in zone_ids
                                if not all_files[z]["blok_ozet.csv"].empty],
                                ignore_index=True)
    combined_blok.to_csv(os.path.join(out_dir, "aggregate_blok_ozet.csv"),
                          index=False, encoding="utf-8")

    combined_hex = pd.concat([all_files[z]["hex_ziyaret_ozet.csv"] for z in zone_ids
                              if not all_files[z]["hex_ziyaret_ozet.csv"].empty],
                              ignore_index=True)
    combined_hex.to_csv(os.path.join(out_dir, "aggregate_hex_ziyaret.csv"),
                        index=False, encoding="utf-8")

    combined_har = pd.concat([all_files[z]["hareketler.csv"] for z in zone_ids
                              if not all_files[z]["hareketler.csv"].empty],
                              ignore_index=True)
    combined_har.to_csv(os.path.join(out_dir, "aggregate_hareketler.csv"),
                        index=False, encoding="utf-8")

    combined_den = pd.concat([all_files[z]["denetimler.csv"] for z in zone_ids
                              if not all_files[z]["denetimler.csv"].empty],
                              ignore_index=True)
    combined_den.to_csv(os.path.join(out_dir, "aggregate_denetimler.csv"),
                        index=False, encoding="utf-8")

    return zone_kpis, out_dir


def print_report(zone_kpis, out_dir):
    """Konsol raporu + summary.txt yazimi."""
    if not zone_kpis:
        print("\n[HATA] Hicbir zone sonucu yuklenememistir.")
        return

    print("\n" + "=" * 78)
    print(" 340 HEX MATHEURISTIC SONUC RAPORU - VORONOI DECOMPOSITION")
    print("=" * 78)

    # Tablo
    print(f"\n{'Zone':>6} {'Hex':>5} {'Kapsama':>8} {'Ihlal':>6} "
          f"{'Slack(dk)':>11} {'Sure(dk)':>9} {'Pik':>6}")
    print("-" * 78)

    total_hex = 0
    total_visited = 0
    total_violations = 0
    total_slack = 0
    total_runtime_min = 0
    overall_peaks = {}

    for k in zone_kpis:
        peak_str = "/".join(f"{k['global_peak'].get(s, 0)}" for s in [k['zone']])
        print(f"{k['zone']:>6} {k['n_hex']:>5} "
              f"{k['visited_hex']}/{k['n_hex']:>3} ({k['coverage_pct']:>4.1f}%) "
              f"{k['violations']:>6} {k['slack_total_dk']:>11.0f} "
              f"{k['runtime_min']:>8.1f}  {peak_str:>6}")
        total_hex += k['n_hex']
        total_visited += k['visited_hex']
        total_violations += k['violations']
        total_slack += k['slack_total_dk']
        total_runtime_min += k['runtime_min']
        overall_peaks.update(k['global_peak'])

    print("-" * 78)
    coverage_overall = 100 * total_visited / total_hex if total_hex else 0
    print(f"{'TOPLAM':>6} {total_hex:>5} {total_visited}/{total_hex} "
          f"({coverage_overall:>4.1f}%) {total_violations:>6} "
          f"{total_slack:>11.0f} {total_runtime_min:>8.1f}")

    # Baseline kıyaslama
    print("\n" + "=" * 78)
    print(" 110 HEX N_d=9 BASELINE KIYASLAMA (kalite kaybi olcumu)")
    print("=" * 78)
    print(f"  Baseline       : {BASELINE_110_ND9['n_hex']} hex, "
          f"{BASELINE_110_ND9['n_drones']} dron, "
          f"slack={BASELINE_110_ND9['slack_total']:,} dk, "
          f"ihlal={BASELINE_110_ND9['violations']}, "
          f"{BASELINE_110_ND9['coverage_pct']:.0f}% kapsama")

    baseline_slack_per_hex = BASELINE_110_ND9['slack_total'] / BASELINE_110_ND9['n_hex']
    matheuristic_slack_per_hex = total_slack / total_hex
    quality_ratio = matheuristic_slack_per_hex / baseline_slack_per_hex

    print(f"\n  Per-hex slack:")
    print(f"    Baseline (110/9)    : {baseline_slack_per_hex:>10,.0f} dk/hex")
    print(f"    Matheuristic (340)  : {matheuristic_slack_per_hex:>10,.0f} dk/hex")
    print(f"    Oran                : {quality_ratio:.2f}x")
    if quality_ratio < 1.5:
        kalite = "MUKEMMEL (matheuristic baseline kalitesine yakin)"
    elif quality_ratio < 2.5:
        kalite = "IYI (kabul edilebilir kalite kaybi)"
    elif quality_ratio < 5.0:
        kalite = "ORTA (kalite kaybi var ama olcek kazanci buyuk)"
    else:
        kalite = "DUSUK (parametre ayarlamasi gerekli)"
    print(f"    Degerlendirme       : {kalite}")

    print(f"\n  Runtime kiyaslama:")
    print(f"    Baseline (110 hex)  : {BASELINE_110_ND9['runtime_min']} dk")
    print(f"    Matheuristic (340)  : {total_runtime_min:.1f} dk (3x daha buyuk problem)")
    if total_runtime_min < 3 * BASELINE_110_ND9['runtime_min']:
        print(f"    -> Decomposition iyiSCALE: 3 kat hex'i {total_runtime_min/BASELINE_110_ND9['runtime_min']:.1f}x sure ile cozdu")

    # Soket onerisi
    print(f"\n  Istasyon soket onerisi (her zone'un kendi piki):")
    for z in zone_kpis:
        for s, p in z['global_peak'].items():
            print(f"    {s}: pik={p}, onerilen soket={p+1} (pik+1 yedek)")

    # Akademik anlat
    print("\n" + "=" * 78)
    print(" AKADEMIK ANLATI (savunma slaytinda kullanilabilir)")
    print("=" * 78)
    anlat = (
        f"340 hex tam olcek pilot alaninda saf MILP cift kanitla yetmedi "
        f"(Nd=12, Nd=18 kosumlari). Hocanin '2 saat kurali' uyarinca spatial "
        f"decomposition matheuristic uygulandi: Voronoi partition ile "
        f"alan 3 istasyon merkezli bolgeye ayrildi (S1: {zone_kpis[0]['n_hex']}, "
        f"S2: {zone_kpis[1]['n_hex'] if len(zone_kpis)>1 else '?'}, "
        f"S3: {zone_kpis[2]['n_hex'] if len(zone_kpis)>2 else '?'} hex), her bolge "
        f"bagimsiz MILP olarak coozuldu. Sonuc: {coverage_overall:.1f}% kapsama, "
        f"{total_violations} ihlal, {total_slack:,.0f} toplam slack, "
        f"{total_runtime_min:.1f} dk toplam runtime. Per-hex slack basinda "
        f"{quality_ratio:.2f}x baseline kalite oranı: {kalite.split(' (')[0]}."
    )
    print(f"\n  {anlat}")

    # summary.txt
    summary_path = os.path.join(out_dir, "aggregate_summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(f"340 HEX MATHEURISTIC SONUC RAPORU\n")
        f.write(f"Olusturuldu: {datetime.now():%Y-%m-%d %H:%M}\n")
        f.write("=" * 78 + "\n\n")
        f.write(f"Toplam hex     : {total_hex}\n")
        f.write(f"Kapsama        : {total_visited}/{total_hex} ({coverage_overall:.1f}%)\n")
        f.write(f"Ihlal          : {total_violations}\n")
        f.write(f"Toplam slack   : {total_slack:,.0f} dk\n")
        f.write(f"Toplam runtime : {total_runtime_min:.1f} dk\n")
        f.write(f"Kalite oranı   : {quality_ratio:.2f}x baseline (110/9)\n\n")
        f.write("Zone detaylari:\n")
        for k in zone_kpis:
            f.write(f"  {k['zone']}: {k['n_hex']} hex, "
                    f"{k['visited_hex']}/{k['n_hex']} kapsama, "
                    f"{k['violations']} ihlal, "
                    f"slack={k['slack_total_dk']:,.0f}, "
                    f"sure={k['runtime_min']:.1f}dk\n")
        f.write(f"\nAnlat:\n{anlat}\n")

    print(f"\n[YAZILDI] {summary_path}")
    print(f"[CIKTILAR] {out_dir}\\")
    print(f"  aggregate_blok_ozet.csv")
    print(f"  aggregate_hex_ziyaret.csv")
    print(f"  aggregate_hareketler.csv")
    print(f"  aggregate_denetimler.csv")
    print(f"  aggregate_summary.txt")


def main():
    print("=" * 78)
    print(" 340 HEX MATHEURISTIC AGGREGATOR")
    print(" 3 zone sonuclarini birlestirir + global rapor uretir")
    print("=" * 78)
    print(f"\n  Base dizin: {ZONES_BASE_DIR}")
    print(f"  Zone'lar  : {', '.join(ZONE_IDS)}")

    zone_kpis, out_dir = aggregate_all(ZONES_BASE_DIR, ZONE_IDS)
    print_report(zone_kpis, out_dir)
    print("\n" + "=" * 78 + "\n")


if __name__ == "__main__":
    main()
