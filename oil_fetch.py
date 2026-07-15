#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAPTOR Oil — Data Fetch
Scarica WTI Crude Oil Futures (CL=F) dal 2000 e i due ETP a leva 3x su Borsa Italiana:
  - 3OIL.MI  (WisdomTree WTI Crude Oil 3x Daily Leveraged)
  - 3OIS.MI  (WisdomTree WTI Crude Oil 3x Daily Short)
Calcola: KAMA Fast/Slow, RSI5/14, AO, SAR, ER, ADX/PDI/NDI, Hurst 60g/1y,
regime di mercato, momentum Antonacci 12M, segnali BUY/SELL.

Schedule:
- 08:00 CET: analisi completa notturna + aggiornamento storico
- 16:00 CET: rilevazione intraday
- 19:00 CET: chiusura giornaliera + salvataggio completo
- workflow_dispatch: aggiornamento manuale on-demand ("Aggiorna ora")
"""

import json, math, os
from datetime import datetime, timezone
import yfinance as yf

TICKERS = {
    'long':  '3OIL.MI',
    'short': '3OIS.MI',
}
WTI_TICKER = 'CL=F'
QTY_FIXED = 100  # quantità fissa per privacy (repo pubblico)

# ── RILEVAMENTO ORARIO ─────────────────────────────────
def get_execution_type():
    now_utc = datetime.now(timezone.utc)
    hour, minute = now_utc.hour, now_utc.minute
    if 6 <= hour < 7 or (hour == 6 and minute >= 0):     # 08:00 CET = 06:00/07:00 UTC (DST-safe range)
        return 'morning'
    elif 14 <= hour < 15:                                 # 16:00 CET ≈ 14:00 UTC
        return 'intraday'
    elif 17 <= hour < 18:                                 # 19:00 CET ≈ 17:00 UTC
        return 'close'
    else:
        return 'manual'

# ── INDICATORI (allineati al sistema RAPTOR / scannerv2) ──
def calc_kama(closes, n=10, fast=2, slow=30):
    fsc = 2/(fast+1); ssc = 2/(slow+1)
    kama = [None]*len(closes)
    if len(closes) <= n: return kama
    kama[n] = closes[n]
    for i in range(n+1, len(closes)):
        d = abs(closes[i]-closes[i-n])
        v = sum(abs(closes[j]-closes[j-1]) for j in range(i-n+1, i+1))
        er = d/v if v else 0
        sc = (er*(fsc-ssc)+ssc)**2
        kama[i] = kama[i-1] + sc*(closes[i]-kama[i-1])
    return kama

def calc_rsi(closes, n=14):
    res = [None]*len(closes)
    for i in range(n+1, len(closes)):
        gs=[]; ls=[]
        for j in range(i-n, i+1):
            dd = closes[j]-closes[j-1]
            gs.append(max(dd,0)); ls.append(max(-dd,0))
        ag=sum(gs)/n; al=sum(ls)/n
        res[i] = round(100-100/(1+ag/al),2) if al>0 else 100.0
    return res

def calc_ao(highs, lows):
    mid = [(h+l)/2 for h,l in zip(highs,lows)]
    def ema(arr, p):
        k=2/(p+1); e=arr[0]; out=[e]
        for x in arr[1:]: e=x*k+e*(1-k); out.append(e)
        return out
    if len(mid)<13: return [0]*len(mid)
    e3=ema(mid,3); e13=ema(mid,13)
    return [round(a-b,4) for a,b in zip(e3,e13)]

def calc_sar(high, low, step=0.03, max_af=0.25):
    n=len(high); sar=[None]*n
    if n<5: return sar
    bull=high[1]>high[0]; af=step
    ep=max(high[:2]) if bull else min(low[:2])
    sar[1]=min(low[:2]) if bull else max(high[:2])
    for i in range(2,n):
        ps=sar[i-1]
        if bull:
            sar[i]=min(ps+af*(ep-ps), low[i-1], low[i-2] if i>=2 else low[i-1])
            if low[i]<sar[i]: bull=False; af=step; sar[i]=ep; ep=low[i]
            else:
                if high[i]>ep: ep=high[i]; af=min(af+step,max_af)
        else:
            sar[i]=max(ps+af*(ep-ps), high[i-1], high[i-2] if i>=2 else high[i-1])
            if high[i]>sar[i]: bull=True; af=step; sar[i]=ep; ep=high[i]
            else:
                if low[i]<ep: ep=low[i]; af=min(af+step,max_af)
    return sar

def calc_er(closes, n=10):
    res=[0]*len(closes)
    for i in range(n,len(closes)):
        d=abs(closes[i]-closes[i-n])
        v=sum(abs(closes[j]-closes[j-1]) for j in range(i-n+1,i+1))
        res[i]=round(d/v,4) if v else 0
    return res

def calc_atr(highs, lows, closes, period=14):
    n = len(closes)
    tr = [0.0]*n
    for i in range(1, n):
        tr[i] = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
    atr = [None]*n
    if n <= period: return atr
    atr[period] = sum(tr[1:period+1])/period
    for i in range(period+1, n):
        atr[i] = (atr[i-1]*(period-1) + tr[i]) / period
    return atr

def calc_adx_full(highs, lows, closes, period=14):
    n = len(closes)
    atr = calc_atr(highs, lows, closes, period)
    adx = [None]*n; pdi_arr=[None]*n; ndi_arr=[None]*n
    if n < period*2: return adx, pdi_arr, ndi_arr
    up   = [highs[i]-highs[i-1] for i in range(1,n)]
    down = [lows[i-1]-lows[i] for i in range(1,n)]
    pdm  = [up[i] if (up[i]>down[i] and up[i]>0) else 0.0 for i in range(len(up))]
    ndm  = [down[i] if (down[i]>up[i] and down[i]>0) else 0.0 for i in range(len(down))]
    pdm14 = sum(pdm[:period]); ndm14 = sum(ndm[:period])
    dx_arr=[]
    for i in range(period, n-1):
        pdm14 = pdm14 - pdm14/period + pdm[i]
        ndm14 = ndm14 - ndm14/period + ndm[i]
        a14 = atr[i+1]
        pdi = 100*pdm14/a14 if a14 else 0
        ndi = 100*ndm14/a14 if a14 else 0
        dx  = 100*abs(pdi-ndi)/(pdi+ndi) if (pdi+ndi) else 0
        dx_arr.append((dx,pdi,ndi))
    if len(dx_arr) >= period:
        adx_val = sum(x[0] for x in dx_arr[:period])/period
        idx = period*2
        for i in range(period, len(dx_arr)):
            adx_val = (adx_val*(period-1) + dx_arr[i][0]) / period
            if idx < n:
                adx[idx]=round(adx_val,2); pdi_arr[idx]=round(dx_arr[i][1],2); ndi_arr[idx]=round(dx_arr[i][2],2)
            idx += 1
    return adx, pdi_arr, ndi_arr

def calc_hurst(prices, min_points=30):
    """Hurst Exponent (metodo varianza). H>0.55 trending · H≈0.5 random walk · H<0.45 mean reverting"""
    if len(prices) < min_points: return 0.5
    lags = [l for l in [2,4,8,16,32] if l < len(prices)//2]
    if len(lags) < 3: return 0.5
    try:
        log_p = [math.log(p) for p in prices if p>0]
        if len(log_p) < max(lags)+1: return 0.5
        vars_=[]
        for lag in lags:
            diffs=[log_p[i]-log_p[i-lag] for i in range(lag,len(log_p))]
            mean_d=sum(diffs)/len(diffs)
            var=sum((d-mean_d)**2 for d in diffs)/len(diffs)
            vars_.append(var if var>0 else 1e-10)
        log_lags=[math.log(l) for l in lags]; log_vars=[math.log(v) for v in vars_]
        n=len(lags); mx=sum(log_lags)/n; my=sum(log_vars)/n
        num=sum((log_lags[i]-mx)*(log_vars[i]-my) for i in range(n))
        den=sum((log_lags[i]-mx)**2 for i in range(n))
        if den==0: return 0.5
        return round(max(0.1,min(0.9, num/den/2)),3)
    except Exception:
        return 0.5

def classify_regime(h60, adx, pdi, ndi):
    """🚀 Slancio · 📈 Salita · 📉 Ribasso · ⚠️ Transizione · ↔ Laterale"""
    adx=adx or 0; pdi=pdi or 0; ndi=ndi or 0
    if adx >= 25:
        if pdi >= ndi:
            return {"code":"SLANCIO","label":"🚀 Slancio","color":"#1a7f37"} if h60>0.55 else {"code":"SALITA","label":"📈 Salita","color":"#2ea043"}
        return {"code":"RIBASSO","label":"📉 Ribasso","color":"#cf222e"}
    elif adx >= 20:
        return {"code":"TRANSIZIONE","label":"⚠️ Transizione","color":"#bc4c00"}
    else:
        return {"code":"LATERALE","label":"↔ Laterale","color":"#8c98a4"}

def calc_rvi(opens, highs, lows, closes, period=10):
    n = len(closes)
    rvi=[None]*n; sig=[None]*n
    if n < period+4: return rvi, sig
    num=[None]*n; den=[None]*n
    for i in range(3, n):
        c0,o0,h0,l0 = closes[i],opens[i],highs[i],lows[i]
        c1,o1,h1,l1 = closes[i-1],opens[i-1],highs[i-1],lows[i-1]
        c2,o2,h2,l2 = closes[i-2],opens[i-2],highs[i-2],lows[i-2]
        c3,o3,h3,l3 = closes[i-3],opens[i-3],highs[i-3],lows[i-3]
        num[i] = ((c0-o0)+2*(c1-o1)+2*(c2-o2)+(c3-o3))/6.0
        den[i] = ((h0-l0)+2*(h1-l1)+2*(h2-l2)+(h3-l3))/6.0
    for i in range(period+3, n):
        s_num = sum(num[i-period+1:i+1])
        s_den = sum(den[i-period+1:i+1])
        rvi[i] = s_num/s_den if s_den != 0 else 0.0
    for i in range(3, n):
        if rvi[i] is not None and rvi[i-1] is not None and rvi[i-2] is not None and rvi[i-3] is not None:
            sig[i] = (rvi[i]+2*rvi[i-1]+2*rvi[i-2]+rvi[i-3])/6.0
    return rvi, sig

def kama_trend(kama, lookback=5):
    """VERDE (in salita) · ROSSO (in discesa) · GRIGIO (laterale)"""
    valid = [(i,v) for i,v in enumerate(kama) if v is not None]
    if len(valid) < lookback+1: return "GRIGIO"
    recent = [v for _,v in valid[-(lookback+1):]]
    if all(recent[i] < recent[i+1] for i in range(len(recent)-1)): return "VERDE"
    if all(recent[i] > recent[i+1] for i in range(len(recent)-1)): return "ROSSO"
    return "GRIGIO"

def calc_baff(closes, kama):
    n=len(closes); count=0
    if kama[-1] is None: return 0
    above = closes[-1] > kama[-1]
    for i in range(n-1,-1,-1):
        if kama[i] is None: break
        if above:
            if closes[i] > kama[i]: count += 1
            else: break
        else:
            if closes[i] <= kama[i]: count -= 1
            else: break
    return count

def momentum_pct(closes, bars):
    if len(closes) <= bars or closes[-bars-1] == 0: return 0.0
    return round((closes[-1]/closes[-bars-1]-1)*100, 2)

from collections import defaultdict

def calc_stagionalita(closes, dates):
    """Rendimento medio mensile su tutta la storia disponibile"""
    monthly_rets = defaultdict(list)
    for i in range(1, len(closes)):
        if closes[i] and closes[i-1]:
            month = int(dates[i][5:7])
            ret = (closes[i]-closes[i-1])/closes[i-1]*100
            monthly_rets[month].append(ret)
    stagionalita = []
    mesi = ['Gen','Feb','Mar','Apr','Mag','Giu','Lug','Ago','Set','Ott','Nov','Dic']
    for m in range(1,13):
        rets = monthly_rets[m]
        avg = sum(rets)/len(rets) if rets else 0
        positive = sum(1 for r in rets if r>0)
        wr = positive/len(rets)*100 if rets else 0
        stagionalita.append({'mese':m,'nome':mesi[m-1],'avg_ret':round(avg,3),'win_rate':round(wr,1),'n_anni':len(rets)})
    return stagionalita

def calc_antonacci(closes, dates, lookback_months=12):
    results=[]
    approx_days = lookback_months*21
    for i in range(approx_days, len(closes)):
        if closes[i] and closes[i-approx_days]:
            ret = (closes[i]-closes[i-approx_days])/closes[i-approx_days]*100
            results.append({'date':dates[i],'price':closes[i],'ret_12m':round(ret,2),
                             'signal':'BUY' if ret>0 else 'OUT'})
    return results

def calc_signals(closes, kama_fast, kama_slow, volumes, ao_arr, er_arr):
    """Segnali RAPTOR — BUY3/BUY2/SELL — allineati fin dall'inizio (fix bug 25 barre)"""
    signals = []
    avg_vol = sum(volumes[-21:-1])/20 if len(volumes)>21 else 1
    for i in range(25, len(closes)):
        kf=kama_fast[i]; ks=kama_slow[i]
        if kf is None or ks is None:
            signals.append(None); continue
        p=closes[i]
        if p>kf and kf>ks:   zona='LONG_CONF'
        elif p>kf and p>ks:  zona='LONG_EARLY'
        elif p<ks:           zona='STOP' if (ks-p)/ks*100>2 else 'USCITA'
        else:                zona='GRIGIA'
        vr = volumes[i]/avg_vol if avg_vol>0 else 1
        gap_ok = ks>0 and abs(kf-ks)/ks>=0.003
        ao = ao_arr[i] if i<len(ao_arr) else 0
        baff=0
        for j in range(max(0,i-5), i+1):
            if kama_fast[j] and closes[j]>kama_fast[j]: baff+=1
            else: baff=0
        sig=None
        if zona=='LONG_CONF' and ao>0 and vr>=2 and baff>=3 and er_arr[i]>=0.35 and gap_ok:
            sig='BUY3'
        elif zona=='LONG_EARLY' and ao>0 and vr>=1.5 and baff>=2 and er_arr[i]>=0.35:
            sig='BUY2'
        elif zona in ('STOP','USCITA'):
            sig='SELL'
        signals.append(sig)
    return [None]*25 + signals  # fix: allineamento a closes fin dall'origine

