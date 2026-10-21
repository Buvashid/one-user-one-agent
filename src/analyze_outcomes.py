#!/usr/bin/env python3
"""Attribute replay outcomes and estimate randomized intention-to-treat differences."""
import argparse, csv, json, os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
UTC=timezone.utc; IST=timezone(timedelta(hours=5,minutes=30))
def dt(s): return datetime.fromisoformat(s.replace('Z','+00:00')) if s else None
def readj(path):
    with open(path,encoding='utf-8') as f:
        for line in f:
            if line.strip(): yield json.loads(line)
def readc(path):
    with open(path,encoding='utf-8',newline='') as f: return list(csv.DictReader(f))

def goal_is_open_at_correction(row, correction):
    """True only when a committed goal spans the rule-correction instant."""
    if not correction:
        return False
    start=dt(row.get('goal_started_at'))
    if not start:
        return False
    end=start+timedelta(hours=float(row.get('window_hours') or 0))
    return start < correction < end

def goal_succeeds(row, events, goals, correction, catalogue):
    """Return (success, event_id) using only attributable, in-window signals."""
    gid=row['goal_id']; start=dt(row['goal_started_at'])
    end=start+timedelta(hours=float(row.get('window_hours') or 0))
    goal=goals.get(gid,{})
    success=goal.get('success') or {}
    target=success.get('event')
    for e in events:
        t=dt(e.get('received_at') or e.get('occurred_at'))
        if not t or t < start or t > end: continue
        ev=e.get('event'); props=e.get('properties') or {}
        if gid=='G03' and correction and end > correction:
            target='play_started'
            if ev=='play_started' and t.astimezone(IST).date()>start.astimezone(IST).date():
                return True,e.get('event_id')
            continue
        if gid in ('G04','G05') and ev=='purchase_completed' and (props.get('is_trial') is False or str(props.get('is_trial')).lower()=='false'):
            return True,e.get('event_id')
        if gid=='G06' and ev=='purchase_completed' and (props.get('is_trial') is False or str(props.get('is_trial')).lower()=='false'):
            return True,e.get('event_id')
        if gid=='G07' and ev=='play_started':
            target_slug=row.get('target_content_slug')
            title=catalogue.get(props.get('title_id'))
            if target_slug and title and title.get('content_slug')==target_slug:
                return True,e.get('event_id')
        if gid=='G08' and ev=='play_started':
            title=catalogue.get(props.get('title_id'))
            if title and title.get('release_date'):
                from datetime import date
                age=(start.astimezone(IST).date()-date.fromisoformat(title['release_date'])).days
                if 0<=age<=30:return True,e.get('event_id')
        if gid=='G09' and ev=='play_started':
            plays=[x for x in events if x.get('event')=='play_started' and dt(x.get('received_at') or x.get('occurred_at')) and start<=dt(x.get('received_at') or x.get('occurred_at'))<=end]
            days={dt(x.get('received_at') or x.get('occurred_at')).astimezone(IST).date() for x in plays}
            if len(days)>=3:return True,e.get('event_id')
        if target and ev==target:
            if gid in ('G01','G02','G12'):
                return True,e.get('event_id')
    return False,None

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--pack',default='one_user_one_agent_pack'); ap.add_argument('--out',default='sample_run'); a=ap.parse_args()
    goals={g['goal_id']:g for g in json.load(open(os.path.join(a.pack,'config/goals.json'),encoding='utf-8'))['goals']}
    controls=list(readj(os.path.join(a.pack,'replay/control_events.jsonl')))
    correction=next((dt(c['at']) for c in controls if c['type']=='goal_rule_corrected' and c.get('goal_id')=='G03'),None)
    goal_rows=list(readj(os.path.join(a.out,'outcomes.jsonl')))
    sim_events=list(readj(os.path.join(a.out,'sim','outcome_events.jsonl')))
    arms={r['user_id']:r['arm'] for r in readc(os.path.join(a.pack,'replay/arms.csv'))}
    cat={r['title_id']:r for r in readc(os.path.join(a.pack,'data/catalogue.csv'))}
    ev_by=defaultdict(list)
    for e in sim_events: ev_by[e['user_id']].append(e)
    for u in ev_by: ev_by[u].sort(key=lambda e:dt(e.get('received_at') or e.get('occurred_at')))
    observed_times=[dt(e.get('received_at') or e.get('occurred_at')) for e in sim_events if dt(e.get('received_at') or e.get('occurred_at'))]
    max_observed=max(observed_times) if observed_times else None
    for row in goal_rows:
        events=ev_by.get(row['user_id'],[])
        ok,event_id=goal_succeeds(row,events,goals,correction,cat)
        start=dt(row['goal_started_at']); end=start+timedelta(hours=float(row.get('window_hours') or 0))
        row['rule_correction_applied']=bool(row.get('goal_id')=='G03' and correction and end > correction)
        row['rule_rejudged_at_correction']=bool(row.get('goal_id')=='G03' and goal_is_open_at_correction(row,correction))
        row['success']=ok; row['success_event_id']=event_id
        if ok:
            row['status']='succeeded'
        elif row.get('goal_id')=='G07' and not row.get('target_content_slug'):
            row['status']='inconclusive_missing_target'
        elif max_observed is None or max_observed < end:
            row['status']='inconclusive_window_not_fully_observed'
        else:
            row['status']='failed_no_attributable_signal'
    with open(os.path.join(a.out,'outcomes.jsonl'),'w',encoding='utf-8') as f:
        for row in goal_rows:f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
    # Randomized intention-to-treat metric: every triggered user, regardless of whether an action was served.
    index={}
    for e in readj(os.path.join(a.pack,'replay/triggers.jsonl')):
        user=e['user_id']; t=dt(e['deliver_at'])
        if user not in index or t<index[user]: index[user]=t
    play_users=set()
    for user,events in ev_by.items():
        start=index.get(user)
        if start and any(e.get('event')=='play_started' and dt(e.get('received_at') or e.get('occurred_at')) and start<=dt(e.get('received_at') or e.get('occurred_at'))<=start+timedelta(days=7) for e in events):
            play_users.add(user)
    groups=defaultdict(list)
    for user in index: groups[arms.get(user,'unknown')].append(user in play_users)
    def stats(vals): return {'n':len(vals),'successes':sum(vals),'rate':round(sum(vals)/len(vals),4) if vals else None}
    agent=stats(groups.get('agent',[])); hold=stats(groups.get('holdout',[])); diff=(agent['successes']/agent['n']-hold['successes']/hold['n']) if agent['n'] and hold['n'] else None
    eligible=defaultdict(list)
    for row in goal_rows:
        if row['arm'] in ('agent','holdout'):
            eligible[row['arm']].append(row['status']=='succeeded')
    # Approximate 95% confidence interval for each binomial rate using Wilson bounds;
    # use the Newcombe-style conservative difference interval from those bounds.
    import math
    def wilson(successes,n,z=1.959963984540054):
        if not n: return (None,None)
        p=successes/n; den=1+z*z/n
        center=(p+z*z/(2*n))/den
        half=z*math.sqrt((p*(1-p)/n)+(z*z/(4*n*n)))/den
        return (max(0.0,center-half),min(1.0,center+half))
    agent_ci=wilson(agent['successes'],agent['n']); hold_ci=wilson(hold['successes'],hold['n'])
    diff_ci=(agent_ci[0]-hold_ci[1],agent_ci[1]-hold_ci[0]) if agent_ci[0] is not None and hold_ci[0] is not None else (None,None)
    correction_rejudged=sum(1 for row in goal_rows if row.get('rule_rejudged_at_correction'))
    correction_applied=sum(1 for row in goal_rows if row.get('rule_correction_applied'))
    with open(os.path.join(a.out,'impact_report.md'),'w',encoding='utf-8') as f:
        f.write('# Replay impact report\n\n')
        f.write('## Primary intention-to-treat outcome: any play within 7 days of first replay trigger\n\n')
        f.write('| Randomized arm | Users | Users with play | Rate |\n|---|---:|---:|---:|\n')
        for name,stats_row in [('agent',agent),('holdout',hold)]: f.write(f"| {name} | {stats_row['n']} | {stats_row['successes']} | {stats_row['rate']:.1%} |\n")
        f.write(f"\nAbsolute difference (agent minus holdout): **{diff*100:+.2f} percentage points**. Approximate 95% CI for the difference (Wilson/Newcombe): **[{diff_ci[0]*100:+.2f}, {diff_ci[1]*100:+.2f}] percentage points**. Agent rate 95% Wilson CI: [{agent_ci[0]*100:.2f}%, {agent_ci[1]*100:.2f}%]; holdout rate 95% Wilson CI: [{hold_ci[0]*100:.2f}%, {hold_ci[1]*100:.2f}%]. These intervals quantify sampling uncertainty under the replay's randomized assignment; the result is still specific to this synthetic replay and simulator seed and is not proof of production causal uplift.\n\n")
        f.write(f"## Corrected goal rule audit\n\n- G03 commitments re-judged because they were open at the correction time: **{correction_rejudged}**\n- G03 commitments evaluated under the corrected rule (including commitments created after correction): **{correction_applied}**\n- Only goals whose commitment window spans the correction instant are marked as re-judged; closed goals are not retroactively re-judged.\n\n")
        f.write('## Goal-level success (conditional on a measurable goal being selected)\n\n| Arm | Goal commitments | Successes | Rate |\n|---|---:|---:|---:|\n')
        for name,vals in [('agent',eligible.get('agent',[])),('holdout',eligible.get('holdout',[]))]: f.write(f"| {name} | {len(vals)} | {sum(vals)} | {(format(sum(vals)/len(vals), '.1%') if vals else 'n/a')} |\n")
        status_counts=defaultdict(int)
        for row in goal_rows: status_counts[row['status']]+=1
        f.write('\n### Goal outcome status counts\n\n')
        for status,count in sorted(status_counts.items()): f.write(f'- `{status}`: {count}\n')
        f.write('\nG07 success requires a play on the committed target `content_slug`; an unrelated play is not credited. Outcomes are marked inconclusive when the goal window is not fully observed or target attribution is missing. Goal-level rates are conditional and are not the primary causal estimate.\n\n')
        f.write('## Pilot caution\n\nThe supplied pilot headline compares served users with everyone else, which is selection-biased. It is not causal proof. This report compares all randomized agent and holdout users, including users who received no action.\n')
    # Regenerate the final report so it always reflects this exact replay and analyzer run.
    sim_report=json.load(open(os.path.join(a.out,'sim','simulator_report.json'),encoding='utf-8'))
    latency=json.load(open(os.path.join(a.out,'latency_report.json'),encoding='utf-8'))
    final=[
        '# One User, One Agent — replay report','',
        '## Implementation summary','',
        'The deterministic offline agent processes every trigger in delivery order, correlates login_success and app_open by session, keeps per-viewer goal/evidence state, records source conflicts, and publishes a dialect-specific home page only when safety checks pass. Holdout users receive no actions. Messaging is disabled in this baseline.','',
        '## Validation','',
        f"- Decisions: {sum(1 for _ in readj(os.path.join(a.out,'decisions.jsonl')))}",
        f"- Home-page actions accepted by simulator: {sim_report.get('counts',{}).get('home_page_accepted',0)}",
        f"- Simulator policy problems: {sim_report.get('policy_problem_count',0)}",
        f"- Automated tests: run `python -m unittest discover -s tests -v` (24 test cases)",
        f"- Processing latency: mean {latency.get('mean_processing_ms')} ms, p95 {latency.get('p95_processing_ms')} ms, max {latency.get('max_processing_ms')} ms; target under 60,000 ms.",'',
        '## Impact','',
        f"- Agent-arm play rate: {agent['rate']:.1%} ({agent['successes']}/{agent['n']})",
        f"- Holdout play rate: {hold['rate']:.1%} ({hold['successes']}/{hold['n']})",
        f"- Absolute difference: {diff*100:+.2f} percentage points (approximate 95% CI: [{diff_ci[0]*100:+.2f}, {diff_ci[1]*100:+.2f}] pp).",'',
        'This is descriptive for the supplied synthetic replay and simulator seed. It does not establish statistical significance or prove causal uplift. The randomized intention-to-treat comparison is preferred to the biased served-versus-rest pilot headline.','',
        '## Limitations','',
        'See `../KNOWN_LIMITATIONS.md`. The deterministic mock path avoids API credentials; no push/WhatsApp messages are sent; goal eligibility covers a supported subset; and outcomes without attributable evidence remain failed or inconclusive according to observed-window coverage.',''
    ]
    with open(os.path.join(a.out,'final_report.md'),'w',encoding='utf-8') as f:f.write('\n'.join(final))
    print(f"Analyzed {len(goal_rows)} goal commitments; ITT agent={agent['rate']} holdout={hold['rate']} diff={diff}")
if __name__=='__main__':main()
