import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parent))
import fastnews247_mvp as n

class ReliabilityTests(unittest.TestCase):
    def test_write_ahead_intent_survives_failure_and_prevents_retry(self):
        import datetime
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config={'channel':{'stateDb':'state.json','draftOutput':'draft.md'},
                    'posting':{'telegram':{'mode':'bridge','channelId':'@fastnews247vn'},
                               'duplicateWindowHours':72,'minimumScoreToDraft':4,
                               'minimumScoreToPost':4,'maxPostsPerRun':1},
                    'feeds':[{'name':'fixture','url':'https://example.test/feed'}]}
            item={'title':'Fed holds rate at 5 percent after September meeting',
                  'published':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  'source':'fixture','link':'https://example.test/article'}
            def fail_send(*args):
                state=json.loads((root/'state.json').read_text())
                self.assertEqual(next(iter(state['seen'].values()))['status'],'pending')
                raise RuntimeError('uncertain')
            with patch.object(n,'ROOT',root), patch.object(n,'parse_feed',return_value=[item]), patch.object(n,'score_item',return_value=(4,[],'fixture')), patch.object(n,'source_article_text',return_value=('verified fixture','')), patch.object(n,'draft_post',return_value=('local fixture',[])), patch.object(n,'telegram_post',side_effect=fail_send) as send:
                self.assertEqual(n.run_once(config,post=True),2)
                self.assertEqual(n.run_once(config,post=True),0)
                self.assertEqual(send.call_count,1)

    def test_unnamed_meta_does_not_crash_article_parser(self):
        parser=n.ArticleHTMLParser()
        parser.feed('<meta charset="utf-8"><meta http-equiv="refresh"><p>Fed giữ lãi suất chính sách ở mức hiện tại và công bố thêm báo cáo về triển vọng kinh tế trong năm tới.</p>')
        self.assertTrue(parser.paragraphs)

    def test_namespaced_rss_and_atom_updated(self):
        feed={'name':'fixture','url':'https://example.test/rss'}
        samples=[
            b'<rdf xmlns:r="urn:rss"><r:item><r:title>News</r:title><r:pubDate>Sat, 19 Sep 2026 08:00:00 GMT</r:pubDate></r:item></rdf>',
            b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>News</title><updated>2026-09-19T08:00:00Z</updated></entry></feed>'
        ]
        for raw in samples:
            with patch.object(n,'fetch_url',return_value=raw):
                self.assertGreater(n.parse_time(n.parse_feed(feed)[0]['published']),0)

    def test_no_ack_is_not_confirmation(self):
        for stdout in ('', '{"ok":true}', '{"status":"submitted"}'):
            with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout, '')):
                self.assertEqual(n.telegram_bridge_post({'channelId':'@fastnews247vn'}, 'local fixture')['status'], 'pending')

    def test_ack_requires_message_and_chat(self):
        self.assertIsNone(n.extract_ack({'messageId':12}))
        self.assertIsNone(n.extract_ack({'ok':False,'messageId':12,'chatId':-100}))
        self.assertEqual(n.extract_ack({'payload':{'messageId':12,'chatId':-100}})['messageId'],12)

    def test_timeout_stays_pending(self):
        with patch.object(subprocess,'run',side_effect=subprocess.TimeoutExpired([],60)):
            self.assertEqual(n.telegram_bridge_post({'channelId':'@fastnews247vn'},'fixture')['status'],'pending')

    def test_uncertain_and_legacy_records_never_auto_expire(self):
        state={'seen':{'pending':{'time':1,'status':'pending'},'legacy':{'time':1},'confirmed':{'time':1,'status':'confirmed'}}}
        self.assertEqual(set(n.prune_state(state,72)['seen']),{'pending','legacy'})

    def test_atomic_replace_preserves_previous_on_failure(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json'; n.save_json(p,{'a':1})
            with patch.object(n.os,'replace',side_effect=OSError('fixture')):
                with self.assertRaises(OSError): n.save_json(p,{'a':2})
            self.assertEqual(json.loads(p.read_text()),{'a':1})

    def test_lock_rejects_other_process_then_recovers(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'lock'
            script="import sys; sys.path.insert(0, 'scripts'); import fastnews247_mvp as n; from pathlib import Path; c=n.owned_lock(Path(sys.argv[1])); c.__enter__()"
            with n.owned_lock(p):
                child=subprocess.run([sys.executable,'-c',script,str(p)],capture_output=True)
                self.assertNotEqual(child.returncode,0)
            with n.owned_lock(p): pass

    def test_domain_brand_preserved_but_unlinked(self):
        safe=n.strip_urls('Crypto.com công bố kế hoạch tại Mỹ.')
        self.assertIn('Crypto chấm com',safe)
        self.assertFalse(n.URL_PATTERN.search(safe))

    def test_truncated_sentence_rejected(self):
        self.assertFalse(n.split_complete_sentences('Sàn giao dịch công bố thỏa thuận với sàn giao dịch chị em...'))
        self.assertFalse(n.split_complete_sentences('Sàn giao dịch công bố thỏa thuận với đối tác'))
        self.assertIn('incomplete-summary',n.summary_quality_issues('Fed tăng lãi suất 25 điểm cơ bản...',{'article_text':''}))

    def test_flags_are_event_geography_not_assets(self):
        flags,_=n.market_flags({'title':'Bitcoin tăng sau số liệu CPI Canada','summary':''},['#BTC'])
        self.assertNotIn('₿',flags)
        self.assertNotIn('🇺🇸',flags)
        self.assertIn('🇨🇦',flags)

    def test_vietnamese_publisher_does_not_imply_vietnam_flag(self):
        flags,_=n.market_flags({'category':'vietnam_market','title':'Nhật Bản nâng lãi suất lên cao nhất 31 năm','summary':''},[])
        self.assertEqual(flags,'🇯🇵')
        flags,_=n.market_flags({'title':'Bank of England releases CPI letter','summary':''},[])
        self.assertEqual(flags,'🇬🇧')

    def test_date_offsets_and_naive_rejection(self):
        self.assertEqual(n.parse_time('2026-09-19T15:00:00+07:00'),n.parse_time('Sat, 19 Sep 2026 08:00:00 GMT'))
        self.assertEqual(n.parse_time('2026-09-19T15:00:00'),0)

    def test_cross_feed_canonical_and_event_dedup(self):
        a={'title':'Fed holds interest rate at 5.5 percent after September meeting','link':'https://example.org/news?utm_source=a'}
        b={'title':'Fed holds interest rate at 5.5 percent after September meeting today','link':'https://other.org/news'}
        self.assertTrue(n.same_event(a,b))
        self.assertTrue(n.same_event(a,{'title':'different title','link':'https://www.example.org/news?ref=b'}))
        self.assertFalse(n.same_event(a,{'title':b['title'].replace('5.5','5.0')}))

if __name__=='__main__': unittest.main()