def calc_score_rating(signal, adx, hurst60, mom_1m, baff):
    """Score 0-100 e rating (STRONG_BUY/BUY/NEUTRAL/SELL) — soglie coerenti con scannerv2 (60/45/30)"""
    base = {'BUY3':70, 'BUY2':50, 'SELL':15}.get(signal, 32)
    adx_bonus = min(15, max(0,(adx or 0)-20)*0.6)
    trend_bonus = 8 if hurst60 and hurst60>0.55 else 0
    mom_bonus = max(-10, min(10, (mom_1m or 0)*0.5))
    baff_bonus = max(-8, min(8, (baff or 0)*1.2))
    score = round(max(0, min(100, base+adx_bonus+trend_bonus+mom_bonus+baff_bonus)))
    if score>=60: rating='STRONG_BUY'
    elif score>=45: rating='BUY'
    elif score>=30: rating='NEUTRAL'
    else: rating='SELL'
    return score, rating

def sanitize(obj):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj): return None
        return obj
    if isinstance(obj, dict): return {k:sanitize(v) for k,v in obj.items()}
    if isinstance(obj, list): return [sanitize(v) for v in obj]
    return obj

def fmt(arr):
    return [round(v,4) if v is not None else None for v in arr]

# ── FETCH SINGOLO STRUMENTO ────────────────────────────
def fetch_series(ticker, start):
    df = yf.download(ticker, start=start, interval="1d", auto_adjust=True, progress=False)
    if hasattr(df.columns, 'levels'):
        df.columns = df.columns.get_level_values(0)
    closes = [round(float(c),4) for c in df['Close'].tolist()]
    highs  = [round(float(c),4) for c in df['High'].tolist()]
    lows   = [round(float(c),4) for c in df['Low'].tolist()]
    opens  = [round(float(c),4) for c in df['Open'].tolist()] if 'Open' in df else list(closes)
    dates  = [ts.strftime('%Y-%m-%d') for ts in df.index]
    volumes = [int(v) for v in df['Volume'].tolist()] if 'Volume' in df else [0]*len(closes)
    return closes, highs, lows, opens, volumes, dates

