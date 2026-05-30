# drone-fire-detection
Dron Tabanlı Erken Yangın Tespit Modeli — DEÜ Endüstri Mühendisliği Bitirme Tezi
# Dron Tabanlı Erken Yangın Tespit Sistemi

> Çoklu dron filosu için Karma Tam Sayılı Doğrusal Programlama (KTSDP), Voronoi tabanlı matsezgisel (V25) ve Uyarlamalı Geniş Komşuluk Araması (UGKA) ile geliştirilmiş bütünleşik bir rota planlama ve şarj çizelgeleme sistemi.


## Proje Hakkında

Bu proje, küresel iklim değişikliğinin etkisiyle giderek artan orman yangını riskine karşı **proaktif bir erken tespit altyapısı** sunar. Geleneksel reaktif yangın söndürme yaklaşımının yetersizliklerini aşmak amacıyla geliştirilen sistem; çoklu dron filosunun rotalanmasını, periyodik denetim aralıklarını ve şarj çizelgelemesini bütünleşik bir matematiksel model ile optimize eder.

Projenin temel araştırma sorusu şudur: *24 saatlik bir operasyonel ufukta, sınırlı sayıdaki dron ile risk seviyesi heterojen bir orman bölgesinin tamamı, batarya ve şarj kısıtları altında, hangi rota planlamasıyla en etkin biçimde denetlenebilir?*

Bu soruyu yanıtlamak için üç farklı çözüm paradigması bir araya getirilmiştir: matematiksel programlama, uzamsal ayrışım tabanlı matsezgisel ve metasezgisel arama. Her bir paradigmanın güçlü yönlerinden ölçeğe uygun biçimde yararlanılmıştır.

Proje, Dokuz Eylül Üniversitesi Endüstri Mühendisliği Bölümü lisans bitirme tezi kapsamında geliştirilmiştir.

---

## Sistemin Genel Mimarisi

Sistem üç katmanlı bir hibrit mimari üzerine kuruludur:

```
┌─────────────────────────────────────────────────────────────────┐
│                       VERİ KATMANI                              │
│   QGIS Risk Haritası  |  CORINE  |  SRTM  |  OpenStreetMap      │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   MATEMATİKSEL MODEL KATMANI                    │
│                                                                 │
│   ┌──────────────┐   ┌──────────────┐   ┌─────────────────┐    │
│   │    KTSDP     │   │     V25      │   │      UGKA       │    │
│   │  (Gurobi)    │──▶│  Voronoi     │──▶│  Metasezgisel   │    │
│   │              │   │  Matsezgisel │   │  İyileştirme    │    │
│   └──────────────┘   └──────────────┘   └─────────────────┘    │
│       Küçük ölçek      Orta/büyük ölçek    Yerel iyileştirme   │
│       (≤ 110 düğüm)    (≥ 340 düğüm)       (Tüm ölçekler)      │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     ÇIKTI KATMANI                               │
│   Rota Planları  |  Şarj Çizelgesi  |  Performans Metrikleri    │
└─────────────────────────────────────────────────────────────────┘
```

**Üç katmanın rolü:**

- **KTSDP (Karma Tam Sayılı Doğrusal Programlama):** Küçük ve orta ölçekli problemler için kesin çözüm üretir. 11 farklı kısıt grubu (başlangıç, konum tekliği, hareket-konum bağı, süreklilik, tek eylem, çarpışma önleme, ziyaret, periyodik denetim, şarj, enerji dengesi, çevrim kapanışı) içerir.
- **V25 Matsezgiseli:** Voronoi tabanlı uzamsal ayrışım ile büyük problem alanını üç şarj istasyonu merkezli alt bölgelere ayırır. Her alt problem bağımsız bir KTSDP olarak çözülür.
- **UGKA Metasezgiseli:** V25'in ayrışım sınırlarından kaynaklanan yapısal eksiklikleri yıkma–onarma döngüleriyle kapatır. Sıcak başlatma stratejisiyle hem KTSDP hem de V25 çözümlerini iyileştirir.

