"""Deterministic paper execution, settlement and risk gates. Claude cannot bypass these."""
from __future__ import annotations
import copy, math, statistics
from datetime import date,timedelta,datetime,timezone
from .market import indicators,valid_bar

class Rejected(Exception):pass

def charges(side,turnover,dp=True):
    brokerage=min(turnover*.005,50)
    stt=turnover*.001;exchange=turnover*.0000307;sebi=turnover*.000001
    gst=.18*(brokerage+exchange+sebi)
    stamp=turnover*.00015 if side=='BUY' else 0
    dpfee=15.34 if side=='SELL' and dp else 0
    ipft=turnover*.00000000118
    return {'brokerage':brokerage,'stt':stt,'nse':exchange,'sebi':sebi,'gst':gst,'stamp':stamp,'dp':dpfee,'ipft':ipft,'total':sum((brokerage,stt,exchange,sebi,gst,stamp,dpfee,ipft))}

def available(state,day):
    return state['cash']-sum(x['amount'] for x in state['cashSettlements'] if x['availableOn']>day.isoformat())

def value(state,bars):
    missing=[s for s,p in state['positions'].items() if s not in bars]
    if missing:return None,missing
    return state['cash']+sum(p['qty']*bars[s]['close'] for s,p in state['positions'].items()),[]

def next_session(day,closed):
    d=day+timedelta(days=1)
    while d.weekday()>=5 or d.isoformat() in closed:d+=timedelta(days=1)
    return d

def record(state,side,sym,qty,reference,day,clock,reason,source,stop=None,target=None,signal=None,trigger=None,closed=None):
    if qty<1 or not isinstance(qty,int):raise Rejected('quantity invalid')
    if reference<=0:raise Rejected('reference price invalid')
    fill=reference*(1.001 if side=='BUY' else .999)
    dp=not any(t['date']==day.isoformat() and t['symbol']==sym and t['side']=='SELL' for t in state['trades'])
    fee=charges(side,fill*qty,dp)
    if side=='BUY':
        if available(state,day)<fill*qty+fee['total']:raise Rejected('unsettled cash cannot fund buy')
        state['cash']-=fill*qty+fee['total']
        pos=state['positions'].setdefault(sym,{'qty':0,'cost':0,'sector':state['universe'][sym],'stop':stop,'target':target,'lots':[]})
        pos['qty']+=qty;pos['cost']+=fill*qty+fee['total'];pos['stop']=stop;pos['target']=target
        pos['lots'].append({'qty':qty,'entry':fill,'entryFee':fee['total'],'date':day.isoformat(),'initialRisk':fill-stop})
    else:
        pos=state['positions'].get(sym)
        if not pos or qty>pos['qty']:raise Rejected('sell exceeds holdings')
        if any(l['date']==day.isoformat() for l in pos['lots']):raise Rejected('same-day sale forbidden')
        left=qty;basis=0;risk=0;held=[]
        for lot in pos['lots']:
            if not left:break
            k=min(left,lot['qty']);basis+=k*(lot['entry']+lot['entryFee']/lot['qty']);risk+=k*lot['initialRisk'];held.append((day-date.fromisoformat(lot['date'])).days)
            lot['qty']-=k;left-=k
        pos['lots']=[l for l in pos['lots'] if l['qty']];pos['qty']-=qty;pos['cost']-=basis
        proceeds=fill*qty-fee['total'];state['cash']+=proceeds
        settle=next_session(day,closed or {})
        state['cashSettlements'].append({'date':day.isoformat(),'availableOn':settle.isoformat(),'amount':proceeds})
        pnl=proceeds-basis
        state['closed'].append({'symbol':sym,'date':day.isoformat(),'pnl':pnl,'r':pnl/risk if risk>0 else None,'days':max(held),'reason':reason,'classification':'pending post-mortem' if pnl<0 else 'winner'})
        state['estimatedTax']+=max(0,pnl)*(.125 if min(held)>365 else .20)
        state['estimatedTDS']=state.get('estimatedTDS',0)+max(0,pnl)*(.1495 if min(held)>365 else .2392)
        if not pos['qty']:del state['positions'][sym]
    state['totalCosts']+=fee['total']
    t={'id':len(state['trades'])+1,'date':day.isoformat(),'timeIST':clock+' IST','recordedAtIST':datetime.now(timezone(timedelta(hours=5,minutes=30))).isoformat(),'signalDate':signal,'side':side,'symbol':sym,'sector':state['universe'].get(sym,pos['sector'] if side=='SELL' else ''),'qty':qty,'price':reference,'fill':fill,'stop':stop,'target':target,'costs':fee,'reason':reason,'source':source,'initialRisk':fill-stop if side=='BUY' else 0,'trigger':trigger,'outcome':'open' if side=='BUY' else 'closed'}
    state['trades'].append(t)
    return t

