"""Live arrangement fingerprint and hooks for an adjustment preview."""
import hashlib
import json

def fingerprint(state):
    value={'date':state['date'],'epoch':state.get('confirmationEpoch'),'closed':state.get('closed'),
           'lines':state['lines'],'plan':state.get('plan'),'persons':{pid:{k:p.get(k) for k in ('state','line','readyAt','assign','start','end','continuousStart','confirmationSequence')} for pid,p in state['persons'].items()},
           'tasks':{key:(task['revision'],task['status']) for key,task in state.get('rotationTasks',{}).items()}}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

class Hooks:
    def __init__(self,week): self.week=week;self.events=[]
    def log(self,*args): self.events.append(args)
    def segment(self,*args): pass
    def week_minutes(self): return self.week
