import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import learning_features as features
import v2_http
from netforge_v2 import V2Error, connect_db, verify_password, import_question_bank
from test_v2_backend import create_legacy_database, write_bank


class LearningUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=self.root/'test.db';self.bank=self.root/'bank.js'
        create_legacy_database(self.db);write_bank(self.bank)
        self.rt=v2_http.configure(self.db,self.bank,'root','Test-Admin-Password!')
        self.aid=self.rt.bootstrap_admin_id
    def tearDown(self):self.temp.cleanup()

    def answered(self,selected=None):
        session=self.rt.service.create_practice_session(1,['net-test-1'])
        self.rt.service.submit_practice_answer(1,session['id'],'net-test-1',selected or ['A'],'one')
        return {'sessionId':session['id'],'questionId':'net-test-1'}

    def test_preferences_are_isolated_between_users_and_admin(self):
        value={'mode':'light','palette':'violet','background':'#fafafa','surface':'#ffffff'}
        self.assertEqual(features.preferences(self.rt,'user',1,value),value)
        self.assertEqual(features.preferences(self.rt,'user',2)['mode'],'dark')
        self.assertEqual(features.preferences(self.rt,'admin',1)['mode'],'dark')
        with self.assertRaises(V2Error):features.preferences(self.rt,'user',1,{**value,'surface':'url(javascript:alert(1))'})

    def test_explanations_require_owned_answer_and_are_cached(self):
        with patch.object(features,'deepseek',return_value='教学文档') as provider:
            with self.assertRaises(V2Error):features.explain(self.rt,1,{'questionId':'net-test-1'})
            body=self.answered()
            with self.assertRaises(V2Error):features.explain(self.rt,2,body)
            self.assertEqual(features.explain(self.rt,1,body)['text'],'教学文档')
            self.assertTrue(features.explain(self.rt,1,body)['cached'])
            provider.assert_called_once()

    def test_failed_ai_request_can_retry_without_caching_error(self):
        body=self.answered()
        with patch.object(features,'deepseek',side_effect=V2Error('ai_provider_error','unavailable')):
            with self.assertRaises(V2Error):features.explain(self.rt,1,body)
        with patch.object(features,'deepseek',return_value='retry success'):
            self.assertEqual(features.explain(self.rt,1,body)['text'],'retry success')

    def test_hard_delete_requires_super_and_clears_relations_and_files(self):
        body=self.answered();features.preferences(self.rt,'user',1,{'mode':'dark','palette':'blue'})
        support=self.rt.service.create_admin_account('support','Test-Admin-Password!',['support_admin'],actor_admin_id=self.aid)
        paper=self.rt.service.create_exam_paper(self.aid,'test',['net-test-1'],published=True)
        attempt=self.rt.service.start_exam_attempt(1,paper)
        self.rt.service.create_feedback(1,'net-test-1','other','test feedback')
        avatar=self.root/'uploads'/'avatars'/'user-1-test.png';avatar.parent.mkdir(parents=True);avatar.write_bytes(b'test')
        with patch.object(features,'deepseek',return_value='cached'):features.explain(self.rt,1,body)
        request={'userId':1,'confirm':True,'confirmUsername':'alice'}
        with self.assertRaises(V2Error):features.hard_delete_user(self.rt,support,request)
        with self.assertRaises(V2Error):features.hard_delete_user(self.rt,self.aid,{**request,'confirmUsername':'wrong'})
        self.assertTrue(features.hard_delete_user(self.rt,self.aid,request)['deleted'])
        self.assertFalse(avatar.exists())
        with connect_db(self.db) as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertIsNone(db.execute('SELECT 1 FROM users WHERE id=1').fetchone())
            self.assertIsNotNone(db.execute('SELECT 1 FROM users WHERE id=2').fetchone())
            for table in ('practice_sessions','practice_answers','question_feedback','exam_attempts','ai_documents','appearance_preferences'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],0,table)

    def test_user_detail_excludes_credentials_and_includes_learning(self):
        self.answered();detail=features.user_detail(self.rt,1)
        self.assertEqual(detail['metrics']['answered'],1)
        self.assertEqual(len(detail['answers']),1)
        self.assertNotIn('password_hash',json.dumps(detail))
        self.assertNotIn('token_hash',json.dumps(detail))
        with patch.object(features,'deepseek',return_value='画像') as provider:
            self.assertEqual(features.portrait(self.rt,self.aid,1,{'confirm':True})['text'],'画像')
            self.assertNotIn('alice',json.dumps(provider.call_args.args))

    def test_deleted_paper_rejects_new_attempts_and_preserves_old_attempt(self):
        paper=self.rt.service.create_exam_paper(self.aid,'test',['net-test-1'],published=True)
        attempt=self.rt.service.start_exam_attempt(1,paper)
        self.assertTrue(features.delete_paper(self.rt,self.aid,{'paperId':paper,'confirm':True})['historyPreserved'])
        with self.assertRaises(V2Error):self.rt.service.start_exam_attempt(1,paper)
        self.assertEqual(v2_http._exam_attempt(1,attempt)['id'],attempt)
        self.assertNotIn(paper,[x['id'] for x in v2_http._admin_papers({})['items']])

    def test_code_delete_preserves_registered_user(self):
        code=self.rt.service.create_registration_code(self.aid,'TEST-DELETE-2026','test',None,10)
        with connect_db(self.db) as db:
            cid=db.execute('SELECT id FROM registration_codes').fetchone()[0]
            db.execute('INSERT INTO registration_code_uses VALUES(?,1,1)',(cid,))
        self.assertTrue(features.delete_code(self.rt,self.aid,{'codeId':cid,'confirm':True})['deleted'])
        self.assertEqual(features.user_detail(self.rt,1)['user']['username'],'alice')

    def test_wrong_book_starts_all_wrong_and_scores_are_independent(self):
        self.answered(['B'])
        session=v2_http._create_practice(1,{'allWrong':True,'mode':'study','count':1})['session']
        self.assertEqual(session['questionIds'],['net-test-1']);self.assertEqual(session['mode'],'practice')
        board=features.leaderboard(self.rt,'all');alice=next(x for x in board['items'] if x['userId']==1)
        self.assertEqual(alice['practiceScore'],0)
        self.assertEqual(alice['examScore'],0)
        self.assertIn('percentile',board['formula'])

    def test_reference_fetch_rejects_internal_hosts_and_redirects(self):
        for url in ('http://127.0.0.1','https://127.0.0.1','https://support.huawei.com.evil.test','https://support.huawei.com@127.0.0.1'):
            with self.assertRaises(V2Error):features.reference_text(url)
        self.assertIsNone(features.NoRedirect().redirect_request(None,None,None,None,None,None))

    def test_motivation_does_not_repeat_when_network_is_unavailable(self):
        with patch.object(features.request,'urlopen',side_effect=OSError):
            messages=[features.motivation(self.rt,1)['text'] for _ in range(12)]
        self.assertEqual(len(messages),len(set(messages)))

    def test_admin_edit_updates_all_metrics_without_fabricating_records(self):
        self.answered(['B']);before=features.user_detail(self.rt,1)
        metrics={'answered':100,'correct':80,'wrong':15,'exams':12,'submittedExams':8,'favorites':9,'feedback':4}
        result=features.update_user(self.rt,self.aid,1,{'profile':{'username':'alice_edited','display_name':'更新昵称','avatar_url':'/uploads/avatars/avatar.png','updated_at':1700000000,'deleted_at':None,'delete_reason':'业务备注'},'metrics':metrics,'version':before['editVersion'],'reason':'修正测试统计','confirm':True})
        self.assertEqual(result['metrics'],metrics)
        self.assertEqual(result['user']['username'],'alice_edited')
        self.assertEqual(result['actualMetrics'],before['actualMetrics'])
        for field in ('id','created_at','last_login_at','status'):
            self.assertEqual(result['user'][field],before['user'][field])
        self.assertEqual(v2_http._home(1)['metrics']['answered'],100)
        self.assertEqual(v2_http._home(1)['metrics']['accuracy'],80)
        self.assertEqual(v2_http._learning(1)['metrics']['wrongCount'],15)
        self.assertEqual(v2_http._public_profile(1)['practiceAnswered'],100)
        listed=next(x for x in v2_http._admin_users({})['items'] if x['id']==1)
        self.assertEqual((listed['practiceAnswers'],listed['practiceCorrect'],listed['examAttempts']),(100,80,12))
        self.assertEqual(len(result['wrongItems']),1)
        self.assertEqual(len(result['answers']),1)
        self.assertEqual(features.user_detail(self.rt,2)['metrics']['answered'],0)
        # Subsequent genuine answers continue increasing the corrected counters.
        self.answered(['A'])
        updated=features.user_detail(self.rt,1)
        self.assertEqual(updated['metrics']['answered'],101)
        self.assertEqual(updated['metrics']['correct'],81)

    def test_admin_edit_rejects_readonly_fields_bad_counts_and_duplicate_name(self):
        detail=features.user_detail(self.rt,1)
        base={'version':detail['editVersion'],'reason':'test','confirm':True}
        for field in ('id','created_at','last_login_at','status'):
            with self.subTest(field=field),self.assertRaises(V2Error):
                features.update_user(self.rt,self.aid,1,{**base,'profile':{field:1}})
        for metrics in ({'answered':-1},{'answered':True},{'answered':1.5},{'answered':1,'correct':2},{'exams':1,'submittedExams':2},{'favorites':1000000001}):
            with self.subTest(metrics=metrics),self.assertRaises(V2Error):
                features.update_user(self.rt,self.aid,1,{**base,'metrics':metrics})
        with self.assertRaises(V2Error) as duplicate:
            features.update_user(self.rt,self.aid,1,{**base,'profile':{'username':'BOB'}})
        self.assertEqual(duplicate.exception.code,'username_conflict')
        with self.assertRaises(V2Error):features.update_user(self.rt,self.aid,1,{**base,'confirm':False,'metrics':{'answered':10}})
        self.assertEqual(features.user_detail(self.rt,1)['user']['username'],'alice')

    def test_admin_edit_permissions_and_concurrent_learning_conflict(self):
        support=self.rt.service.create_admin_account('limited','Test-Admin-Password!',['support_admin'],actor_admin_id=self.aid)
        detail=features.user_detail(self.rt,1)
        body={'version':detail['editVersion'],'reason':'test','confirm':True,'metrics':{'answered':10}}
        with self.assertRaises(V2Error):features.update_user(self.rt,support,1,body)
        self.answered()
        with self.assertRaises(V2Error) as stale:features.update_user(self.rt,self.aid,1,body)
        self.assertEqual(stale.exception.code,'user_edit_conflict')

    def test_password_change_revokes_credentials_and_never_audits_password(self):
        detail=features.user_detail(self.rt,1)
        with connect_db(self.db) as db:
            db.execute("INSERT INTO sessions VALUES('old-session',1,9999999999,1)")
            db.execute("INSERT INTO password_reset_tokens(user_id,token_hash,expires_at,created_at) VALUES(1,'old-reset',9999999999,1)")
        body={'password':'New-Password-2026!','version':detail['editVersion'],'reason':'help learner regain access','confirm':True}
        result=features.change_user_password(self.rt,self.aid,1,body)
        self.assertTrue(result['sessionsRevoked'])
        with connect_db(self.db) as db:
            encoded=db.execute('SELECT password_hash FROM users WHERE id=1').fetchone()[0]
            self.assertTrue(verify_password(body['password'],encoded))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions WHERE user_id=1').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM password_reset_tokens WHERE user_id=1').fetchone()[0],0)
            audit=' '.join(str(tuple(row)) for row in db.execute('SELECT before_json,after_json FROM admin_audit_logs'))
            self.assertNotIn(body['password'],audit);self.assertNotIn(encoded,audit)
        self.assertNotEqual(features.user_detail(self.rt,1)['editVersion'],detail['editVersion'])
        with self.assertRaises(V2Error):features.change_user_password(self.rt,self.aid,1,{**body,'password':'short'})


    def test_memorize_counts_all_filters_and_no_score_side_effect(self):
        original=json.loads(self.bank.read_text(encoding='utf-8').split('=',1)[1].rstrip(';'))['questions'][0]
        questions=[{**original,'id':f'large-{i}','question':f'Bulk {i}'} for i in range(125)]
        self.bank.write_text('window.NETWORK_QUESTION_BANK = '+json.dumps({'questions':questions})+';')
        import_question_bank(self.db,self.bank)
        for count in (20,60,80,'all'):
            session=v2_http._create_practice(1,{'mode':'study','count':count,'filters':{'q':'Bulk'}})['session']
            ids=session['questionIds'];self.assertEqual(len(ids),125 if count=='all' else count)
            self.assertEqual(len(ids),len(set(ids)))
        for count in (0,1,100,True,1.5,'60'):
            with self.assertRaises(V2Error):v2_http._create_practice(1,{'mode':'study','count':count})
        with self.assertRaises(V2Error):v2_http._create_practice(1,{'mode':'study','count':'all','filters':{'topic':'no match'}})
        small=v2_http._create_practice(1,{'mode':'study','count':80,'filters':{'q':'Q2'}})['session']
        self.assertEqual(small['questionIds'],['net-test-2'])
        with connect_db(self.db) as db:before=features.actual_metrics(db,1)
        result=v2_http._practice_answer(1,small['id'],{'questionId':'net-test-2','studyReveal':True})
        self.assertTrue(result['studyReveal'])
        with self.assertRaises(V2Error):self.rt.service.submit_practice_answer(1,small['id'],'net-test-2',['A','B'],'fake-score')
        with connect_db(self.db) as db:self.assertEqual(before,features.actual_metrics(db,1))

    def test_memorize_ai_and_references_are_owned_and_published(self):
        session=self.rt.service.create_practice_session(1,['net-test-1'],mode='memorize')
        body={'sessionId':session['id'],'questionId':'net-test-1'}
        with patch.object(features,'deepseek',return_value='背题AI讲解'):
            self.assertEqual(features.explain(self.rt,1,body)['text'],'背题AI讲解')
        with connect_db(self.db) as db:
            revision=db.execute("SELECT current_revision_id FROM questions WHERE id='net-test-1'").fetchone()[0]
            payload=json.loads(db.execute('SELECT payload_json FROM question_revisions WHERE id=?',(revision,)).fetchone()[0])
            payload['references']=['https://www.rfc-editor.org/rfc/rfc8200']
            db.execute('UPDATE question_revisions SET payload_json=? WHERE id=?',(json.dumps(payload),revision))
        with patch.object(features,'reference_text',return_value='IPv6公开参考正文'),patch.object(features,'deepseek',return_value='背题参考文档'):
            self.assertEqual(features.reference_document(self.rt,1,{**body,'index':0})['text'],'背题参考文档')
        for uid,qid in ((2,'net-test-1'),(1,'net-test-2')):
            with self.assertRaises(V2Error):features.answered_payload(self.rt,uid,{**body,'questionId':qid})
        with connect_db(self.db) as db:db.execute("UPDATE questions SET status='unpublished' WHERE id='net-test-1'")
        with self.assertRaises(V2Error):features.answered_payload(self.rt,1,body)

    def test_personal_summary_cache_privacy_isolation_and_stale_state(self):
        self.assertFalse(features.personal_summary(self.rt,1)['ready'])
        with self.assertRaises(V2Error):features.personal_summary(self.rt,1,{})
        with self.assertRaises(V2Error):features.personal_summary(self.rt,1,{'confirm':True,'userId':2})
        with patch.object(features,'deepseek',return_value='# 专属学习分析') as provider:
            result=features.personal_summary(self.rt,1,{'confirm':True})
            self.assertTrue(result['ready']);self.assertFalse(result['stale'])
            self.assertTrue(features.personal_summary(self.rt,1,{'confirm':True})['cached'])
            provider.assert_called_once()
            prompt=json.dumps(provider.call_args.args,ensure_ascii=False)
            for secret in ('alice','password_hash','token_hash','display_name','delete_reason'):
                self.assertNotIn(secret,prompt)
        self.assertFalse(features.personal_summary(self.rt,2)['ready'])
        self.answered();self.assertTrue(features.personal_summary(self.rt,1)['stale'])
        with patch.object(features,'deepseek',return_value='新分析'):
            features.personal_summary(self.rt,1,{'confirm':True})
        current=features.personal_summary(self.rt,1)
        self.assertEqual(current['text'],'新分析');self.assertFalse(current['stale'])

    def test_personal_summary_failure_retries_and_deletion_does_not_recreate_data(self):
        with patch.object(features,'deepseek',side_effect=V2Error('ai_provider_error','test')):
            with self.assertRaises(V2Error):features.personal_summary(self.rt,1,{'confirm':True})
        self.assertFalse(features.personal_summary(self.rt,1)['ready'])
        def deleted(_messages):
            features.hard_delete_user(self.rt,self.aid,{'userId':1,'confirm':True,'confirmUsername':'alice'})
            return 'late response'
        with patch.object(features,'deepseek',side_effect=deleted):
            with self.assertRaises(V2Error):features.personal_summary(self.rt,1,{'confirm':True})
        with connect_db(self.db) as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_documents WHERE owner_type='user' AND owner_id=1").fetchone()[0],0)

    def test_ratio_ranking_calendar_windows_zero_and_legacy_answers(self):
        # Wednesday 2026-10-28 noon in Beijing; yesterday belongs to this week.
        from datetime import datetime,timezone,timedelta
        now=int(datetime(2026,10,28,12,tzinfo=timezone(timedelta(hours=8))).timestamp())
        with connect_db(self.db) as db:
            for uid,correct,stamp,skipped in [(1,1,now,0),(1,1,now,0),(1,0,now,0),(1,1,now-86400,0),(1,1,now-8*86400,0),(1,0,now,1),(2,1,now,0),(2,1,now,0)]:
                db.execute("INSERT INTO attempts(user_id,question_id,selected_json,correct,skipped,topic,question_type,created_at) VALUES(?,'net-test-1','[]',?,?,'WLAN','single',?)",(uid,correct,skipped,stamp))
        with patch.object(features.time,'time',return_value=now):
            for period,expected in [('day',10),('week',15),('all',20)]:
                board=features.leaderboard(self.rt,period);alice=next(x for x in board['items'] if x['userId']==1)
                self.assertEqual(alice['practiceScore'],expected)
                self.assertEqual(alice['wrong'],1)
            bob=next(x for x in features.leaderboard(self.rt,'day')['items'] if x['userId']==2)
            self.assertEqual(bob['practiceScore'],20)
        with self.assertRaises(V2Error):features.leaderboard(self.rt,'invalid')


if __name__=='__main__':unittest.main()
