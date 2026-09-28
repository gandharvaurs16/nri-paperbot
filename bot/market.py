"""Unadjusted exchange-session OHLCV via Yahoo Finance; verify before simulation."""
from __future__ import annotations
import csv, io, json, statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]

def refresh_calendar(day:date):
    """Refresh official NSE CM holidays; fail closed if current-year data cannot be verified."""
    p=ROOT/'config'/f'holidays_{day.year}.json'
    if day.year<2026:
        if not p.exists():raise ValueError('Historical NSE calendar missing')
        return json.loads(p.read_text())
    url='https://www.nseindia.com/api/holiday-master?type=trading'
    try:
        ses=requests.Session();ses.headers.update({'User-Agent':'Mozilla/5.0','Accept':'application/json','Referer':'https://www.nseindia.com/resources/exchange-communication-holidays'})
        ses.get('https://www.nseindia.com/',timeout=15)
        response=ses.get(url,timeout=20);response.raise_for_status()
        items=response.json()['CM']
        mapping={datetime.strptime(x['tradingDate'],'%d-%b-%Y').date().isoformat():x['description'] for x in items}
        mapping={k:v for k,v in mapping.items() if k.startswith(str(day.year)+'-')}
        if len(mapping)<10:raise ValueError('Official calendar missing current year')
        clearing_url='https://www.nseindia.com/api/holiday-master?type=clearing'
        clearing_response=ses.get(clearing_url,timeout=20);clearing_response.raise_for_status()
        clearing_items=clearing_response.json()['CM']
        clearing={datetime.strptime(x['tradingDate'],'%d-%b-%Y').date().isoformat():x['description'] for x in clearing_items}
        clearing={k:v for k,v in clearing.items() if k.startswith(str(day.year)+'-')}
        if len(clearing)<10:raise ValueError('Official clearing calendar incomplete')
        data={'source':url,'clearing_source':clearing_url,'checked_on':datetime.now(timezone.utc).date().isoformat(),'coverage_end':f'{day.year}-12-31','holidays':mapping,'clearingHolidays':clearing}
        p.write_text(json.dumps(data,indent=2)+'\n')
        return data
    except Exception as ex:
        if p.exists():
            data=json.loads(p.read_text())
            checked=data.get('checked_on')
            if checked and (day-date.fromisoformat(checked)).days<=7:return data
        raise ValueError('Official NSE calendar refresh failed or stale: '+str(ex))

def calendar(day:date):
    if day.weekday()>=5:return 'market closed: weekend'
    cal=refresh_calendar(day)
    if day>date.fromisoformat(cal['coverage_end']):raise ValueError('NSE calendar coverage expired')
    if day.isoformat() in cal['holidays']:return 'market closed: '+cal['holidays'][day.isoformat()]
    return 'trading'

def universe(asof:date, offline=False):
    """Point-in-time membership. An unavailable historical snapshot cannot create buys."""
    folder=ROOT/'config/universe_history'
    archive=folder/f'{asof.isoformat()}.csv'
    if archive.exists():text=archive.read_text(encoding='utf-8-sig')
    elif offline or asof!=datetime.now(timezone(timedelta(hours=5,minutes=30))).date():
        older=sorted(p for p in folder.glob('*.csv') if p.stem<=asof.isoformat()) if folder.exists() else []
        if not older or (asof-date.fromisoformat(older[-1].stem)).days>31:
            raise ValueError('No point-in-time Nifty 200 membership snapshot within 31 days of '+asof.isoformat())
        archive=older[-1];text=archive.read_text(encoding='utf-8-sig')
    else:
        cfg=json.loads((ROOT/'config/settings.json').read_text())
        res=requests.get(cfg['universe_url'],headers={'User-Agent':'Mozilla/5.0'},timeout=25);res.raise_for_status()
        text=res.text
    rows=list(csv.DictReader(io.StringIO(text)))
    result={}
    for r in rows:
        symbol=(r.get('Symbol') or r.get('symbol') or '').strip().upper()
        sector=(r.get('Industry') or r.get('industry') or r.get('Sector') or 'Unclassified').strip()
        if symbol:result[symbol]=sector
    if len(result)<150:raise ValueError('Nifty 200 membership file incomplete')
    if not archive.exists():archive.parent.mkdir(parents=True,exist_ok=True);archive.write_text(text)
    return result

