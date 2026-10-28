"""Toolkit-independent reservation editor; dated orders and repeat templates stay separate."""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, date, timedelta
import uuid
from zoneinfo import ZoneInfo

from .config import BookingContext, Goal, RepeatRule, Target, RushSettings, config_from_dict, parse_start
from .repeat import copy_goals


# ==========================================
# Function: Own editable workspace state independently from GTK and browser workers.
# Method: Preserve schema/journal identities and validate complete mutations before applying them.
# ==========================================
class Workspace:
    # ==========================================
    # Function: Load an independent editor snapshot and per-goal draft selections.
    # Method: Deep-copy persisted configuration and use stable prefixed template identities.
    # ==========================================
    def __init__(self, config):
        self.data = asdict(config)
        self.selected = None
        self.states, self.dates, self.drafts = {}, {}, {}
        self.dirty = False
        self.baseline = deepcopy(self.data)
        self.replacements = {}
        if self.items():
            self.selected = self.items()[0][0]

    # ==========================================
    # Function: List independent dated goals and weekly templates in sidebar order.
    # Method: Keep generated occurrences owned by their template and out of independent execution controls.
    # ==========================================
    def items(self):
        return [(g['id'], g) for g in self.data['goals'] if not g.get('repeat_id')] + [('rule:'+r['id'], r) for r in self.data['repeat_rules']]

    # ==========================================
    # Function: Resolve a stable workspace identity.
    # Method: Resolve persisted collections directly so hidden occurrences remain available for history deletion.
    # ==========================================
    def get(self, identity=None):
        identity = identity or self.selected
        values = self.data['repeat_rules'] if self.repeating(identity) else self.data['goals']
        return next((item for item in values if item['id'] == (identity[5:] if self.repeating(identity) else identity)), None)

    # ==========================================
    # Function: List frozen executions belonging to a recurring template.
    # Method: Preserve their original requirements and show the newest booking dates first.
    # ==========================================
    def occurrences(self, identity=None):
        identity = identity or self.selected
        if not self.repeating(identity):
            return []
        return sorted((g for g in self.data['goals'] if g.get('repeat_id') == identity[5:]),
                      key=lambda g: (g['candidates'][0]['date'], g['id']), reverse=True)

    # ==========================================
    # Function: Distinguish templates from immutable generated date occurrences.
    # Method: Interpret only the sidebar identity prefix as repeat editing mode.
    # ==========================================
    def repeating(self, identity=None):
        return bool((identity or self.selected or '').startswith('rule:'))

    # ==========================================
    # Function: Read the workspace's own current local date.
    # Method: Respect the persisted scheduling timezone rather than the host locale.
    # ==========================================
    def today(self):
        return datetime.now(ZoneInfo(self.data['timezone'])).date()

    # ==========================================
    # Function: Protect dated order requirements from accidental editing.
    # Method: Freeze generated occurrences and recorded one-time goals while leaving templates editable when idle.
    # ==========================================
    def editable(self, identity=None):
        identity = identity or self.selected
        if not self.get(identity):
            raise ValueError('请先选择或新建一个预约目标')
        if self.get(identity).get('repeat_id'):
            raise ValueError('重复执行目标由模板管理，请修改重复模板')
        if not self.repeating(identity) and self.states.get(identity, {}).get('status', 'pending') not in {'pending', 'preview'}:
            raise ValueError('此目标已有执行记录，请使用 + 创建下一天目标')

    # ==========================================
    # Function: Return a dated goal's date or the next expected weekly booking date.
    # Method: Exclude already materialized execution days and apply the rule's day offset.
    # ==========================================
    def day(self, identity=None):
        identity = identity or self.selected
        item = self.get(identity)
        today = self.today()
        if item and self.repeating(identity):
            for offset in range(7*(len(item.get('skip_dates',[]))+1)+1):
                day = today+timedelta(days=offset)
                if day.weekday() in item['weekdays'] and day.isoformat() not in item.get('skip_dates',[]) and not any(g['id'] == f"daily-{item['id']}-{day.isoformat()}" for g in self.data['goals']):
                    return (day+timedelta(days=item['days_ahead'])).isoformat()
        if item and item['candidates']:
            return item['candidates'][0]['date']
        return self.dates.get(identity, (today+timedelta(days=1)).isoformat())

    # ==========================================
    # Function: Add a local incomplete goal without starting any account operation.
    # Method: Allocate an independent identity and defer complete-plan validation until saving.
    # ==========================================
    def add(self):
        if len(self.data['goals']) >= 100:
            raise ValueError('最多支持 100 个日期目标')
        item = {'id': 'goal-'+uuid.uuid4().hex[:16], 'name': '新预约目标', 'candidates': [], 'origin': None, 'repeat_id': None}
        self.data['goals'].append(item)
        self.selected, self.dirty = item['id'], True
        return item

    # ==========================================
    # Function: Apply editable name and one-time date changes.
    # Method: Keep invalid date text as a local draft until candidates or save require validation.
    # ==========================================
    def metadata(self, name, day):
        self.editable()
        item = self.get()
        changed = item['name'] != name
        item['name'] = name
        if not self.repeating():
            changed = changed or self.day() != day
            self.dates[self.selected] = day
            for candidate in item['candidates']:
                candidate['date'] = day
        self.dirty = self.dirty or changed

    # ==========================================
    # Function: Copy the clicked goal into an independent next-day goal.
    # Method: Deep-copy ranked alternatives, freeze inherited routes, and never carry repeat/order state.
    # ==========================================
    def copy_next(self, identity):
        source = self.get(identity)
        if not source or not source['candidates']:
            raise ValueError('请先为目标添加场馆和时段')
        day = (date.fromisoformat(self.day(identity))+timedelta(days=1)).isoformat()
        if day < self.today().isoformat():
            raise ValueError('下一天仍是过去日期，请新建目标并选择未来日期')
        if len(self.data['goals']) >= 100:
            raise ValueError('最多支持 100 个日期目标')
        item = copy_goals(self.copy_source(identity), [day])[0]
        self.data['goals'].append(item)
        self.selected, self.dirty = item['id'], True
        return item

    # ==========================================
    # Function: Resolve a template's next dated requirements before copying or converting it.
    # Method: Preserve rush lead/cadence and translate execution-day HH:MM:SS into a concrete ISO time.
    # ==========================================
    def copy_source(self, identity=None):
        identity = identity or self.selected
        source = self.get(identity)
        result = {key: deepcopy(source[key]) for key in ('id', 'name', 'candidates')}
        result['origin'] = None
        result['rush'] = deepcopy(source.get('rush', asdict(RushSettings())))
        if self.repeating(identity):
            day = self.day(identity)
            for candidate in result['candidates']:
                candidate['date'] = day
            if result['rush']['at']:
                execution = date.fromisoformat(day) - timedelta(days=source['days_ahead'])
                result['rush']['at'] = execution.isoformat() + 'T' + result['rush']['at']
        return result

    # ==========================================
    # Function: Remove a goal regardless of recorded execution status while leaving order history independent.
    # Method: Clear local drafts, dated occurrences of a deleted template and their transient editor state.
    # ==========================================
    def remove(self, identity):
        item = self.get(identity)
        if item is None:
            raise ValueError('目标不存在')
        removed = {identity, *self.replacements.pop(identity, set())}
        rules={value[5:] for value in removed if value.startswith('rule:')}
        removed.update(g['id'] for g in self.data['goals'] if g.get('repeat_id') in rules)
        self.data['goals'] = [g for g in self.data['goals'] if g['id'] not in removed]
        self.data['repeat_rules'] = [r for r in self.data['repeat_rules'] if 'rule:'+r['id'] not in removed]
        for key in removed:
            for store in (self.drafts,self.dates,self.states,self.replacements):
                store.pop(key,None)
        if self.selected in removed:
            self.selected = self.items()[0][0] if self.items() else None
        self.dirty = True
        return removed

    # ==========================================
    # Function: Preserve explicit priority when reordering goals or candidates.
    # Method: Swap adjacent visible goals without promoting hidden generated executions into sidebar order.
    # ==========================================
    def move(self, delta, candidate=None):
        if not self.get():
            return
        if candidate is None:
            values = self.data['repeat_rules' if self.repeating() else 'goals']
            positions=[i for i,item in enumerate(values) if self.repeating() or not item.get('repeat_id')]
            index = positions.index(values.index(self.get()))
            other=index+delta
            if 0 <= other < len(positions):
                first,second=positions[index],positions[other]
                values[first],values[second]=values[second],values[first]
                self.dirty=True
                return other
            return index
        else:
            self.editable()
            values, index = self.get()['candidates'], candidate
        other = index+delta
        if 0 <= other < len(values):
            values[index], values[other] = values[other], values[index]
            self.dirty = True
            return other
        return index

    # ==========================================
    # Function: Convert one-time and recurring modes without scheduling an extra source booking.
    # Method: Validate the new template or dated copy before replacing the editable source.
    # ==========================================
    def toggle_repeat(self):
        self.editable()
        old_id, source = self.selected, self.get()
        if not source['candidates']:
            raise ValueError('请先为目标添加场馆和时段，再开启重复预约')
        if self.repeating():
            if len(self.data['goals']) >= 100:
                raise ValueError('最多支持 100 个日期目标')
            day = self.day()
            item = copy_goals(self.copy_source(), [day])[0]
            self.data['repeat_rules'].remove(source)
            self.data['goals'].append(item)
            self.selected = item['id']
        else:
            if len(self.data['repeat_rules']) >= 10:
                raise ValueError('最多支持 10 个重复目标')
            candidates = deepcopy(source['candidates'])
            for candidate in candidates:
                candidate['context'] = candidate.get('context') or asdict(BookingContext(self.data['booking_url'], self.data['venue'], self.data['sport']))
            rush = deepcopy(source.get('rush', asdict(RushSettings())))
            if rush['at']:
                rush['at'] = parse_start(rush['at'], self.data['timezone']).astimezone(ZoneInfo(self.data['timezone'])).strftime('%H:%M:%S')
            rule = RepeatRule('rule-'+uuid.uuid4().hex[:16], source['name'],
                              config_from_dict(self.data | {'goals': [source]}).goals[0].candidates, rush=RushSettings(**rush))
            item = asdict(rule) | {'candidates': candidates}
            self.data['repeat_rules'].append(item)
            self.data['goals'].remove(source)
            self.selected = 'rule:'+item['id']
        self.drafts[self.selected] = self.drafts.pop(old_id, [])
        self.replacements[self.selected] = self.replacements.pop(old_id,set()) | {old_id}
        self.dirty = True
        return item

    # ==========================================
    # Function: Validate and store one goal's independent timed-release controls.
    # Method: Apply a strict RushSettings value without changing candidates, order keys or other goals.
    # ==========================================
    def rush(self, value):
        self.editable()
        parsed = RushSettings(**value).validate(repeating=self.repeating())
        item = self.get()
        normalized = asdict(parsed)
        if item.get('rush') != normalized:
            item['rush'] = normalized
            self.dirty = True

    # ==========================================
    # Function: Set a template's own weekday, trigger, offset and pause state.
    # Method: Revalidate the complete RepeatRule before changing the stored draft.
    # ==========================================
    def schedule(self, weekdays, at, days_ahead, enabled):
        self.editable()
        if not self.repeating():
            return
        item = self.get()
        proposed = item | {'weekdays': weekdays, 'at': at, 'days_ahead': days_ahead, 'enabled': enabled}
        parsed = config_from_dict(self.data | {'goals': [], 'repeat_rules': [proposed]}).repeat_rules[0]
        changed = item != asdict(parsed)
        item.update(asdict(parsed))
        self.dirty = self.dirty or changed

    # ==========================================
    # Function: Stage a selected venue/project and its own court choices.
    # Method: Update one context in place while preserving all other draft priorities.
    # ==========================================
    def stage(self, context, courts):
        self.editable()
        if not courts:
            raise ValueError('请至少选择一个具体场地')
        values = self.drafts.setdefault(self.selected, [])
        existing = next((v for v in values if v['context'] == context), None)
        if existing:
            existing['courts'] = list(courts)
        else:
            values.append({'context': context, 'courts': list(courts)})

    # ==========================================
    # Function: Add prioritized venue/court/time alternatives to the current goal.
    # Method: Validate the whole addition before appending, preventing partial invalid bundles.
    # ==========================================
    def add_candidates(self, contexts, slots, bundle):
        self.editable()
        if not slots or not contexts or any(not c['courts'] for c in contexts):
            raise ValueError('请至少选择一个场地和一个时段')
        day = self.get()['candidates'][0]['date'] if self.repeating() else self.day()
        groups = [slots] if bundle else [[s] for s in slots]
        additions = [asdict(Target(day, court, group, BookingContext(**c['context']))) for c in contexts for court in c['courts'] for group in groups]
        item, values = self.get(), deepcopy(self.get()['candidates'])
        for candidate in additions:
            if candidate not in values:
                values.append(candidate)
        if sum(len(g['candidates']) for g in self.data['goals'])+len(values)-len(item['candidates']) > 1000:
            raise ValueError('最多支持 1000 个候选')
        item['candidates'] = values
        self.drafts[self.selected] = []
        self.dirty = True

    # ==========================================
    # Function: Validate a selected target projection or an explicitly requested full working copy.
    # Method: Use the booking schema without letting unrelated incomplete drafts block scoped saves.
    # ==========================================
    def payload(self, identity=None):
        if identity is None:
            return asdict(config_from_dict(self.data))
        item = self.get(identity)
        if item is None:
            raise ValueError('请先选择一个预约目标')
        repeating = self.repeating(identity)
        return asdict(config_from_dict(self.data | {'targets':[], 'goals':[] if repeating else [item],
                                                  'repeat_rules':[item] if repeating else []}))

    # ==========================================
    # Function: Accept a scoped disk save while retaining other goals' unsaved and incomplete drafts.
    # Method: Update the persisted baseline and compute remaining edits without overwriting the working model.
    # ==========================================
    def accept_saved(self, data, identity=None, removed=()):
        self.baseline = deepcopy(data)
        if identity:
            self.replacements.pop(identity,None)
        for key in removed:
            self.replacements.pop(key,None)
        self.dirty = self.data != self.baseline

    # ==========================================
    # Function: Merge generated occurrences and persisted settings without erasing unrelated unsaved target drafts.
    # Method: Replace only entries unchanged from the previous disk baseline and append new remote identities.
    # ==========================================
    def merge_remote(self, data):
        for field in ('goals','repeat_rules'):
            before = {item['id']:item for item in self.baseline[field]}
            remote = {item['id']:item for item in data[field]}
            merged = []
            for item in self.data[field]:
                key = item['id']
                if key in before and item==before[key]:
                    if key in remote:
                        merged.append(deepcopy(remote[key]))
                else:
                    merged.append(item)
            present = {item['id'] for item in merged}
            merged.extend(deepcopy(item) for key,item in remote.items() if key not in present and key not in before)
            self.data[field] = merged
        self.baseline = deepcopy(data)
        self.dirty = self.data != self.baseline
