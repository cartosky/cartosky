from herbie import Herbie
import requests

run_date = '2026-04-17 06:00'
fxxs = [240, 246, 252]
products = ['atmos.25', 'atmos.5', 'atmos.1']

for prod in products:
    print(f"\n--- Product: {prod} ---")
    available_fxx = []
    for fxx in fxxs:
        try:
            H = Herbie(run_date, model='gefs', product=prod, fxx=fxx, member='mean')
            if H.grib:
                available_fxx.append(fxx)
        except:
            pass
    print(f"Available fxx: {available_fxx}")

# Manual URL construction for atmos.25 f246 just to be absolutely sure
f246_0p25_url = "https://noaa-gefs-pds.s3.amazonaws.com/gefs.20260417/06/atmos/pgrb2sp25/geavg.t06z.pgrb2s.0p25.f246"
r = requests.head(f246_0p25_url)
print(f"\nManual check of atmos.25 f246: {f246_0p25_url}")
print(f"Status: {r.status_code}")

# Check if atmos.5 goes beyond 240
f384_0p5_url = "https://noaa-gefs-pds.s3.amazonaws.com/gefs.20260417/06/atmos/pgrb2ap5/geavg.t06z.pgrb2a.0p50.f384"
r2 = requests.head(f384_0p5_url)
print(f"\nManual check of atmos.5 f384: {f384_0p5_url}")
print(f"Status: {r2.status_code}")
