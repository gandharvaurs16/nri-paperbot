"""Claude proposes; local engine verifies and may reject every proposal."""
import json,os,requests,math
MODEL=os.getenv('CLAUDE_MODEL','claude-sonnet-5-5')
SCHEMA={'type':'object','properties':{
 'summary':{'type':'string'},'decisions':{'type':'array','items':{'type':'object','properties':{'action':{'type':'string','enum':['BUY','SELL','HOLD']},'symbol':{'type':'string'},'stop':{'type':'number'},'target':{'type':'number'},'reason':{'type':'string'},'confidence':{'type':'string','enum':['low','medium','high']}},'required':['action','symbol','stop','target','reason','confidence'],'additionalProperties':False}}},'required':['summary','decisions'],'additionalProperties':False}
REVIEW_SCHEMA={'type':'object','properties':{'summary':{'type':'string'},'changes':{'type':'array','items':{'type':'object','properties':{'parameter':{'type':'string','enum':['min_volume_ratio','min_momentum60']},'value':{'type':'number'},'reason':{'type':'string'},'expected_effect':{'type':'string'}},'required':['parameter','value','reason','expected_effect'],'additionalProperties':False}}},'required':['summary','changes'],'additionalProperties':False}

def query(payload,schema):
    key=os.getenv('ANTHROPIC_API_KEY')
    if not key:raise RuntimeError('ANTHROPIC_API_KEY missing')
    body={'model':MODEL,'max_tokens':1800,'system':'You are a paper-only Indian equity analyst. Output JSON only. No live orders. Decide using supplied as-of data, never future data. Propose at most three actions, with specific reasons. Hold when no robust setup. Hard limits cannot be changed.','messages':[{'role':'user','content':json.dumps(payload,separators=(',',':'),default=str)}],'output_config':{'format':{'type':'json_schema','schema':schema}}}
    r=requests.post('https://api.anthropic.com/v1/messages',headers={'x-api-key':key,'anthropic-version':'2023-06-01','content-type':'application/json'},json=body,timeout=60)
    r.raise_for_status();data=r.json()
    if data.get('stop_reason')!='end_turn':raise RuntimeError('Claude response incomplete')
    text=''.join(x.get('text','') for x in data.get('content',[]) if x.get('type')=='text')
    output=json.loads(text)
    if schema==SCHEMA:
        if not isinstance(output,dict) or set(output)!={'summary','decisions'} or not isinstance(output['summary'],str) or not isinstance(output['decisions'],list) or len(output['decisions'])>3:raise ValueError('Invalid decision envelope')
        for d in output['decisions']:
            if not isinstance(d,dict) or set(d)!={'action','symbol','stop','target','reason','confidence'} or d['action'] not in ('BUY','SELL','HOLD') or d['confidence'] not in ('low','medium','high'):raise ValueError('Invalid decision')
            if not isinstance(d['symbol'],str) or not isinstance(d['reason'],str) or not d['reason'].strip() or len(d['reason'])>700 or len(d['symbol'])>30:raise ValueError('Invalid decision text')
            if any(not isinstance(d[k],(int,float)) or isinstance(d[k],bool) or not math.isfinite(d[k]) for k in ('stop','target')):raise ValueError('Invalid stop/target')
    else:
        if not isinstance(output,dict) or set(output)!={'summary','changes'} or not isinstance(output['summary'],str) or not isinstance(output['changes'],list) or len(output['changes'])>2:raise ValueError('Invalid review')
        for change in output['changes']:
            if not isinstance(change,dict) or set(change)!={'parameter','value','reason','expected_effect'} or change['parameter'] not in ('min_volume_ratio','min_momentum60') or not isinstance(change['value'],(int,float)) or not math.isfinite(change['value']) or any(not isinstance(change[x],str) for x in ('reason','expected_effect')):raise ValueError('Invalid review change')
    return output,data.get('usage',{})

def decisions(payload):return query(payload,SCHEMA)
def review(payload):return query(payload,REVIEW_SCHEMA)
