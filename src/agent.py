#!/usr/bin/env python3
"""Deterministic, evidence-first OTT lifecycle agent for the One User, One Agent pack."""
from __future__ import annotations
import argparse, csv, json, os, time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))


def dt(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def iso(d):
    return d.astimezone(UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def read_jsonl(path):
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def read_csv(path):
    with open(path, encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(',', ':')) + '\n')


def truth(v):
    return str(v).lower() == 'true'


class Agent:
    def __init__(self, pack, out):
        self.pack = pack
        self.out = out
        self.profiles = {}

        for p in read_csv(os.path.join(pack, 'data/profiles_snapshot.csv')):
            old = self.profiles.get(p['user_id'])
            if old is None or p['updated_at'] > old['updated_at']:
                self.profiles[p['user_id']] = p

        self.catalogue = {}
        self.titles_by_dialect = defaultdict(list)
        for t in read_csv(os.path.join(pack, 'data/catalogue.csv')):
            t['premium'] = truth(t['is_premium'])
            t['genres'] = t['genres'].split('|') if t['genres'] else []
            t['_from'] = dt(t['available_from'])
            t['_until'] = dt(t['available_until']) if t['available_until'] else None
            self.catalogue[t['title_id']] = t
            self.titles_by_dialect[t['dialect']].append(t)

        self.episodes = defaultdict(list)
        for e in read_csv(os.path.join(pack, 'data/episodes.csv')):
            self.episodes[e['content_slug']].append(e)

        self.arms = {
            r['user_id']: r['arm']
            for r in read_csv(os.path.join(pack, 'replay/arms.csv'))
        }

        self.events_by_user = defaultdict(list)
        self.seen_event_ids = set()
        for e in read_jsonl(os.path.join(pack, 'data/events.jsonl')):
            self._add_event(e)

        self.active_goals = {}
        self.last_message_day = {}
        self.inapp_day = {}
        self.withdrawn = {}
        self.dependency_unavailable = {}
        self.store_failure = None
        self.session_events = defaultdict(set)
        self.control_audit = []

        with open(os.path.join(pack, 'config/interventions.json'), encoding='utf-8') as f:
            self.interventions = json.load(f)

        with open(os.path.join(pack, 'config/goals.json'), encoding='utf-8') as f:
            self.goal_rules = json.load(f)['goals']

        self.decisions = []
        self.actions = []
        self.outcomes = []
        self.latencies = []
        self.action_n = 0
        self.decision_n = 0
        self.handled_triggers = set()

    def _add_event(self, e):
        eid = e.get('event_id')
        if eid and eid in self.seen_event_ids:
            return False
        if eid:
            self.seen_event_ids.add(eid)
        self.events_by_user[e['user_id']].append(e)
        return True

    def _hist(self, u, at):
        # received_at is the trusted arrival clock. Never use phone occurred_at to order evidence.
        return [
            e for e in self.events_by_user.get(u, [])
            if dt(e.get('received_at')) and dt(e['received_at']) <= at
        ]

    def _eligible_goal(self, u, at, dialect):
        p = self.profiles.get(u, {})
        hist = self._hist(u, at)
        signup = dt(p.get('signup_at'))
        status = p.get('subscription_status', 'unknown')
        plays = [e for e in hist if e['event'] == 'play_started']
        completes = [e for e in hist if e['event'] == 'episode_completed']
        opens = [e for e in hist if e['event'] == 'app_open']
        paid = [
            e for e in hist
            if e['event'] == 'purchase_completed'
            and not truth(e.get('properties', {}).get('is_trial', False))
        ]

        # A trustworthy purchase received after the warehouse snapshot supersedes its stale status.
        snapshot_time = dt(p.get('updated_at') or p.get('snapshot_at'))
        recent_paid = [
            e for e in paid
            if snapshot_time is None or dt(e.get('received_at')) > snapshot_time
        ]
        if recent_paid:
            status = 'active'

        candidates = []

        def add(gid, reason, priority, target_content_slug=None):
            candidates.append((priority, gid, reason, target_content_slug))

        if signup and timedelta(0) <= at - signup <= timedelta(days=7) and not plays:
            add('G01', 'signup within 7 days and no prior play_started event', 100)

        if not completes:
            add('G02', 'no prior episode_completed event in received-event history', 80)

        if status == 'trial_active':
            add('G04', 'latest profile snapshot says trial_active', 65)

        if status == 'never' and not paid:
            add('G05', 'profile says never; no observed non-trial purchase event', 55)

        if status == 'lapsed':
            add('G06', 'latest profile snapshot says lapsed', 45)

        # G07: show/micro-drama played, has remaining episodes, no play for >=3 days.
        last_by_slug = {}
        for e in plays:
            t = self.catalogue.get(e.get('properties', {}).get('title_id'))
            if t and t['content_type'] in ('show', 'micro_drama'):
                when = dt(e.get('received_at'))
                if t['content_slug'] not in last_by_slug or when > last_by_slug[t['content_slug']][0]:
                    last_by_slug[t['content_slug']] = (when, t)

        completed_episode_ids = {
            e.get('properties', {}).get('episode_id')
            for e in hist
            if e['event'] == 'episode_completed'
            and e.get('properties', {}).get('episode_id')
        }

        for slug, (when, t) in last_by_slug.items():
            eps = sorted(
                self.episodes.get(slug, []),
                key=lambda ep: (int(ep.get('season') or 0), int(ep.get('episode') or 0))
            )
            played_episode_id = next((
                e.get('properties', {}).get('episode_id')
                for e in reversed(plays)
                if self.catalogue.get(
                    e.get('properties', {}).get('title_id'), {}
                ).get('content_slug') == slug
                and e.get('properties', {}).get('episode_id')
            ), None)

            played_ep = next(
                (ep for ep in eps if ep.get('episode_id') == played_episode_id),
                None
            )
            if not played_ep:
                # Without an episode-level position, we cannot prove there is an unwatched next episode.
                continue

            played_order = (
                int(played_ep.get('season') or 0),
                int(played_ep.get('episode') or 0)
            )
            has_unwatched = any(
                (int(ep.get('season') or 0), int(ep.get('episode') or 0)) > played_order
                and ep.get('episode_id') not in completed_episode_ids
                for ep in eps
            )

            if has_unwatched and at - when >= timedelta(days=3):
                # Persist the target series so outcome attribution cannot count an unrelated play.
                add(
                    'G07',
                    f"unfinished episodic title {slug}; last observed play {iso(when)}; "
                    f"remaining episode evidence={played_episode_id}",
                    75,
                    slug
                )
                break

        if status == 'active':
            recent = [
                e for e in plays
                if at - timedelta(days=14) <= dt(e['received_at']) <= at
            ]
            playdays = {
                dt(e['received_at']).astimezone(IST).date().isoformat()
                for e in recent
            }
            if len(playdays) <= 1:
                add(
                    'G09',
                    f'active subscriber; plays on {len(playdays)} distinct IST day(s) in last 14 days',
                    60
                )

            new_titles = [
                t for t in self.catalogue.values()
                if t['dialect'] == dialect
                and t['release_date']
                and (
                    at.date() - __import__('datetime').date.fromisoformat(t['release_date'])
                ).days in range(0, 31)
                and self._available(t, at)
            ]
            if new_titles:
                add(
                    'G08',
                    'active subscriber and at least one available title released within prior 30 days',
                    50
                )

        if not any(
            at - timedelta(days=14) <= dt(e['received_at']) <= at
            for e in opens
        ):
            add('G12', 'no app_open event received in prior 14 days', 40)

        # G03 remains eligible before and after the correction event. Existing open commitments
        # are re-judged under the corrected rule; new commitments use the currently active rule.
        if signup and timedelta(0) <= at - signup <= timedelta(days=2):
            g03 = next((g for g in self.goal_rules if g.get('goal_id') == 'G03'), {})
            rule_event = (g03.get('success') or {}).get('event', 'app_open')
            add(
                'G03',
                f'signup within 2 days; current G03 success rule requires {rule_event}',
                35
            )

        if not candidates:
            return None

        # Explicit priority order gives one goal and avoids vague G10 / unsupported G11.
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        _, gid, reason, target_slug = candidates[0]
        selected = {
            'goal_id': gid,
            'reason': reason,
            'selected_at': iso(at),
            'window_hours': next(
                (g['success']['window_hours'] for g in self.goal_rules if g['goal_id'] == gid),
                None
            )
        }
        if gid == 'G07' and target_slug:
            selected['target_content_slug'] = target_slug
        return selected

    def _apply_control(self, obj, when):
        kind = obj.get('type')
        if kind == 'title_withdrawn':
            self.withdrawn[obj['title_id']] = when
        elif kind == 'goal_rule_corrected':
            changed = False
            for g in self.goal_rules:
                if g['goal_id'] == obj['goal_id']:
                    g['success'] = obj['new_success']
                    changed = True
            self.control_audit.append({
                'at': iso(when),
                'type': kind,
                'goal_id': obj['goal_id'],
                'applied': changed,
                'new_success': obj.get('new_success')
            })
        elif kind in ('dependency_unavailable', 'dependency_degraded'):
            dep = obj.get('dependency', 'unknown')
            self.dependency_unavailable[dep] = dt(obj.get('until'))
            self.control_audit.append({
                'at': iso(when),
                'type': kind,
                'dependency': dep,
                'until': obj.get('until'),
                'detail': obj.get('detail')
            })

    def _dependency_failure(self, at):
        active = []
        for dep, until in self.dependency_unavailable.items():
            if until is None or at < until:
                active.append(dep)
        return active

    def _event_age(self, event):
        occurred_at = dt(event.get('occurred_at'))
        received_at = dt(event.get('received_at'))
        if occurred_at is None or received_at is None:
            return None, 'missing occurred_at or received_at'
        return round((received_at - occurred_at).total_seconds(), 3), None

    def _session_key(self, event):
        session_id = event.get('session_id')
        return (event.get('user_id'), session_id) if session_id else None

    def _record_session_event(self, event):
        key = self._session_key(event)
        if key:
            self.session_events[key].add(event.get('event'))
        return key

    def _update_session_decisions(self, key):
        if not key:
            return
        event_types = self.session_events[key]
        correlated = {'login_success', 'app_open'} <= event_types
        for decision in self.decisions:
            if (decision.get('user_id'), decision.get('session_id')) == key:
                decision['session_event_types'] = sorted(event_types)
                decision['session_correlated'] = correlated

    def _failure_decision(self, event, reason, error=None):
        self.decision_n += 1
        age, age_error = self._event_age(event)
        decision = {
            'decision_id': f'D-{self.decision_n:05d}',
            'user_id': event.get('user_id'),
            'trigger_event_id': event.get('event_id'),
            'session_id': event.get('session_id'),
            'trigger_received_at': event.get('received_at'),
            'deliver_at': event.get('deliver_at'),
            'event_age_s': age,
            'event_age_error': age_error,
            'current_dialect': event.get('app_dialect'),
            'session_event_types': sorted(
                self.session_events.get(self._session_key(event), set())
            ),
            'session_correlated': False,
            'goal_id': None,
            'decision': 'none',
            'reason': reason,
            'processing_ms': 0.1,
            'published_action_id': None,
            'checks': {'passed': [], 'failed': [reason]},
        }
        if error:
            decision['error_type'] = type(error).__name__
        self.decisions.append(decision)
        self.latencies.append(0.1)
        self.handled_triggers.add(event.get('event_id'))
        self._update_session_decisions(self._session_key(event))

    def _model_output_error(self, output, user_id=None, at=None, dialect=None):
        """Validate mock output without allowing it to become trusted evidence."""
        if output is None:
            return None
        if not isinstance(output, dict):
            return 'model output must be an object'

        allowed = {
            'goal_id', 'eligible', 'confidence', 'reason',
            'evidence_refs', 'catalogue_ids', 'dialect', 'availability'
        }
        if set(output) - allowed:
            return 'model output contains unexpected fields'
        required = {'goal_id', 'eligible', 'confidence', 'reason'}
        if set(output) & required != required:
            return 'model output is missing required fields'
        if not isinstance(output['goal_id'], str):
            return 'model goal_id must be a string'
        goal_ids = {g.get('goal_id') for g in self.goal_rules}
        if output['goal_id'] not in goal_ids:
            return 'model output contains an unknown goal_id'
        if not isinstance(output['eligible'], bool):
            return 'model eligible must be a boolean'
        if (
            isinstance(output['confidence'], bool)
            or not isinstance(output['confidence'], (int, float))
            or not 0 <= output['confidence'] <= 1
        ):
            return 'model confidence must be a number between 0 and 1'
        if not isinstance(output['reason'], str) or len(output['reason']) > 500:
            return 'model reason must be a string of at most 500 characters'

        if 'dialect' in output and (
            not isinstance(output['dialect'], str)
            or output['dialect'] not in self.titles_by_dialect
        ):
            return 'model output contains an unknown dialect'
        if (
            output.get('dialect')
            and dialect
            and output['dialect'] != dialect
        ):
            return 'model dialect claim conflicts with the trigger dialect'
        if 'availability' in output and not isinstance(output['availability'], bool):
            return 'model availability must be a boolean'
        if 'catalogue_ids' in output:
            ids = output['catalogue_ids']
            if (
                not isinstance(ids, list)
                or any(not isinstance(title_id, str) for title_id in ids)
                or len(ids) != len(set(ids))
            ):
                return 'model catalogue_ids must be a unique list of strings'
            for title_id in ids:
                title = self.catalogue.get(title_id)
                if not title:
                    return 'model output contains an unknown catalogue ID'
                if output.get('dialect') and title['dialect'] != output['dialect']:
                    return 'model catalogue dialect claim is unsupported'
                if at and not self._available(title, at):
                    return 'model output references an unavailable catalogue title'
            if output.get('availability') is False and ids:
                return 'model availability claim conflicts with catalogue data'
        elif 'availability' in output:
            return 'model availability claim has no catalogue reference'

        if 'evidence_refs' in output:
            refs = output['evidence_refs']
            if not isinstance(refs, list):
                return 'model evidence_refs must be a list'
            trusted = {
                e.get('event_id'): e
                for e in self._hist(user_id, at)
                if user_id and at and e.get('event_id')
            }
            for ref in refs:
                if not isinstance(ref, dict) or set(ref) != {
                    'event_id', 'event', 'received_at'
                }:
                    return 'model evidence reference has an invalid shape'
                if any(not isinstance(ref[field], str) for field in (
                    'event_id', 'event', 'received_at'
                )):
                    return 'model evidence reference has invalid field types'
                source = trusted.get(ref['event_id'])
                if (
                    not source
                    or source.get('event') != ref['event']
                    or source.get('received_at') != ref['received_at']
                ):
                    return 'model evidence claim is not supported by trusted events'

        if output['eligible'] and user_id and at:
            trusted_goal = self._eligible_goal(
                user_id, at, dialect or output.get('dialect')
            )
            if not trusted_goal or trusted_goal['goal_id'] != output['goal_id']:
                return 'model eligibility claim is not supported by deterministic evidence'
        return None

    def _validate_model_output(self, output, user_id=None, at=None, dialect=None):
        """Validate output with trusted replay context; never use this as a schema-only check."""
        if user_id is None or at is None or dialect is None:
            return False
        # The deterministic rules engine is authoritative; model output is never action input.
        return self._model_output_error(output, user_id, at, dialect) is None

    def _available(self, t, at):
        return (
            t['_from'] is not None
            and t['_from'] <= at
            and (t['_until'] is None or at < t['_until'])
            and t['title_id'] not in self.withdrawn
        )

    def _page(self, u, at, dialect):
        # Candidate titles are sourced only from the pack, filtered by current trigger dialect and availability.
        pool = [
            t for t in self.titles_by_dialect.get(dialect, [])
            if self._available(t, at)
        ]
        if not pool:
            return None

        hist = self._hist(u, at)
        genre_count = defaultdict(int)
        for e in hist:
            if e['event'] == 'play_started':
                t = self.catalogue.get(e.get('properties', {}).get('title_id'))
                if t:
                    for g in t['genres']:
                        genre_count[g] += 1

        pool.sort(key=lambda t: (
            -sum(genre_count[g] for g in t['genres']),
            t['title_id']
        ))

        seen = set()
        hero = None
        for t in pool:
            if t['content_slug'] not in seen:
                hero = t
                seen.add(t['content_slug'])
                break
        if not hero:
            return None

        rows = []
        for rowtype in [
            'because_you_watched', 'popular_in_dialect',
            'new_releases', 'genre', 'next_episode'
        ]:
            selected = []
            for t in pool:
                if t['content_slug'] in seen:
                    continue
                if rowtype == 'new_releases' and not (
                    t['release_date']
                    and 0 <= (
                        at.date() - __import__('datetime').date.fromisoformat(t['release_date'])
                    ).days <= 30
                ):
                    continue
                if rowtype == 'genre' and not t['genres']:
                    continue
                selected.append(t)
                seen.add(t['content_slug'])
                if len(selected) == 6:
                    break

            if len(selected) >= 3:
                rows.append({'row_type': rowtype, 'title_ids': [t['title_id'] for t in selected]})
            if len(rows) >= 6:
                break

        return {'dialect': dialect, 'hero_title_id': hero['title_id'], 'rows': rows[:6]}

    def _verify_page(self, page, at, dialect, user_id=None, goal_id=None):
        # Independent pre-publication gate: do not trust the builder's output merely because it returned a page.
        passed = []
        failed = []

        if page.get('dialect') == dialect:
            passed.append('current_dialect')
        else:
            failed.append('wrong_page_dialect')

        hero_id = page.get('hero_title_id')
        all_ids = [hero_id] if hero_id else []
        rows = page.get('rows') or []

        if hero_id in self.catalogue:
            passed.append('hero_exists')
        else:
            failed.append('hero_missing_or_unknown')

        if len(rows) <= 6:
            passed.append('row_count')
        else:
            failed.append('too_many_rows')

        allowed = set(self.interventions.get('home_page', {}).get('rows', {}).get('row_types', []))
        seen_slugs = set()

        for title_id in all_ids:
            title = self.catalogue.get(title_id)
            if not title:
                continue
            if title['dialect'] != dialect:
                failed.append('wrong_dialect_title:' + title_id)
            if not self._available(title, at):
                failed.append('unavailable_title:' + title_id)
            if title['content_slug'] in seen_slugs:
                failed.append('duplicate_content_slug:' + title_id)
            seen_slugs.add(title['content_slug'])

        for row in rows:
            row_type = row.get('row_type')
            ids = row.get('title_ids') or []
            if row_type not in allowed:
                failed.append('invalid_row_type:' + str(row_type))
            if not 3 <= len(ids) <= 10:
                failed.append('invalid_row_size:' + str(row_type))

            for title_id in ids:
                all_ids.append(title_id)
                title = self.catalogue.get(title_id)
                if not title:
                    failed.append('unknown_title:' + str(title_id))
                    continue
                if title['dialect'] != dialect:
                    failed.append('wrong_dialect_title:' + title_id)
                if not self._available(title, at):
                    failed.append('unavailable_title:' + title_id)
                if title['content_slug'] in seen_slugs:
                    failed.append('duplicate_content_slug:' + title_id)
                seen_slugs.add(title['content_slug'])

        if not failed:
            passed.extend(['title_availability', 'content_slug_dedup', 'row_constraints'])

        if user_id and self.arms.get(user_id) == 'holdout':
            failed.append('holdout_action_forbidden')
        elif user_id:
            passed.append('holdout_check')

        if goal_id:
            rule = next((g for g in self.goal_rules if g.get('goal_id') == goal_id), None)
            if rule and (rule.get('success') or {}).get('event'):
                passed.append('measurable_goal_rule')
            else:
                failed.append('goal_not_measurable')

        return {'passed': passed, 'failed': failed}

    def _no_action(self, reason):
        return {'type': 'none', 'reason': reason}

    def _verify_message(self, user_id, channel, at, template_id=None):
        """Independent safety gate for push and WhatsApp messages."""
        passed = []
        failed = []
        profile = self.profiles.get(user_id, {})

        if channel not in ("push", "whatsapp"):
            return {"passed": passed, "failed": ["unsupported_message_channel"]}

        # Holdout users must never receive an agent action.
        if self.arms.get(user_id) == "holdout":
            failed.append("holdout_action_forbidden")
        else:
            passed.append("holdout_check")

        # Channel-specific, explicit consent.
        consent_field = (
            "notifications_opt_in" if channel == "push"
            else "whatsapp_opt_in"
        )
        if truth(profile.get(consent_field, "false")):
            passed.append("channel_consent")
        else:
            failed.append("missing_channel_consent")

        # A notification opt-out event overrides the profile snapshot.
        history = self._hist(user_id, at)
        if channel == "push" and any(
            e.get("event") == "notification_opt_out" for e in history
        ):
            failed.append("notification_opt_out_event")
        elif channel == "push":
            passed.append("opt_out_event_check")

        # Quiet hours: 22:00 through 07:59 IST.
        local_time = at.astimezone(IST).time()
        if local_time.hour >= 22 or local_time.hour < 8:
            failed.append("quiet_hours_ist")
        else:
            passed.append("quiet_hours_check")

        # Shared daily cap across push and WhatsApp.
        local_day = at.astimezone(IST).date()
        prior_message_today = any(
            action.get("user_id") == user_id
            and action.get("type") in ("push", "whatsapp")
            and dt(action.get("published_at") or action.get("sent_at"))
            and dt(action.get("published_at") or action.get("sent_at")).astimezone(IST).date() == local_day
            for action in self.actions
        )
        prior_delivery_today = any(
            e.get("event") == "message_delivered"
            and dt(e.get("received_at"))
            and dt(e["received_at"]).astimezone(IST).date() == local_day
            for e in history
        )

        if prior_message_today or prior_delivery_today:
            failed.append("daily_message_cap_reached")
        else:
            passed.append("daily_message_cap_check")

        # WhatsApp must use an approved configured template.
        if channel == "whatsapp":
            approved = {
                item.get("template_id")
                for item in self.interventions.get("whatsapp", {}).get('approved_templates', [])
            }
            if template_id in approved:
                passed.append("approved_whatsapp_template")
            else:
                failed.append("unapproved_whatsapp_template")

        return {"passed": passed, "failed": failed}

    def _handle_trigger(self, e):
        start = time.perf_counter()
        at = dt(e.get('deliver_at') or e.get('received_at'))
        if at is None:
            self._failure_decision(e, 'missing deliver_at/received_at')
            return
        u = e['user_id']
        dialect = e.get('app_dialect')
        session_key = self._record_session_event(e)
        self.decision_n += 1
        did = f'D-{self.decision_n:05d}'
        p = self.profiles.get(u)
        profile_dialect = (p or {}).get('preferred_dialect')
        dialect_conflicts = []

        if dialect and profile_dialect and dialect != profile_dialect:
            dialect_conflicts.append({
                'fact': 'dialect',
                'chosen': dialect,
                'chosen_source': 'trigger.app_dialect',
                'conflicting_value': profile_dialect,
                'conflicting_source': 'profile.preferred_dialect'
            })

        fact_conflicts = []
        if p:
            snapshot_time = dt(p.get('updated_at') or p.get('snapshot_at'))
            for hist_event in self._hist(u, at):
                props = hist_event.get('properties') or {}
                is_paid_purchase = (
                    hist_event.get('event') == 'purchase_completed'
                    and (
                        props.get('is_trial') is False
                        or str(props.get('is_trial')).lower() == 'false'
                    )
                )
                received = dt(hist_event.get('received_at'))
                if is_paid_purchase and received and (
                    snapshot_time is None or received > snapshot_time
                ):
                    fact_conflicts.append({
                        'fact': 'subscription_status',
                        'chosen': 'active',
                        'chosen_source': 'purchase_completed event',
                        'event_id': hist_event.get('event_id'),
                        'received_at': hist_event.get('received_at'),
                        'conflicting_value': p.get('subscription_status'),
                        'conflicting_source': 'profile_snapshot',
                        'profile_updated_at': p.get('updated_at')
                    })

        event_age_s, event_age_error = self._event_age(e)

        shadow_goal = None
        failed_dependencies = self._dependency_failure(at)
        failure_reason = e.get('_failure_reason')

        if e.get('event_id') in self.handled_triggers:
            choice = self._no_action('duplicate trigger event_id')
        elif self.store_failure:
            choice = self._no_action(
                f'data-store failure: {self.store_failure}; fail-closed fallback'
            )
        elif failure_reason:
            choice = self._no_action(f'{failure_reason}; deterministic fallback fails closed')
        elif failed_dependencies:
            choice = self._no_action(
                'dependency unavailable/degraded: ' + ', '.join(failed_dependencies)
                + '; fail-closed fallback'
            )
        elif e.get('event') == 'login_success':
            choice = self._no_action(
                'login_success recorded; login cannot publish a foreground action'
            )
        elif (
            e.get('event') == 'app_open'
            and event_age_s is not None
            and event_age_s >= 72 * 60 * 60
        ):
            choice = self._no_action(
                f'stale buffered app_open: event age {event_age_s} seconds; '
                'no foreground action'
            )
        elif self.arms.get(u) == 'holdout':
            # Shadow-assign an eligible goal for evaluation while still publishing no intervention.
            if p and dialect:
                shadow_goal = self._eligible_goal(u, at, dialect)
            choice = self._no_action('randomized holdout: no agent actions permitted')
        elif not p:
            choice = self._no_action('no profile snapshot; insufficient evidence for safe goal eligibility')
        elif not dialect:
            choice = self._no_action('trigger has no current app_dialect')
        else:
            goal = self.active_goals.get(u)
            if goal and at < dt(goal['selected_at']) + timedelta(hours=goal['window_hours'] or 0):
                choice = self._no_action(f"existing active goal {goal['goal_id']} is still open")
            else:
                goal = self._eligible_goal(u, at, dialect)
                if not goal:
                    choice = self._no_action('no eligible measurable goal supported by current evidence')
                else:
                    self.active_goals[u] = goal
                    page = self._page(u, at, dialect)
                    if not page:
                        choice = self._no_action('no available catalogue title in current app dialect')
                    else:
                        verification = self._verify_page(
                            page, at, dialect, user_id=u, goal_id=goal['goal_id']
                        )
                        if verification['failed']:
                            choice = self._no_action(
                                'independent verification failed: '
                                + ', '.join(verification['failed'])
                            )
                            choice['checks'] = verification
                        else:
                            choice = {
                                'type': 'home_page',
                                'goal_id': goal['goal_id'],
                                'goal_reason': goal['reason'],
                                'checks': verification,
                                **page
                            }

        if choice['type'] == 'home_page':
            self.action_n += 1
            action = {
                'action_id': f'A-{self.action_n:05d}',
                'decision_id': did,
                'user_id': u,
                'trigger_event_id': e['event_id'],
                'type': 'home_page',
                'published_at': iso(at + timedelta(milliseconds=1)),
                **{k: choice[k] for k in ('dialect', 'hero_title_id', 'rows')}
            }
            self.actions.append(action)

        elapsed_ms = (time.perf_counter() - start) * 1000
        self.latencies.append(elapsed_ms)
        hist = self._hist(u, at)

        # Store compact evidence references, not a copied full personal history.
        evidence_ids = [x.get('event_id') for x in hist[-8:] if x.get('event_id')]
        selected_goal = self.active_goals.get(u) or shadow_goal
        evidence_refs = [
            {
                'event_id': x.get('event_id'),
                'event': x.get('event'),
                'source': 'event_stream',
                'received_at': x.get('received_at')
            }
            for x in hist[-8:] if x.get('event_id')
        ]

        # Keep no-action explanations specific. Holdout is intentional, not a safety failure.
        default_checks = {
            'passed': ['holdout_check'] if self.arms.get(u) == 'holdout' else [],
            'failed': [] if self.arms.get(u) == 'holdout'
            else [choice.get('reason', 'unspecified reason')]
        }

        self.decisions.append({
            'decision_id': did,
            'user_id': u,
            'trigger_event_id': e['event_id'],
            'session_id': e.get('session_id'),
            'trigger_received_at': e.get('received_at'),
            'deliver_at': e['deliver_at'],
            'event_age_s': event_age_s,
            'event_age_error': event_age_error,
            'current_dialect': dialect,
            'dialect_source': 'trigger.app_dialect' if dialect else None,
            'dialect_conflicts': dialect_conflicts,
            'fact_conflicts': fact_conflicts,
            'profile_snapshot_at': p.get('snapshot_at') if p else None,
            'profile_updated_at': p.get('updated_at') if p else None,
            'evidence_event_ids': evidence_ids,
            'evidence': evidence_refs,
            'goal_id': choice.get('goal_id') or (
                selected_goal.get('goal_id') if selected_goal else None
            ),
            'goal_reason': choice.get('goal_reason') or (
                selected_goal.get('reason') if selected_goal else None
            ),
            'goal_target_content_slug': (selected_goal or {}).get('target_content_slug'),
            'goal_deadline': iso(
                at + timedelta(hours=(selected_goal or {}).get('window_hours') or 0)
            ) if (selected_goal or {}).get('window_hours') else None,
            'confidence': 0.8 if choice['type'] == 'home_page' else 0.0,
            'confidence_reason': (
                'deterministic eligibility and catalogue checks passed'
                if choice['type'] == 'home_page'
                else f"no action taken: {choice.get('reason', 'unspecified reason')}"
            ),
            'assumptions': [
                'trigger app_dialect is the current client setting',
                'received_at/deliver_at are trusted server timestamps'
            ],
            'checks': choice.get('checks', default_checks),
            'decision': choice['type'],
            'reason': choice.get(
                'reason',
                'eligible goal selected; personalized page built from currently available titles'
            ),
            'processing_ms': round(elapsed_ms, 3),
            'published_action_id': (
                self.actions[-1]['action_id'] if choice['type'] == 'home_page' else None
            )
        })

        self.handled_triggers.add(e['event_id'])
        self._update_session_decisions(session_key)

    def handle_trigger(self, event):
        try:
            self._handle_trigger(event)
        except Exception as error:
            self._record_session_event(event)
            self._failure_decision(
                event,
                f'agent dependency failure: {error}; fail-closed fallback',
                error,
            )

    def run(self):
        os.makedirs(self.out, exist_ok=True)
        triggers = list(read_jsonl(os.path.join(self.pack, 'replay/triggers.jsonl')))
        live = list(read_jsonl(os.path.join(self.pack, 'replay/live_events.jsonl')))
        controls = list(read_jsonl(os.path.join(self.pack, 'replay/control_events.jsonl')))
        faults = {
            r['trigger_event_id']: r
            for r in read_jsonl(os.path.join(self.pack, 'replay/model_faults.jsonl'))
        }

        timeline = []
        for e in triggers:
            timeline.append((dt(e['deliver_at']), 0, 'trigger', e))
        for e in live:
            timeline.append((dt(e.get('deliver_at') or e.get('received_at')), 1, 'event', e))
        for c in controls:
            timeline.append((dt(c['at']), -1, 'control', c))
        timeline.sort(key=lambda x: (x[0], x[1]))

        # Control changes apply at their recorded time; every trigger gets one decision.
        for when, _, kind, obj in timeline:
            if kind == 'control':
                self._apply_control(obj, when)
                continue

            if kind == 'event':
                try:
                    self._add_event(obj)
                except Exception as error:
                    self.store_failure = f'{type(error).__name__}: {error}'
                if obj.get('event') == 'notification_opt_out':
                    # State remains event-sourced; _eligible_goal reads the history. Messaging is disabled by default.
                    pass
                continue

            failed_dependencies = self._dependency_failure(when)
            fault = faults.get(obj['event_id'])
            trigger = dict(obj)
            if failed_dependencies:
                trigger['_failure_reason'] = (
                    'dependency unavailable/degraded: '
                    + ', '.join(failed_dependencies)
                )
            elif fault:
                trigger['_failure_reason'] = f"model fault '{fault['fault']}'"
                if 'model_output' in fault:
                    output_error = self._model_output_error(
                        fault['model_output'],
                        obj.get('user_id'),
                        dt(obj.get('deliver_at')),
                        obj.get('app_dialect'),
                    )
                    if output_error:
                        trigger['_failure_reason'] = output_error
            self.handle_trigger(trigger)

        write_jsonl(os.path.join(self.out, 'decisions.jsonl'), self.decisions)
        write_jsonl(os.path.join(self.out, 'actions.jsonl'), self.actions)

        # Goal commitments are written here; analyze_outcomes.py adds success attribution after simulation.
        lat = sorted(self.latencies)
        p95 = lat[min(len(lat) - 1, int(.95 * (len(lat) - 1)))] if lat else 0
        with open(os.path.join(self.out, 'latency_report.json'), 'w') as f:
            json.dump({
                'decisions': len(lat),
                'mean_processing_ms': round(sum(lat) / len(lat), 3) if lat else 0,
                'p95_processing_ms': round(p95, 3),
                'max_processing_ms': round(max(lat) if lat else 0, 3),
                'target_ms': 60000,
                'within_target': bool(lat and max(lat) < 60000)
            }, f, indent=2)

        self._write_outcomes()
        self._write_viewer_state()
        with open(os.path.join(self.out, 'control_audit.json'), 'w', encoding='utf-8') as f:
            json.dump(self.control_audit, f, ensure_ascii=False, indent=2)
        self._impact_report()
        print(f"Replay complete: {len(self.decisions)} decisions, {len(self.actions)} actions; outputs: {self.out}")

    def _write_outcomes(self):
        first = {}
        for d in self.decisions:
            if d.get('goal_id') and d['user_id'] not in first:
                goal = next((g for g in self.goal_rules if g['goal_id'] == d['goal_id']), {})
                first[d['user_id']] = {
                    'user_id': d['user_id'],
                    'arm': self.arms.get(d['user_id'], 'unknown'),
                    'goal_id': d['goal_id'],
                    'goal_started_at': d['deliver_at'],
                    'window_hours': (goal.get('success') or {}).get('window_hours', 0),
                    'trigger_event_id': d['trigger_event_id'],
                    'decision': d['decision'],
                    'target_content_slug': d.get('goal_target_content_slug'),
                    'success': None,
                    'status': 'pending_simulation'
                }

        self.outcomes = list(first.values())
        write_jsonl(os.path.join(self.out, 'outcomes.jsonl'), self.outcomes)

    def _write_viewer_state(self):
        states = {}
        for d in self.decisions:
            u = d['user_id']
            state = states.setdefault(u, {
                'user_id': u,
                'arm': self.arms.get(u, 'unknown'),
                'last_trigger_at': None,
                'current_dialect': None,
                'active_goal': None,
                'last_decision': None,
                'last_reason': None,
                'recent_evidence_event_ids': []
            })
            state.update({
                'last_trigger_at': d.get('deliver_at'),
                'current_dialect': d.get('current_dialect'),
                'last_decision': d.get('decision'),
                'last_reason': d.get('reason'),
                'recent_evidence_event_ids': d.get('evidence_event_ids', [])
            })
            if d.get('goal_id') and state['active_goal'] is None:
                state['active_goal'] = {
                    'goal_id': d['goal_id'],
                    'started_at': d.get('deliver_at'),
                    'reason': d.get('goal_reason')
                }

        path = os.path.join(self.out, 'viewer_state')
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, 'state.json'), 'w', encoding='utf-8') as f:
            json.dump(states, f, ensure_ascii=False, indent=2)

    def _impact_report(self):
        rows = read_csv(os.path.join(self.pack, 'pilot/pilot_assignment.csv'))
        outcomes = {
            r['user_id']: r
            for r in read_csv(os.path.join(self.pack, 'pilot/pilot_outcomes.csv'))
        }
        arms = defaultdict(list)
        for r in rows:
            arm = r.get('arm', '').lower()
            outcome = outcomes.get(r['user_id'])
            if arm in ('treatment', 'agent', 'holdout', 'control') and outcome:
                arms[arm].append(outcome)

        # Prefer randomized arms; report rates from raw rows and flag the served-vs-rest headline as biased.
        def rate(records):
            if not records:
                return None
            vals = []
            for r in records:
                v = next((
                    r[k] for k in ('goal_succeeded', 'success', 'succeeded', 'outcome')
                    if k in r
                ), None)
                vals.append(str(v).lower() in ('true', '1', 'yes', 'success', 'succeeded'))
            return sum(vals) / len(vals) if vals else None

        text = [
            '# Impact report', '',
            '## Pilot interpretation', '',
            'The supplied headline compares users served with everyone else, which is not a randomized intention-to-treat comparison and can be biased by eligibility and engagement. Do not use the 2.3x headline as causal proof.', '',
            'The replay is assigned by `replay/arms.csv`. The correct primary estimate is intention-to-treat: compare outcomes for every `agent`-arm user against every `holdout` user, regardless of whether the agent acted. Holdout must receive zero actions.', '',
            '## Current replay outputs', '',
            f"- Decisions: {len(self.decisions)}",
            f"- Published home-page actions: {len(self.actions)}",
            f"- No-action decisions: {sum(1 for d in self.decisions if d['decision'] == 'none')}", '',
            '## Measurement caveat', '',
            'Run the supplied simulator against `actions.jsonl`, then compute goal outcomes from the resulting `outcome_events.jsonl` for the goal and its committed window. Compare agent vs holdout by randomized assignment (intention-to-treat), report sample sizes and absolute percentage-point difference, and avoid conditioning on being served. This initial report does not claim causal uplift before simulation.'
        ]
        with open(os.path.join(self.out, 'impact_report.md'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(text) + '\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pack', default='one_user_one_agent_pack')
    ap.add_argument('--out', default='sample_run')
    a = ap.parse_args()
    Agent(a.pack, a.out).run()


if __name__ == '__main__':
    main()