"""Conservative dated scrip allowlist; absence or staleness means no new buys."""
import json
from datetime import date
from .market import ROOT

def approvals(day:date,backtest=False):
    folder=ROOT/'config/eligibility_history'
    candidates=sorted(p for p in folder.glob('*.json') if p.stem<=day.isoformat()) if folder.exists() else []
    if not backtest:
        current=ROOT/'config/eligibility.json'
        current_data=json.loads(current.read_text())
        current_date=current_data.get('checked_on') or ''
        if current_date<=day.isoformat() and (not candidates or current_date>=candidates[-1].stem):
            source=current
        elif candidates:source=candidates[-1]
        else:return set(),'no dated eligibility file'
    elif candidates:source=candidates[-1]
    else:return set(),'no historical eligibility file'
    data=json.loads(source.read_text())
    checked=data.get('checked_on');label=data.get('source')
    if not checked or not label or not isinstance(data.get('approved'),list):return set(),'undated or incomplete eligibility'
    age=(day-date.fromisoformat(checked)).days
    if age<0 or age>7:return set(),'eligibility is stale or from the future'
    return set(data['approved'])-set(data.get('blocked',[])),label
