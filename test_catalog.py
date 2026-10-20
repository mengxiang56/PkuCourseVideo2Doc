from datetime import datetime
import unittest

from catalog import current_term, official_url, parse_courses, parse_replays, term_label


class CatalogTests(unittest.TestCase):
    def test_current_term_and_summer(self):
        self.assertEqual(current_term(datetime(2026,10,19)), '26271')
        self.assertEqual(current_term(datetime(2027,1,10)), '26271')
        self.assertEqual(current_term(datetime(2027,3,1)), '26272')
        self.assertEqual(current_term(datetime(2027,7,10)), '26273')
        self.assertIn('2026—2027', term_label('26271'))

    def test_enrolled_course_links_and_legacy_blackboard_ids(self):
        links = [
            {'text':'26271-00048-0000: 数字系统(26-27学年第1学期)', 'href':'/webapps/blackboard/execute/launcher?type=Course&id=PkId{key=_123_1, dataType=course}&url='},
            {'text':'25262-00048-0000: 旧课程', 'href':'/webapps/blackboard/execute/launcher?type=Course&id=_122_1'},
            {'text':'其他链接', 'href':'https://attacker.test/webapps/blackboard/execute/launcher?type=Course&id=_999_1'},
        ]
        courses = parse_courses(links + links[:1])
        self.assertEqual(len(courses), 2)
        self.assertEqual((courses[0].id, courses[0].term), ('_123_1','26271'))
        self.assertEqual(courses[0].name, '数字系统(26-27学年第1学期)')

    def test_replay_metadata_sort_dedup_and_sensitive_url_repr(self):
        rows = [
            {'cells':['10月12日第3-4节','2026-10-12 10:10:00','教师甲','观看'], 'href':'playVideo.action?token=fictional-token'},
            {'cells':['9月28日第3-4节','2026-09-28 10:10:00','教师甲','观看'], 'href':'playVideo.action?token=fictional-other'},
            {'cells':['外部','2026-10-13',''], 'href':'https://attacker.test/playVideo.action?token=fictional-token'},
        ]
        replays = parse_replays(rows + rows[:1], 'https://course.pku.edu.cn/webapps/bb-streammedia-hqy-BBLEARN/videoList.action')
        self.assertEqual(len(replays), 2)
        self.assertIn('10月12日', replays[0].label)
        self.assertNotIn('fictional-token', repr(replays[0]))
        self.assertNotIn('token=', replays[0].key)
        self.assertEqual(replays[0].url.split('?')[0], 'https://course.pku.edu.cn/webapps/bb-streammedia-hqy-BBLEARN/playVideo.action')

    def test_external_urls_are_excluded(self):
        for url in ('http://course.pku.edu.cn/x', 'https://course.pku.edu.cn.attacker.test/x', 'javascript:alert(1)'):
            self.assertIsNone(official_url(url))


if __name__ == '__main__':
    unittest.main(verbosity=2)
