import hashlib,json,unittest
from merge_media import content_parts,media_values,visible_value,history_event,official_value,raw_value
from merge_stream import feed
import test_build5 as previous
from test_manager import SID
from test_build5 import SECOND

class Build17Tests(unittest.TestCase):
 def setUp(self):
  self.helper=previous.Build5Tests();self.helper.setUp();self.m=self.helper.m;self.other=self.helper.add()
  self.image={'type':'input_image','image_url':'data:image/png;base64,AAA','detail':'original'}
  self.file={'type':'input_image','file_id':'file-123','detail':'low'}
  self.audio={'type':'input_audio','audio_url':'data:audio/wav;base64,AAA'}
 def tearDown(self):self.helper.tearDown()
 def append(self,content,role='user'):
  with self.other.open('a') as f:f.write(json.dumps({'type':'response_item','payload':{'type':'message','role':role,'content':content}})+'\n')
 def message(self):return self.m.merge_content([SID,SECOND])[0][-1]
 def test_image_only_included(self):
  self.append([self.image]);m=self.message();self.assertEqual(m['content'],[self.image]);self.assertEqual(m['text'],'')
 def test_audio_only_included(self):
  self.append([self.audio]);self.assertEqual(self.message()['content'],[self.audio])
 def test_content_interleaving_preserved(self):
  parts=[{'type':'input_text','text':'before'},self.image,{'type':'input_text','text':'after'},self.audio,self.file]
  self.append(parts);self.assertEqual(self.message()['content'],parts)
 def test_plan_reports_media_counts(self):
  self.append([self.image,self.file,self.audio]);self.helper.c.update(cwd=str(self.helper.c.root))
  p=self.m.merge_plan([SID,SECOND],'test');self.assertEqual(p['image_count'],2);self.assertEqual(p['audio_count'],1);self.assertEqual(p['message_count'],3)
 def test_internal_text_filter_does_not_remove_image(self):
  self.append([{'type':'input_text','text':'# AGENTS.md instructions\ninternal'},self.image]);self.assertEqual(self.message()['content'],[self.image])
 def test_bad_image_reference_is_specific(self):
  self.append([{'type':'input_image'}])
  with self.assertRaisesRegex(Exception,'图片引用无效'):self.message()
 def test_conflicting_image_references_rejected(self):
  with self.assertRaisesRegex(Exception,'唯一'):content_parts([dict(self.image,file_id='file')],'user','line')
 def test_bad_audio_is_specific(self):
  self.append([{'type':'input_audio','audio_url':None}])
  with self.assertRaisesRegex(Exception,'audio_url'):self.message()
 def test_unknown_type_not_discarded(self):
  self.append([{'type':'future_video','data':'x'}])
  with self.assertRaisesRegex(Exception,'future_video'):self.message()
 def test_assistant_media_not_silently_hidden(self):
  self.append([self.image],'assistant')
  with self.assertRaisesRegex(Exception,'助手消息'):self.message()
 def test_image_order_detail_and_audio_event(self):
  self.append([self.file,self.image,self.audio]);m=self.message();e=history_event(m)
  self.assertEqual(e['image_order'],['file','inline']);self.assertEqual(e['file_id_details'],['low']);self.assertEqual(e['image_details'],['original']);self.assertEqual(e['audio'],[self.audio['audio_url']])
 def test_official_full_media_hash_matches(self):
  self.append([self.file,self.image,self.audio]);m=self.message();h=hashlib.sha256();feed(h,visible_value(m['role'],m['text'],media_values(m['content'])))
  item={'type':'userMessage','content':[{'type':'image','fileId':'file-123','detail':'low'},{'type':'image','url':self.image['image_url'],'detail':'original'},{'type':'audio','url':self.audio['audio_url']}]}
  self.m.merger.verify_items(iter([item]),h.hexdigest(),1)
 def test_official_missing_attachment_is_failure(self):
  h=hashlib.sha256();feed(h,visible_value('user','text',media_values([self.image])))
  with self.assertRaisesRegex(Exception,'附件'):self.m.merger.verify_items(iter([{'type':'userMessage','content':[{'type':'text','text':'text'}]}]),h.hexdigest(),1)
 def test_official_modified_image_is_failure(self):
  h=hashlib.sha256();feed(h,visible_value('user','',media_values([self.image])))
  with self.assertRaisesRegex(Exception,'不一致'):self.m.merger.verify_items(iter([{'type':'userMessage','content':[{'type':'image','url':'changed','detail':'original'}]}]),h.hexdigest(),1)
 def test_official_unknown_output_is_failure(self):
  with self.assertRaisesRegex(Exception,'未预期'):official_value({'type':'userMessage','content':[{'type':'future','x':1}]})
 def test_raw_content_fingerprint_detects_interleaving_change(self):
  m={'role':'user','content':[self.image,self.audio],'phase':None};before=raw_value(m);m={**m,'content':[self.audio,self.image]};self.assertNotEqual(before,raw_value(m))
 def test_optional_null_detail_normalized(self):
  result=content_parts([dict(self.image,detail=None)],'user','line');self.assertNotIn('detail',result[0]);self.assertIsNone(media_values(result)[0][-1])
 def test_text_digest_backwards_compatible(self):self.assertEqual(visible_value('user','text',[]),['user','text'])
if __name__=='__main__':unittest.main()