---

## Uygulama Alanı

**Pilot Bölge:** İzmir, Seferihisar — Teos Orman-Kentsel Ara Yüzü (OKA)

Pilot bölge dört temel kritere göre seçilmiştir:
- Geçmiş yangın örüntüsü (İzmir, son on yılda Türkiye'de en çok yangın yaşanan ikinci il)
- Sahil yakınlığı (Ege meltemi etkisi)
- Değişken topografya
- Orman, yerleşim, tarım ve tarihi koruma alanlarının iç içe geçtiği OKA niteliği

**Risk haritası beş faktörlü ağırlıklı yaklaşımla oluşturulmuştur:**
- Bitki örtüsü (%30)
- Bina yoğunluğu (%25)
- Eğim (%15)
- Yola yakınlık (%15)
- Tarihi koruma alanları (%15)

**Operasyonel parametreler:**

| Parametre | Değer |
|---|---|
| Plan ufku | 24 saat (12 × 2 saatlik blok) |
| Altıgen yarıçapı | 58,9 m |
| Hücre alanı | ≈ 9.015 m² |
| Dron modeli | DJI Matrice 30T |
| Termal kamera görüş açısı | 61° |
| Uçuş irtifası | 100 m |
| Kapsama genişliği | 117,8 m |
| Şarj istasyonu sayısı | 3 (S1, S2, S3) |

---

## Temel Sonuçlar

Sistem üç farklı ölçekte test edilmiştir:

| Konfigürasyon | Yöntem | Kapsama | İhlal | Slack (dk) | Süre |
|---|---|---|---|---|---|
| 44 hücre (pilot)  | KTSDP        | 44/44 (%100)     | 21  | 4.585     | 42,8 dk |
| 110 hücre (orta)  | KTSDP        | 110/110 (%100)   | 80  | 635.904   | 105,6 dk |
| 110 hücre (orta)  | UGKA         | 110/110 (%100)   | 43  | 673.879   | < 1 dk |
| 340 hücre (tam)   | Saf KTSDP    | —                | —   | —         | Timeout |
| 340 hücre (tam)   | V25          | 303/340 (%89,1)  | 274 | 7.516.848 | 471,3 dk |
| 340 hücre (tam)   | **V25 + UGKA** | **340/340 (%100)** | 305 | **6.843.782** | **478,0 dk** |

**Temel bulgular:**

- 110 düğümde UGKA, KTSDP çözümünü iyileştirerek ihlal hücre sayısını **%46,3 oranında azaltmış** ve çözüm süresini **~500× hızlandırmıştır**.
- 340 düğümde saf KTSDP fizibl çözüm üretememiş; hibrit V25+UGKA mimarisi tam kapsama sağlamıştır.
- V25'in S2 bölgesindeki uzun şeritli geometriden kaynaklanan **yapısal sınırlama** UGKA tarafından aşılmıştır.

---

##  Gereksinimler

### Yazılım
- Python ≥ 3.10
- Gurobi ≥ 11.0 (akademik veya ticari lisans)
- QGIS ≥ 3.34 (risk haritası ön işleme için)

### Python Kütüphaneleri
```
gurobipy>=11.0
numpy>=1.24
pandas>=2.0
geopandas>=0.13
shapely>=2.0
matplotlib>=3.7
scikit-learn>=1.3
networkx>=3.1
```



##  Kullanım

 1. Veri Hazırlama

QGIS üzerinden risk haritası oluşturup CSV olarak dışa aktar.

 2. KTSDP Modelinin Çalıştırılması 

 python ktsdp_model.py --nodes 110 --drones 9 --blocks 12

 3. V25 Matsezgiselinin Çalıştırılması 

 python v25_matheuristic.py --nodes 340 --drones 32

 4. UGKA Metasezgiselinin Çalıştırılması

 python ugka_metaheuristic.py --nodes 340 --init v25


## Yöntem

### Matematiksel Model (KTSDP)

Model 11 farklı kısıt grubunu içerir:

1. **Başlangıç kısıdı** — Her dron operasyona istasyonunda başlar.
2. **Konum tekliği** — Bir dron her dilimde tek bir konumdadır.
3. **Hareket-konum bağı** — Hareketler ile konum değişimleri arasında tutarlılık.
4. **Süreklilik** — Dron hareketleri zincirleme tutarlıdır.
5. **Tek eylem** — Bir dilimde dron ya hareket eder ya denetler ya da şarj olur.
6. **Çarpışma önleme** — Aynı dilimde aynı hücrede tek dron.
7. **Ziyaret kısıdı** — Denetim için dronun hücrede olması gerekir.
8. **Periyodik denetim** — Her hücre risk seviyesine bağlı maksimum aralıkta denetlenir.
9. **Şarj kısıdı** — Soket kapasitesi ve şarj süresi modellenir.
10. **Enerji dengesi** — Batarya seviyesi sürekli izlenir, Emin = %20 alt sınır.
11. **Çevrim kapanışı** — Plan sonunda dronlar istasyonlarına döner.

**Amaç fonksiyonu:**

```
min  α · Enerji-mesafe maliyeti  +  β · Risk-ağırlıklı slack  −  γ · Ziyaret ödülü
```

Ağırlıklar: α = 1, β = 10, γ = 5

### V25 Matsezgiseli

- Voronoi diyagramı ile pilot bölgeyi 3 alt bölgeye ayırır.
- Her alt bölge bağımsız bir KTSDP problemi olarak çözülür.
- Karar değişkeni sayısını yaklaşık **9 kat azaltır**.

### UGKA (Uyarlamalı Geniş Komşuluk Araması)

- **Yıkma operatörleri:** Rastgele, En kötü, Mekânsal, Zaman penceresi
- **Onarma operatörleri:** Açgözlü, Pişmanlık-2, Rastgele, Risk-öncelikli
- **Soğuma katsayısı:** 0,998 (Kirkpatrick vd., 1983)
- **Operatör skorlama:** Ropke ve Pisinger (2006) standardı
- **Sıcak başlatma:** KTSDP veya V25 çıktısı

---

## Bilinen Sınırlamalar

- **Deterministik hava modeli:** Rüzgâr, sıcaklık ve nem parametreleri sabit varsayılır.
- **Statik risk haritası:** Anlık dinamik faktörler (yangın hava indisi vb.) modele dahil değildir.
- **%20 MIP Gap toleransı:** Büyük ölçekte gerçek optimumdan uzaklaşma riski.
- **Homojen filo varsayımı:** Tüm dronlar aynı model (DJI Matrice 30T) olarak modellenmiştir.
- **Tek bölgeli doğrulama:** Sistem sadece Seferihisar-Teos OKA bölgesinde test edilmiştir.

---


##  Yazarlar

- **Firdevs KUTLU** — *Endüstri Mühendisliği Lisans Öğrencisi* — Dokuz Eylül Üniversitesi
- **Büşra GÜNDOĞDU** — *Endüstri Mühendisliği Lisans Öğrencisi* — Dokuz Eylül Üniversitesi

**Tez Danışmanı:** Prof. Dr. Şener AKPINAR — Dokuz Eylül Üniversitesi, Endüstri Mühendisliği Bölümü


##  İletişim

Sorular, öneriler veya işbirlikleri için:"  
- Firdevs KUTLU — `firdevs.kutlu@ogr.deu.edu.tr`
- Büşra GÜNDOĞDU — `busra.gundogdu@ogr.deu.edu.tr`

---
## Yapay Zeka Kullanım Beyanı
Bu çalışmada yer alan Python kodlarının geliştirilmesinde 
ve tez yazımının belirli bölümlerinde Claude Sonnet 
(Anthropic, 2026) yapay zeka aracından yararlanılmıştır. 
Üretilen tüm içerikler yazarlar tarafından incelenmiş 
ve doğrulanmıştır.
