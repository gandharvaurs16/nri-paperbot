"""Scheduled paper runner. No broker API or live-order code exists."""
from __future__ import annotations
import argparse,json,os,sys,traceback
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from .market import ROOT,calendar,refresh_calendar,universe,yf_bars,headlines
from .engine import process_day
from .brain import decisions,review
from .report import write_report
from .eligibility import approvals

IST=timezone(timedelta(hours=5,minutes=30))
STATE=ROOT/'data/state.json'

def write(state):
    state['revision']=state.get('revision',0)+1
    txt=json.dumps(state,indent=2,ensure_ascii=False,allow_nan=False)+'\n'
    tmp=STATE.with_suffix('.tmp');tmp.write_text(txt);tmp.replace(STATE)
    (ROOT/'docs/state.json').write_text(txt)

def incident(state,day,message,backtest=False):
    rec={'date':day.isoformat(),'timeIST':datetime.now(IST).isoformat(),'message':message[:500]}
    if not any(x['date']==rec['date'] and x['message']==rec['message'] for x in state['incidents']):state['incidents'].append(rec)
    state['status']='No new trades: '+message[:120]
    if not backtest:write(state)
    print('ALERT:',message,file=sys.stderr)

def run(target=None,backtest=False,brain=decisions,weekly=review):
    cfg=json.loads((ROOT/'config/settings.json').read_text());state=json.loads(STATE.read_text())
    now=datetime.now(IST)
    if target is None:target=now.date()
    if not backtest and target>=now.date() and now.time() < __import__('datetime').time(16,15):
        print('NSE close and data publication not yet due');return 0
    start=date.fromisoformat(state['lastProcessed'])+timedelta(days=1) if state['lastProcessed'] else date.fromisoformat(state['settings']['start'] if backtest else cfg['start'])
    if target<start:print('No unprocessed dates');return 0
    target=min(target,now.date() if not backtest else target)
    members=None;histories={}
    day=start
    while day<=target:
        ds=day.isoformat()
        try:kind=calendar(day)
        except ValueError as ex:
            incident(state,day,str(ex),backtest);return 2
        if kind=='trading':
            membership_missing=False
            try:day_members=universe(day,offline=backtest)
            except ValueError as ex:
                if backtest:
                    incident(state,day,str(ex),True);return 2
                membership_missing=True
                try:day_members=universe(datetime.now(IST).date())
                except Exception as fetch_error:
                    incident(state,day,'Data/universe retrieval failed: '+str(fetch_error),False);return 2
            if members is None:
                members=day_members
                try:histories=yf_bars(['NIFTY50']+list(members),start-timedelta(days=430),target)
                except Exception as ex:
                    incident(state,day,'Price retrieval failed: '+str(ex),backtest);return 2
            newcomers=[sym for sym in day_members if sym not in histories]
            if newcomers:
                try:histories.update(yf_bars(newcomers,start-timedelta(days=430),target))
                except Exception as ex:
                    incident(state,day,'New constituent price retrieval failed: '+str(ex),backtest);return 2
            state['universe']=day_members
            benchmark=histories['NIFTY50'].get(ds)
            bars={sym:h[ds] for sym,h in histories.items() if sym!='NIFTY50' and ds in h}
            # NRO non-PIS is not subject to the PIS caution/ban list per Zerodha.
            # This check screens the Nifty 200 universe and a repository denylist.
            allow,eligibility_source=approvals(day,backtest)
            eligible={sym:(sym in allow and not membership_missing) for sym in day_members}
            # For backtest news is intentionally empty; future headlines would leak.
            news=[] if backtest else headlines(list(bars),day)
            try:
                day_calendar=refresh_calendar(day)
                candidate=process_day(state,day,bars,histories,benchmark,cfg,
                                      day_calendar['holidays'],
                                      brain,news,eligible,weekly,
                                      {**day_calendar['holidays'],**day_calendar.get('clearingHolidays',{})})
            except Exception as ex:
                incident(state,day,'Run failed without ledger advance: '+str(ex),backtest);return 2
            if candidate['lastProcessed']!=ds:
                incident(state,day,candidate.get('status','Data or AI unavailable; will retry same date'),backtest);return 2
            state=candidate
            state['sessions'][-1]['eligibilitySource']=eligibility_source
            if not allow:
                state['sessions'][-1]['decisions']+='; no new entries: '+eligibility_source
                state['reports'][-1]['decisions']+='; no new entries: '+eligibility_source
            if membership_missing:
                state['sessions'][-1]['decisions']+='; no new entries: historical membership snapshot missing'
                state['reports'][-1]['decisions']+='; no new entries: historical membership snapshot missing'
        else:
            holidays={} if day.weekday()>=5 else refresh_calendar(day)['holidays']
            state=process_day(state,day,{},histories,{},cfg,holidays,brain)
            state['sessions'][-1]['decisions']=kind
        if backtest:
            STATE.write_text(json.dumps(state,indent=2)+'\n')
        else:
            write(state)
            if day>=date(2026,10,27) and state.get('milestoneReport'):write_report(state,ROOT)
        day+=timedelta(days=1)
    if backtest:
        from .report import build
        result=build(state);result['backtest_range']=[start.isoformat(),target.isoformat()];result['data_note']='Point-in-time constituent snapshots required; historical news omitted rather than leaked.'
        (ROOT/'data/backtest_output.json').write_text(json.dumps(result,indent=2,default=str)+'\n')
        print(json.dumps({'start':start.isoformat(),'end':target.isoformat(),'equity':state.get('lastEquity'),'trades':len(state['trades']),'report':'data/backtest_output.json'},indent=2))
    else:print('Processed through',state['lastProcessed'],'equity',round(state.get('lastEquity',100000),2))
    return 0

def cli():
    p=argparse.ArgumentParser();p.add_argument('--date',type=date.fromisoformat);p.add_argument('--backtest',action='store_true');p.add_argument('--from-date',type=date.fromisoformat);args=p.parse_args()
    if args.backtest:
        if not args.from_date or not args.date:p.error('--backtest needs --from-date and --date')
        # Backtests use a temporary ledger and the same execution engine, but consume API calls.
        global STATE
        source=json.loads(STATE.read_text())
        scratch=ROOT/'data/backtest_state.json';source.update({'lastProcessed':None,'snapshots':[],'trades':[],'reports':[],'sessions':[],'pendingOrders':[],'cashSettlements':[],'reviews':[],'closed':[],'cash':100000,'positions':{},'peakEquity':100000,'lastEquity':100000,'totalCosts':0,'estimatedTax':0,'estimatedTDS':0,'paused':False})
        source['settings']['start']=args.from_date.isoformat();scratch.write_text(json.dumps(source));STATE=scratch
        try:return run(args.date,backtest=True)
        finally:scratch.unlink(missing_ok=True)
    return run(args.date)
if __name__=='__main__':sys.exit(cli())