def risk_gate(state,symbol,bar,stop,target,day,settings,eligibility,benchmark_ok=True):
    if state.get('paused'):raise Rejected('10% drawdown lock active')
    if not eligibility:raise Rejected('NRI/broker eligibility not verified')
    if not benchmark_ok:raise Rejected('Nifty trend filter failed')
    if symbol not in state['universe']:raise Rejected('outside verified Nifty 200 universe')
    if symbol in state['positions']:raise Rejected('one position per stock')
    if len(state['positions'])>=settings['max_positions']:raise Rejected('position count limit')
    err=valid_bar(symbol,day,bar,minimum=settings['min_price'])
    if err:raise Rejected(err)
    opening=bar['open']*1.001
    if not (0<stop<opening<target):raise Rejected('invalid stop/target after gap')
    per_share=opening-stop
    if target-opening<2*per_share:raise Rejected('target below 2R at actual open')
    equity=state.get('lastEquity') or settings['capital']
    risk_fraction=settings['risk_fraction']*(.5 if state.get('drawdown',0)>=.05 else 1)
    qty=math.floor(min(min(equity,settings['capital'])*risk_fraction/per_share,equity*settings['max_stock_weight']/opening,(available(state,day)-equity*settings['min_cash_weight'])/(opening*1.007)))
    if qty<1:raise Rejected('insufficient size after risk/cash rules')
    value=qty*opening
    sector=state['universe'][symbol]
    exposure=sum(p['qty']*(state.get('latestCloses',{}).get(s) or p['cost']/p['qty']) for s,p in state['positions'].items() if p['sector']==sector)
    if (exposure+value)/equity>settings['max_sector_weight']:raise Rejected('sector limit')
    heat=sum(sum(l['qty']*l['initialRisk'] for l in p['lots']) for p in state['positions'].values())
    if heat+qty*per_share>settings['max_heat']*equity:raise Rejected('portfolio heat')
    if available(state,day)-value-charges('BUY',value)['total']<equity*settings['min_cash_weight']:raise Rejected('cash reserve')
    return qty

