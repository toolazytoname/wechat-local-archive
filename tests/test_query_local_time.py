import unittest
from wechat_export.export_service import QuerySpec, QueryError, parse_time_to_ms

class LocalTimeQueryTests(unittest.TestCase):
    def spec(self, since, tz='America/Los_Angeles'):
        return QuerySpec.from_mapping({'scope': {'kind':'all'}, 'since': since, 'display_timezone': tz})

    def test_naive_bound_uses_declared_timezone(self):
        spec=self.spec('2026-01-02T01:00')
        self.assertEqual(spec.since_ms,parse_time_to_ms('2026-01-02T09:00:00Z'))

    def test_spring_gap_rejected(self):
        with self.assertRaises(QueryError) as ctx:self.spec('2026-03-08T02:30')
        self.assertEqual(ctx.exception.code,'nonexistent_local_time')

    def test_autumn_fold_requires_offset(self):
        with self.assertRaises(QueryError) as ctx:self.spec('2026-11-01T01:30')
        self.assertEqual(ctx.exception.code,'ambiguous_local_time')

    def test_explicit_fold_offsets_are_distinct(self):
        first=self.spec('2026-11-01T01:30:00-07:00')
        second=self.spec('2026-11-01T01:30:00-08:00')
        self.assertEqual(second.since_ms-first.since_ms,3600000)

    def test_non_hour_dst_fold(self):
        with self.assertRaises(QueryError):self.spec('2026-04-05T01:45','Australia/Lord_Howe')

    def test_invalid_calendar_date_not_silently_normalized(self):
        with self.assertRaises(QueryError):self.spec('2026-02-30T01:30')

    def test_no_dst_zone_is_unambiguous(self):
        self.assertEqual(self.spec('2026-11-01T01:30','Asia/Shanghai').since_ms,
                         parse_time_to_ms('2026-10-31T17:30Z'))

    def test_mixed_explicit_and_local_interval(self):
        spec=QuerySpec.from_mapping({'scope':{'kind':'all'},'display_timezone':'America/Los_Angeles',
                                    'since':'2026-01-02T01:00','until':'2026-01-02T09:01Z'})
        self.assertEqual(spec.until_ms-spec.since_ms,60000)