def build_etp_block(ticker, start):
    print(f"Scarico {ticker}...")
    closes, highs, lows, opens, volumes, dates = fetch_series(ticker, start)
    print(f"{ticker}: {len(closes)} barre ({dates[0]} → {dates[-1]})")

    kama_fast = calc_kama(closes, n=5,  fast=3, slow=20)
    kama_slow = calc_kama(closes, n=20, fast=2, slow=40)
    rsi14 = calc_rsi(closes, 14)
    rsi5  = calc_rsi(closes, 5)
    ao    = calc_ao(highs, lows)
    sar   = calc_sar(highs, lows)
    er    = calc_er(closes, 10)
    adx, pdi, ndi = calc_adx_full(highs, lows, closes, 14)
    rvi, rvi_sig = calc_rvi(opens, highs, lows, closes, 10)
    trend_kama = kama_trend(kama_fast)
    signals = calc_signals(closes, kama_fast, kama_slow, volumes, ao, er)

    h60 = calc_hurst(closes[-60:]) if len(closes)>=60 else 0.5
    h1y = calc_hurst(closes[-252:]) if len(closes)>=252 else 0.5
    last_adx = next((v for v in reversed(adx) if v is not None), 0)
    last_pdi = next((v for v in reversed(pdi) if v is not None), 0)
    last_ndi = next((v for v in reversed(ndi) if v is not None), 0)
    regime = classify_regime(h60, last_adx, last_pdi, last_ndi)

    antonacci_full = calc_antonacci(closes, dates)
    antonacci_latest = antonacci_full[-1] if antonacci_full else {}

    baff = calc_baff(closes, kama_fast)
    mom_1m = momentum_pct(closes, 21)
    mom_3m = momentum_pct(closes, 63)
    mom_6m = momentum_pct(closes, 126)

    last_signal_idx = next((i for i in range(len(signals)-1,-1,-1) if signals[i]), None)
    signal_since = dates[last_signal_idx] if last_signal_idx is not None else None
    bars_since = (len(signals)-1-last_signal_idx) if last_signal_idx is not None else None
    last_signal = signals[last_signal_idx] if last_signal_idx is not None else None
    score, rating = calc_score_rating(last_signal, last_adx, h60, mom_1m, baff)

    return {
        'ticker': ticker,
        'dates': dates, 'closes': closes, 'highs': highs, 'lows': lows, 'opens': opens, 'volumes': volumes,
        'kama_fast': fmt(kama_fast), 'kama_slow': fmt(kama_slow),
        'rsi14': fmt(rsi14), 'rsi5': fmt(rsi5), 'ao': fmt(ao), 'sar': fmt(sar), 'er': er,
        'adx': fmt(adx), 'pdi': fmt(pdi), 'ndi': fmt(ndi),
        'rvi': fmt(rvi), 'rvi_sig': fmt(rvi_sig), 'trend_kama': trend_kama,
        'kama_pct': round((closes[-1]/kama_fast[-1]-1)*100, 2) if kama_fast[-1] else None,
        'signals': signals,
        'signal_since': signal_since, 'bars_since': bars_since, 'last_signal': last_signal,
        'score': score, 'rating': rating,
        'baff': baff, 'mom_1m': mom_1m, 'mom_3m': mom_3m, 'mom_6m': mom_6m,
        'hurst_60': h60, 'hurst_1y': h1y, 'regime': regime,
        'antonacci': antonacci_full[-252:], 'antonacci_latest': antonacci_latest,
        'qty_fissa': QTY_FIXED,
    }