def process_day(state,day,bars,histories,benchmark,settings,closed,brain_fn,news=None,eligibility=None,review_fn=None,settlement_closed=None):
    """Atomic logical day: caller saves only the returned state after success."""
    s=copy.deepcopy(state);ds=day.isoformat()
    if s.get('lastProcessed') and ds<=s['lastProcessed']:return s
    if ds in closed or day.weekday()>=5:
        s['sessions'].append({'date':ds,'status':'market closed','decisions':closed.get(ds,'weekend'),'sources':[{'url':'config/holidays_2026.json','asof':ds}]})
        s['lastProcessed']=ds;return s
    errors=[]
    required=['NIFTY50']+list(s['positions'])+[p['symbol'] for p in s['pendingOrders']]
    for sym in set(required):
        bar=benchmark if sym=='NIFTY50' else bars.get(sym)
        prev_dates=[k for k in histories.get(sym,{}) if k<ds]
        prev=histories[sym][max(prev_dates)]['close'] if prev_dates else None
        issue=valid_bar(sym,day,bar,prev,minimum=settings['min_price'])
        if issue:errors.append(sym+': '+issue)
    if errors:
        s['status']='Data unavailable '+ds+': '+', '.join(errors)
        return s
    # Obtain and validate the AI response before any fill on this date. An API error
    # leaves the entire day unchanged, including mechanical stop processing.
    ni=indicators(histories['NIFTY50'],day)
    candidates=[];data_issues=[]
    for sym,h in histories.items():
        if sym=='NIFTY50' or sym not in s['universe'] or sym in s['positions']:continue
        previous_days=[k for k in h if k<ds]
        issue=valid_bar(sym,day,h.get(ds),h[max(previous_days)]['close'] if previous_days else None,settings['min_price'])
        if issue:
            data_issues.append(sym+': '+issue);continue
        x=indicators(h,day)
        if x and x['close']>x['sma200'] and x['sma50']>x['sma200'] and x['close']>x['high20_prior'] and x['volume_ratio']>=s.get('strategy',{}).get('min_volume_ratio',1.2) and x['median_turnover20']>=settings['min_median_turnover'] and x['close']>=settings['min_price'] and x['momentum60']>=s.get('strategy',{}).get('min_momentum60',0.0) and (eligibility or {}).get(sym,False):
            candidates.append({'symbol':sym,'sector':s['universe'][sym],**x})
    candidates=sorted(candidates,key=lambda x:x['momentum60'],reverse=True)[:20]
    s['watchlist']=[{'symbol':x['symbol'],'sector':x['sector'],'status':'Verified EOD candidate; next-open risk check required'} for x in candidates]
    try:
        proposal,usage=brain_fn({'date':ds,'portfolio':{'cash':s['cash'],'available':available(s,day),'equity_at_last_close':s.get('lastEquity'),'positions':s['positions'],'drawdown':s.get('drawdown',0)},'nifty':ni,'candidates':candidates,'news':news or [],'framework':s['frameworkHistory'][-1],'constraints':settings})
    except Exception as ex:
        s['status']='AI unavailable '+ds+': '+str(ex)[:120]
        return s
    # Release settled cash implicitly via available(). Enforce queued sells before queued buys.
    pending=sorted(s['pendingOrders'],key=lambda p:0 if p['side']=='SELL' else 1);s['pendingOrders']=[]
    decisions=[]
    for order in pending:
        sym=order['symbol'];bar=bars.get(sym)
        if not bar or sym not in s['universe']:
            decisions.append('Canceled '+sym+': missing opening quote or universe');continue
        try:
            if order['side']=='BUY':
                qty=risk_gate(s,sym,bar,order['stop'],order['target'],day,settings,(eligibility or {}).get(sym,False),benchmark_ok=(lambda prior: len(prior)>=200 and prior[-1]>statistics.mean(prior[-200:]))([histories['NIFTY50'][k]['close'] for k in sorted(histories['NIFTY50']) if k<ds]))
                t=record(s,'BUY',sym,qty,bar['open'],day,'09:15',order['reason'],bar['source'],stop=order['stop'],target=order['target'],signal=order['signalDate'])
            else:
                pos=s['positions'].get(sym)
                if not pos:continue
                t=record(s,'SELL',sym,pos['qty'],bar['open'],day,'09:15',order['reason'],bar['source'],signal=order['signalDate'],trigger=order.get('trigger','signal'),closed=settlement_closed or closed)
            decisions.append(t['side']+' '+sym+' '+str(t['qty'])+' @ '+str(round(t['fill'],2)))
        except Rejected as ex:decisions.append('Rejected '+sym+': '+str(ex))
    # Existing positions: daily bar cannot order target/stop within the day. Prefer adverse fill.
    for sym,pos in list(s['positions'].items()):
        b=bars.get(sym);last_buy=pos['lots'][-1]['date']==ds
        stop_hit=b['low']<=pos['stop'];target_hit=b['high']>=pos['target']
        if not(stop_hit or target_hit):continue
        trigger='stop' if stop_hit else 'target'
        if stop_hit and target_hit:decisions.append(sym+': stop and target both hit; assumed stop first')
        if last_buy:
            s['pendingOrders'].append({'side':'SELL','symbol':sym,'reason':'Same-day '+trigger+' observed; delivery prevents sale today','signalDate':ds,'trigger':trigger})
            decisions.append(sym+': '+trigger+' observed on buy day, next-session exit queued')
            continue
        reference=min(pos['stop'],b['open']) if trigger=='stop' else max(pos['target'],b['open'])
        try:
            t=record(s,'SELL',sym,pos['qty'],reference,day,'within-session OHLC' if reference!=b['open'] else '09:15',trigger+' hit'+(' (daily order ambiguous)' if stop_hit and target_hit else ''),b['source'],signal=ds,trigger=trigger,closed=settlement_closed or closed)
            decisions.append('Exit '+sym+' '+trigger+' @ '+str(round(t['fill'],2)))
        except Rejected as ex:decisions.append('Exit deferred '+sym+': '+str(ex))
    equity,missing=value(s,bars)
    if missing:raise ValueError('Missing position closes: '+','.join(missing))
    peak=max(s.get('peakEquity',settings['capital']),equity);s['peakEquity']=peak;s['lastEquity']=equity
    worst_intraday=s['cash']+sum(p['qty']*bars[sym]['low'] for sym,p in s['positions'].items())
    s['drawdown']=max(1-equity/peak,1-worst_intraday/peak)
    s['latestCloses']={k:b['close'] for k,b in bars.items()}
    if s['drawdown']>=settings['drawdown_stop'] and not s.get('paused'):
        s['paused']=True;s['pendingOrders']=[{'side':'SELL','symbol':sym,'reason':'Hard drawdown liquidation','signalDate':ds,'trigger':'drawdown'} for sym in s['positions']]
        decisions.append('Drawdown threshold reached; liquidation queued at next open; gap may exceed 10%')
    for d in proposal['decisions']:
        sym=d['symbol'].upper().strip();action=d['action']
        if action=='HOLD':continue
        if action=='BUY':
            x=next((c for c in candidates if c['symbol']==sym),None)
            if not x:decisions.append('Rejected '+sym+': not in verified candidate set');continue
            if not (0<d['stop']<x['close']<d['target'] and d['target']-x['close']>=2*(x['close']-d['stop'])):decisions.append('Rejected '+sym+': stop/2R invalid');continue
            if len(s['pendingOrders'])>=3:continue
            s['pendingOrders'].append({'side':'BUY','symbol':sym,'stop':d['stop'],'target':d['target'],'reason':d['reason'],'confidence':d['confidence'],'signalDate':ds})
            decisions.append('Queued BUY '+sym+' for next open')
        elif action=='SELL' and sym in s['positions']:
            s['pendingOrders'].append({'side':'SELL','symbol':sym,'reason':d['reason'],'signalDate':ds,'trigger':'signal'});decisions.append('Queued SELL '+sym+' for next open')
        else:decisions.append('Rejected '+sym+': unsupported action/holding')
    losers=[x for x in s['closed'] if x['date']==ds and x['pnl']<0]
    for x in losers:
        x['classification']='rule-following loss' if x['reason'].startswith(('stop','Same-day')) else 'review required'
        x['postmortem']='Loss '+str(round(x['pnl'],2))+'. '+x['classification']+'; review slippage, data and initial setup in weekly review.'
    if day.weekday()==4 and review_fn:
        recent=[t for t in s['closed'] if (day-date.fromisoformat(t['date'])).days<=6]
        try:
            rev,_=review_fn({'date':ds,'trades':recent,'framework':s['frameworkHistory'][-1],'rule':'Only evidence-backed changes; never change hard limits.'})
            applied=[]
            # Evidence threshold avoids optimizing a handful of trades. Only two bounded
            # strategy parameters can change; risk configuration stays immutable.
            if len(recent)>=8 and sum(x['pnl']<0 for x in recent)>=2:
                bounds={'min_volume_ratio':(1.0,2.0),'min_momentum60':(-.05,.25)}
                for proposal in rev['changes'][:2]:
                    key=proposal['parameter'];value=proposal['value']
                    if key in bounds and bounds[key][0]<=value<=bounds[key][1] and len(proposal['reason'])>=20 and key not in [x['parameter'] for x in applied]:
                        old=s['strategy'][key];s['strategy'][key]=value
                        applied.append({**proposal,'old':old})
                if applied:
                    parts=s['settings']['frameworkVersion'].lstrip('v').split('.')
                    new='v'+parts[0]+'.'+str(int(parts[1])+1)
                    s['settings']['frameworkVersion']=new
                    s['frameworkHistory'].append({'version':new,'date':ds,'reason':'Weekly evidence review on '+str(len(recent))+' closed trades','changes':applied,'expectedEffect':'; '.join(x['expected_effect'] for x in applied)})
            s['reviews'].append({'date':ds,'summary':rev['summary'],'proposals':rev['changes'],'applied':bool(applied),'reason_if_not_applied':'Insufficient sample or out-of-bounds proposal' if not applied else ''})
        except Exception as ex:s['reviews'].append({'date':ds,'summary':'Review unavailable: '+str(ex),'proposals':[],'applied':False})
    s['snapshots'].append({'date':ds,'nifty':benchmark['close'],'closes':{sym:b['close'] for sym,b in bars.items()},'source':'Yahoo Finance daily unadjusted OHLCV; '+benchmark['source']})
    s['sessions'].append({'date':ds,'status':'trading','decisions':'; '.join(decisions) or 'No trade','portfolioValue':equity,'cashAvailable':available(s,day),'unsettledCash':s['cash']-available(s,day),'benchmarkClose':benchmark['close'],'drawdown':s['drawdown'],'frameworkVersion':s['settings']['frameworkVersion'],'sources':[{'url':'https://finance.yahoo.com','asof':ds}],'apiUsage':usage,'dataIssues':data_issues[:50]})
    prior_nifty=[histories['NIFTY50'][k]['close'] for k in sorted(histories['NIFTY50']) if k<ds]
    nifty_move=(benchmark['close']/prior_nifty[-1]-1)*100 if prior_nifty else None
    market_note='Nifty 50 closed '+str(round(benchmark['close'],2))+((' ('+('%+.2f'%nifty_move)+'% vs prior session)') if nifty_move is not None else ' (prior close unavailable)')
    headlines_note='; Headlines: '+' | '.join(x['headline'] for x in (news or [])[:3]) if news else '; no verified as-of headlines supplied'
    s['reports'].append({'date':ds,'summary':market_note+headlines_note+'. '+proposal['summary'],'decisions':'; '.join(decisions) or 'No trade; setup did not qualify','plan':'Pending orders at next trading open: '+(', '.join(x['side']+' '+x['symbol'] for x in s['pendingOrders']) or 'none')})
    s['lastProcessed']=ds;s['status']='Processed '+ds
    if ds=='2026-10-27':s['milestoneReport']={'date':ds,'portfolioValue':equity,'nifty':benchmark['close'],'note':'30-day milestone. Strategy continues until stopped.'}
    return s
