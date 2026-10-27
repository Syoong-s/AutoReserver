"""Toolkit-independent editor invariants shared by the production GTK client."""

from copy import deepcopy
from datetime import timedelta
import unittest

from autoreserve.workspace import Workspace
from autoreserve.catalog import ANY_COURT
from test_catalog import native_config


# ==========================================
# Function: Verify editor behavior without constructing any graphical toolkit.
# Method: Exercise real model mutations and the persisted schema in isolated memory.
# ==========================================
class WorkspaceEditorTests(unittest.TestCase):
    # ==========================================
    # Function: Provide a valid independent workspace for each scenario.
    # Method: Reuse the offline booking fixture and deep-copy its dataclass representation.
    # ==========================================
    def setUp(self):
        self.model=Workspace(native_config())

    # ==========================================
    # Function: Base next-day copies on the source date and preserve source candidates.
    # Method: Change a future source date, deep-copy it, and compare nested object identities.
    # ==========================================
    def test_copy_next_day_is_independent(self):
        model=self.model
        day=(model.today()+timedelta(days=5)).isoformat()
        model.metadata('运动计划',day)
        source=deepcopy(model.get())
        copied=model.copy_next(model.selected)
        self.assertEqual(copied['candidates'][0]['date'],(model.today()+timedelta(days=6)).isoformat())
        self.assertEqual(model.data['goals'][0],source)
        self.assertNotEqual(copied['id'],source['id'])
        self.assertIsNone(copied['repeat_id'])

    # ==========================================
    # Function: Avoid leaving an extra executable ordinary goal when enabling repeat.
    # Method: Convert both ways and verify candidate priority and independent IDs survive.
    # ==========================================
    def test_mode_conversion_preserves_requirements(self):
        model=self.model
        source=deepcopy(model.get())
        model.toggle_repeat()
        self.assertTrue(model.repeating())
        self.assertEqual(model.data['goals'],[])
        self.assertEqual(len(model.data['repeat_rules']),1)
        self.assertTrue(all(c['context'] for c in model.get()['candidates']))
        model.schedule([0,2,4],'11:30',3,True)
        model.toggle_repeat()
        self.assertFalse(model.repeating())
        self.assertEqual(model.data['repeat_rules'],[])
        self.assertNotEqual(model.get()['id'],source['id'])
        self.assertEqual(model.get()['candidates'][0]['court'],source['candidates'][0]['court'])
        model.payload()

    # ==========================================
    # Function: Reject invalid schedules without partially changing a valid template.
    # Method: Compare the complete model before and after an empty-week validation error.
    # ==========================================
    def test_weekly_validation_is_atomic_and_unchanged_commit_stays_clean(self):
        model=self.model
        model.toggle_repeat()
        model.dirty=False
        rule=deepcopy(model.get())
        model.schedule(rule['weekdays'],rule['at'],rule['days_ahead'],rule['enabled'])
        self.assertFalse(model.dirty)
        with self.assertRaises(ValueError):
            model.schedule([],'12:00',2,True)
        self.assertEqual(model.get(),rule)

    # ==========================================
    # Function: Keep each goal's unfinished venue selection independent.
    # Method: Stage two contexts on separate targets and navigate by stable identity.
    # ==========================================
    def test_staged_venues_are_per_goal(self):
        model=self.model
        first=model.selected
        context={'booking_url':model.data['booking_url'],'venue':model.data['venue'],'sport':model.data['sport']}
        model.stage(context,[ANY_COURT])
        model.add()
        model.stage(context,['场地2'])
        self.assertEqual(model.drafts[first][0]['courts'],[ANY_COURT])
        self.assertEqual(model.drafts[model.selected][0]['courts'],['场地2'])

    # ==========================================
    # Function: Reject nonadjacent bundles before altering candidate requirements.
    # Method: Try to add a disjoint two-hour order and compare existing alternatives.
    # ==========================================
    def test_invalid_bundle_does_not_partially_append(self):
        model=self.model
        source=deepcopy(model.get())
        context={'booking_url':model.data['booking_url'],'venue':model.data['venue'],'sport':model.data['sport']}
        with self.assertRaises(ValueError):
            model.add_candidates([{'context':context,'courts':[ANY_COURT]}],['12:00-13:00','14:00-15:00'],True)
        self.assertEqual(model.get(),source)

    # ==========================================
    # Function: Preserve durable goal requirements while permitting independent copies.
    # Method: Mark a source submitted, reject editing/removal, and allow its next-day clone.
    # ==========================================
    def test_recorded_goals_are_immutable_but_copyable(self):
        model=self.model
        source=model.selected
        model.states[source]={'status':'submitted'}
        with self.assertRaises(ValueError):
            model.remove(source)
        with self.assertRaises(ValueError):
            model.metadata('改写','2030-01-01')
        model.copy_next(source)
        self.assertEqual(len(model.data['goals']),2)

    # ==========================================
    # Function: Keep old weekly templates and materialized order instances independent.
    # Method: Generate an occurrence then remove its template, retaining the dated target.
    # ==========================================
    def test_removing_repeat_keeps_generated_occurrences(self):
        model=self.model
        model.toggle_repeat()
        identity=model.selected
        template=deepcopy(model.get())
        occurrence={'id':'daily-'+template['id']+'-'+model.today().isoformat(),'name':template['name'],
                    'candidates':deepcopy(template['candidates']),'repeat_id':template['id'],'origin':None}
        model.data['goals'].append(occurrence)
        model.remove(identity)
        self.assertEqual(model.data['goals'],[occurrence])
        self.assertEqual(model.data['repeat_rules'],[])