# ── MAIN ─────────────────────────────────────────────
def main():
    now = datetime.now()
    exec_type = get_execution_type()
    print(f"RAPTOR Oil Fetch — {now.strftime('%Y-%m-%d %H:%M')} [{exec_type.upper()}]")

    # WTI Futures (25 anni, contesto)
    print("Scarico WTI Crude Oil Futures (CL=F)...")
    wti_closes, wti_highs, wti_lows, wti_opens, wti_vols, wti_dates = fetch_series(WTI_TICKER, "2000-01-01")
    print(f"WTI: {len(wti_closes)} barre ({wti_dates[0]} → {wti_dates[-1]})")
    wti_antonacci = calc_antonacci(wti_closes, wti_dates)
    wti_antonacci_latest = wti_antonacci[-1] if wti_antonacci else {}
    wti_kama_fast = calc_kama(wti_closes, n=5,  fast=3, slow=20)
    wti_kama_slow = calc_kama(wti_closes, n=20, fast=2, slow=40)
    wti_stagionalita = calc_stagionalita(wti_closes, wti_dates)

    if exec_type in ('morning', 'close', 'manual'):
        print(f"[{exec_type.upper()}] Calcolo analisi completa...")
        oil_long  = build_etp_block(TICKERS['long'],  "2018-01-01")
        oil_short = build_etp_block(TICKERS['short'], "2018-01-01")

        output = sanitize({
            'execution_type': exec_type,
            'updated_at': now.isoformat(),
            'updated_display': now.strftime('%d/%m/%Y %H:%M'),
            'wti': {
                'dates': wti_dates[-756:], 'closes': wti_closes[-756:],
                'highs': wti_highs[-756:], 'lows': wti_lows[-756:],
                'kama_fast': fmt(wti_kama_fast[-756:]), 'kama_slow': fmt(wti_kama_slow[-756:]),
            },
            'wti_stagionalita': wti_stagionalita,
            'antonacci_wti': wti_antonacci[-252:],
            'antonacci_wti_latest': wti_antonacci_latest,
            'oil_long': oil_long,
            'oil_short': oil_short,
        })
    else:
        print("[INTRADAY] Calcolo segnali veloci...")
        try:
            with open('oil_data.json','r',encoding='utf-8') as f:
                output = json.load(f)
        except Exception:
            output = {}
        oil_long  = build_etp_block(TICKERS['long'],  "2018-01-01")
        oil_short = build_etp_block(TICKERS['short'], "2018-01-01")
        output['execution_type'] = exec_type
        output['updated_at'] = now.isoformat()
        output['updated_display'] = now.strftime('%d/%m/%Y %H:%M')
        output['wti'] = {
            'dates': wti_dates[-756:], 'closes': wti_closes[-756:],
            'highs': wti_highs[-756:], 'lows': wti_lows[-756:],
            'kama_fast': fmt(wti_kama_fast[-756:]), 'kama_slow': fmt(wti_kama_slow[-756:]),
        }
        output['wti_stagionalita'] = wti_stagionalita
        output['antonacci_wti'] = wti_antonacci[-252:]
        output['antonacci_wti_latest'] = wti_antonacci_latest
        output['oil_long'] = oil_long
        output['oil_short'] = oil_short
        output = sanitize(output)

    os.makedirs('data', exist_ok=True)
    with open('oil_data.json','w',encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',',':'), allow_nan=False)
    print(f"✅ oil_data.json aggiornato [{exec_type}]")

if __name__ == '__main__':
    main()
