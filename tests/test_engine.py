import copy,unittest
from datetime import date,timedelta
from bot.engine import charges,record,available,risk_gate,Rejected,process_day

CFG={'capital':100000,'risk_fraction':.005,'max_positions':6,'max_stock_weight':.2,'max_sector_weight':.35,'min_cash_weight':.2,'max_heat':.03,'drawdown_stop':.10,'min_price':50,'min_median_turnover':100000000}

def state():
 return {'cash':100000.,'cashSettlements':[],'positions':{},'universe':{'ABC':'IT'},'trades':[],'closed':[],'totalCosts':0.,'estimatedTax':0.,'lastEquity':100000.,'peakEquity':100000.,'drawdown':0.,'latestCloses':{},'paused':False,'pendingOrders':[],'sessions':[],'snapshots':[],'reports':[],'reviews':[],'frameworkHistory':[{'version':'v1.0'}],'settings':{'frameworkVersion':'v1.0'},'strategy':{'min_volume_ratio':1.2,'min_momentum60':0},'lastProcessed':None}
class EngineTests(unittest.TestCase):
 def test_costs_include_sell_dp_and_no_sell_stamp(self):
  buy=charges('BUY',10000);sell=charges('SELL',10000)
  self.assertAlmostEqual(buy['stamp'],1.5);self.assertEqual(sell['stamp'],0)
  self.assertAlmostEqual(sell['dp'],15.34);self.assertGreater(buy['brokerage'],0)
 def test_t_plus_one_and_no_same_day_sale(self):
  s=state();d=date(2026,9,28)
  record(s,'BUY','ABC',10,100,d,'09:15','test','fixture',stop=90,target=120,signal='2026-09-25')
  with self.assertRaises(Rejected):record(s,'SELL','ABC',10,95,d,'15:30','stop','fixture',signal=d.isoformat(),closed={})
  sale=record(s,'SELL','ABC',10,110,d+timedelta(days=1),'09:15','target','fixture',signal=d.isoformat(),closed={})
  self.assertEqual(s['cashSettlements'][0]['availableOn'],'2026-09-30')
  self.assertLess(available(s,date(2026,9,29)),s['cash'])
 def test_risk_size_and_drawdown_lock(self):
  s=state();b={'open':100,'high':105,'low':95,'close':102,'volume':2000000}
  q=risk_gate(s,'ABC',b,90,125,date(2026,9,29),CFG,True)
  self.assertLessEqual(q*(100.1-90),500)
  s['paused']=True
  with self.assertRaises(Rejected):risk_gate(s,'ABC',b,90,125,date(2026,9,29),CFG,True)
 def test_ai_error_fails_before_pending_fill(self):
  s=state();s['pendingOrders']=[{'side':'BUY','symbol':'ABC','stop':90,'target':125,'reason':'breakout','signalDate':'2026-09-28'}]
  day=date(2026,9,29);b={'open':100,'high':105,'low':95,'close':102,'volume':2000000,'source':'fixture'}
  hist={'ABC':{day.isoformat():b},'NIFTY50':{day.isoformat():{**b,'close':25000,'open':25000,'high':25010,'low':24900}}}
  def fail(_):raise RuntimeError('API unavailable')
  out=process_day(s,day,{'ABC':b},hist,hist['NIFTY50'][day.isoformat()],CFG,{},fail,eligibility={'ABC':True})
  self.assertEqual(out['trades'],[]);self.assertIsNone(out['lastProcessed']);self.assertEqual(len(out['pendingOrders']),1)
 def test_holiday_does_not_call_ai_or_change_cash(self):
  s=state();day=date(2026,10,2)
  out=process_day(s,day,{}, {},{},CFG,{day.isoformat():'Gandhi Jayanti'},lambda _:self.fail('AI should not run'))
  self.assertEqual(out['lastProcessed'],day.isoformat());self.assertEqual(out['sessions'][0]['status'],'market closed')
  self.assertEqual(out['cash'],100000)
if __name__=='__main__':unittest.main()
