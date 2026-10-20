import os
import tkinter as tk
import unittest

from app import App
from catalog import Course, Replay


@unittest.skipUnless(os.name == 'nt', 'Windows desktop UI')
class EmptyReplayUITests(unittest.TestCase):
    def test_empty_course_clears_previous_selection_and_disables_download(self):
        window = tk.Tk()
        window.withdraw()
        try:
            app = App(window)
            course = Course('_123_1','测试课程','26271','')
            app.visible_courses = [course]
            app.course.configure(values=[course.name])
            app.course.current(0)
            app.replays = [Replay('old','旧回放','2026-10-12','教师甲','')]
            app.replay_list.insert('', 'end', iid='0', values=('旧回放','',''))
            app.replay_list.selection_set('0')
            app.select_replay()
            self.assertEqual(str(app.start['state']), 'normal')
            app.queue.put(('replays', {'course_id':course.id,'items':[],'state':'unavailable','message':'该课程的课堂实录尚未开放。'}))
            app.queue.put(('idle', None))
            app.drain()
            self.assertIsNone(app.selected_replay())
            self.assertEqual(app.title.get(), '')
            self.assertEqual(str(app.start['state']), 'disabled')
            self.assertIn('未开放', app.catalog_status.get())
            app.sources.select(1)
            app.source_changed()
            self.assertEqual(str(app.start['state']), 'normal')
        finally:
            window.destroy()


if __name__ == '__main__':
    unittest.main(verbosity=2)