def yf_bars(symbols:list[str],start:date,end:date):
    import yfinance as yf
    result={s:{} for s in symbols}
    for off in range(0,len(symbols),20):
        chunk=symbols[off:off+20]
        labels=[('^NSEI' if s=='NIFTY50' else s+'.NS') for s in chunk]
        frame=yf.download(labels,start=start.isoformat(),end=(end+timedelta(days=1)).isoformat(),auto_adjust=False,actions=True,group_by='ticker',threads=True,progress=False,timeout=30)
        for symbol,label in zip(chunk,labels):
            try:
                part=frame[label] if len(labels)>1 else frame
                for idx,row in part.iterrows():
                    d=idx.date().isoformat()
                    if d<start.isoformat() or d>end.isoformat():continue
                    fields={k.lower():float(row[k]) for k in ('Open','High','Low','Close','Volume')}
                    if any(__import__('math').isnan(v) for v in fields.values()):continue
                    fields['split']=float(row.get('Stock Splits',0) or 0)
                    fields['dividend']=float(row.get('Dividends',0) or 0)
                    fields['source']='Yahoo Finance '+label+' daily unadjusted OHLCV'
                    result[symbol][d]=fields
            except (KeyError,TypeError,ValueError):continue
    return result

def valid_bar(symbol,day,b,previous=None,minimum=50):
    if not b:return 'missing or stale OHLCV'
    try:o,h,l,c,v=(float(b[k]) for k in ('open','high','low','close','volume'))
    except (KeyError,TypeError,ValueError):return 'incomplete OHLCV'
    if min(o,h,l,c)<=0 or not (l<=o<=h and l<=c<=h):return 'inconsistent OHLC'
    if symbol!='NIFTY50' and (c<minimum or v<=0):return 'price or volume below threshold'
    if previous and not b.get('split') and abs(c/previous-1)>.35:return 'abnormal daily move without verified corporate action'
    if b.get('split'):return 'corporate split needs independent verification'
    return None

def indicators(history:dict[str,dict],day:date):
    dates=sorted(k for k in history if k<=day.isoformat()); bars=[history[k] for k in dates]
    if len(bars)<205:return None
    closes=[b['close'] for b in bars];vol=[b['volume'] for b in bars]
    trs=[max(b['high']-b['low'],abs(b['high']-closes[i-1]),abs(b['low']-closes[i-1])) for i,b in enumerate(bars) if i>0]
    return {'close':closes[-1],'sma50':statistics.mean(closes[-50:]),'sma200':statistics.mean(closes[-200:]),'high20_prior':max(closes[-21:-1]),'low20_prior':min(closes[-21:-1]),'atr14':statistics.mean(trs[-14:]),'volume_ratio':vol[-1]/max(1,statistics.mean(vol[-21:-1])),'median_turnover20':statistics.median(b['close']*b['volume'] for b in bars[-20:]),'momentum60':closes[-1]/closes[-61]-1}

def headlines(symbols,day:date):
    """Current Yahoo headlines only; never backfill later headlines into a historical run."""
    if day != datetime.now(timezone(timedelta(hours=5,minutes=30))).date():return []
    import yfinance as yf
    cutoff=datetime.combine(day,datetime.min.time(),tzinfo=timezone(timedelta(hours=5,minutes=30))).replace(hour=15,minute=30).timestamp()
    out=[]
    for sym in symbols[:12]:
        try:
            for n in yf.Ticker(sym+'.NS').news[:5]:
                x=n.get('content',n);ts=x.get('pubDate') or n.get('providerPublishTime');
                if isinstance(ts,str):ts=datetime.fromisoformat(ts.replace('Z','+00:00')).timestamp()
                if ts and cutoff-3*86400<=ts<=cutoff:
                    out.append({'symbol':sym,'headline':str(x.get('title',''))[:200],'published_utc':datetime.fromtimestamp(ts,timezone.utc).isoformat(),'url':x.get('canonicalUrl',{}).get('url','') if isinstance(x.get('canonicalUrl'),dict) else ''})
        except Exception:continue
    return out[:30]
