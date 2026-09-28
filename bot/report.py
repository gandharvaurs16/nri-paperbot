"""Thirty-day milestone report, using only ledger facts."""
from __future__ import annotations
import json,math,statistics
from pathlib import Path

def build(state):
    sessions=[x for x in state['sessions'] if x['date']<='2026-10-27' and x.get('portfolioValue') is not None]
    closed=[x for x in state['closed'] if x['date']<='2026-10-27']
    start=100000;end=sessions[-1]['portfolioValue'] if sessions else None
    first_index=sessions[0]['benchmarkClose'] if sessions else None;last_index=sessions[-1]['benchmarkClose'] if sessions else None
    returns=[sessions[i]['portfolioValue']/sessions[i-1]['portfolioValue']-1 for i in range(1,len(sessions))]
    wins=[x for x in closed if x['pnl']>0];losers=[x for x in closed if x['pnl']<0]
    avg=lambda xs:sum(x['pnl'] for x in xs)/len(xs) if xs else None
    mean=statistics.mean(returns) if returns else 0;sd=statistics.stdev(returns) if len(returns)>2 else 0
    best=max(closed,key=lambda x:x['pnl']) if closed else None;worst=min(closed,key=lambda x:x['pnl']) if closed else None
    trade_costs=sum(t['costs']['total'] for t in state['trades'] if t['date']<='2026-10-27')
    gain_tax=sum(max(0,x['pnl'])*(.125 if x['days']>365 else .20) for x in closed)
    tds=sum(max(0,x['pnl'])*(.1495 if x['days']>365 else .2392) for x in closed)
    r_values=[x['r'] for x in closed if x.get('r') is not None]
    return {'period':'2026-09-28 to 2026-10-27','status':'complete' if sessions and sessions[-1]['date']=='2026-10-27' else 'incomplete','ending_equity':end,'total_return_pct':(end/start-1)*100 if end is not None else None,'benchmark_return_pct':(last_index/first_index-1)*100 if first_index and last_index else None,'equity_curve':[{'date':x['date'],'equity':x['portfolioValue'],'nifty':x['benchmarkClose']} for x in sessions],'max_drawdown_pct':max([x.get('drawdown',0) for x in sessions] or [0])*100,'closed_trades':len(closed),'win_rate_pct':len(wins)/len(closed)*100 if closed else None,'average_win':avg(wins),'average_loss':avg(losers),'average_r':statistics.mean(r_values) if r_values else None,'expectancy':avg(closed),'profit_factor':sum(x['pnl'] for x in wins)/-sum(x['pnl'] for x in losers) if losers else None,'sharpe_approx':math.sqrt(252)*mean/sd if sd else None,'total_costs':trade_costs,'estimated_tax':gain_tax,'estimated_tds':tds,'best_trade':best,'worst_trade':worst,'framework_versions':[x for x in state['frameworkHistory'] if x['date']<='2026-10-27'],'version_effect':'Insufficient 30-day evidence to attribute performance changes to framework versions.','assessment':'Thirty calendar days and a small number of trades cannot establish a reliable edge. Treat positive results as potentially luck. Test at least 100–200 out-of-sample trades with clean point-in-time data and comparable costs before real money.'}

def write_report(state,root:Path):
    report=build(state)
    for path in (root/'data/final_30_day_report.json',root/'docs/final_30_day_report.json'):
        path.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
