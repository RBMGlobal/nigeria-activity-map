"""Source-string punctuation must survive collection, rendering and validation."""
from datetime import date, timedelta
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('MPLBACKEND','Agg')
PIPELINE=Path(__file__).resolve().parents[1]
SCRIPTS=PIPELINE/'scripts'
sys.path.insert(0,str(SCRIPTS))
from embedded_data import load_json, replace_json


def module(name,folder=SCRIPTS):
    spec=importlib.util.spec_from_file_location('punctuation_'+name,folder/(name+'.py'))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


class EmbeddedDataTests(unittest.TestCase):
    def test_literals_nested_json_and_whitespace(self):
        value=[{'text':'literal ]; text; quoted "value"; object }; text','nested':[['x'],{'z':2}]}]
        text='// before\n  const EVENTS\t=\n '+json.dumps(value)+' ; // after\nconst OTHER = [3];'
        self.assertEqual(load_json(text,'EVENTS'),value)
        replaced=replace_json(text,'EVENTS',json.dumps([{'text':'new ]; literal'}]))
        self.assertEqual(load_json(replaced,'EVENTS'),[{'text':'new ]; literal'}])
        self.assertTrue(replaced.startswith('// before\n  const EVENTS\t=\n '))
        self.assertTrue(replaced.endswith(' ; // after\nconst OTHER = [3];'))

    def test_rejects_missing_duplicate_truncated_or_executable_value(self):
        for text in ['const OTHER = [];','const EVENTS = [];\nconst EVENTS = [];','const EVENTS = [{"text":"x"};','const EVENTS = [] + injected();','const EVENTS = {};' ]:
            with self.subTest(text=text), self.assertRaises(ValueError):load_json(text,'EVENTS')

    def test_optional_and_object_values(self):
        self.assertIsNone(load_json('const OTHER = [];','EVENTS',required=False))
        meta={'note':'literal }; text'}
        self.assertEqual(load_json('const META = '+json.dumps(meta)+';','META',dict),meta)

    def test_declaration_text_inside_string_is_not_a_second_declaration(self):
        value=[{'text':'const EVENTS = [];\nconst EVENTS = [];'}]
        self.assertEqual(load_json('const EVENTS = '+json.dumps(value)+';','EVENTS'),value)

    def test_real_render_validator_and_heat_keep_literal_headline(self):
        ucdp=module('update_events_map');gdelt=module('update_gdelt_layer')
        renderer=module('render_events_lastweek');validator=module('validate_map_release')
        heat=module('refresh_sentiment_heat');builder=module('build_release',PIPELINE)
        yesterday=date.today()-timedelta(days=1)
        text_value='literal ]; text, quoted "value" and </script>'
        events=[{'d':yesterday.isoformat(),'la':8+i/1000,'lo':8,'t':1,'k':1,'c':0,'s':'Test','w':'Test','headline':text_value} for i in range(101)]
        news=[{'d':yesterday.isoformat(),'la':9+i/1000,'lo':9,'r':19,'n':1,'id':str(i),'s':'Test','w':'Test','headline':text_value} for i in range(101)]
        html='<html><script>/*'+('padding '*65000)+'*/\nL.map("map");\nconst EVENTS = '+json.dumps(events)+';\nconst GDELT_EVENTS = '+json.dumps(news)+';\n</script><div class="sub">Synthetic fixture</div></html>'
        html=ucdp.replace_json(html,'EVENTS',ucdp.embedded_json(events))
        html=gdelt.replace_json(html,'GDELT_EVENTS',gdelt.embedded_json(news))
        self.assertEqual(gdelt.parse_array(html,'EVENTS'),events)
        self.assertEqual(builder.parse_array(html,'GDELT_EVENTS'),news)
        self.assertEqual(html.count('</script>'),1)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);scripts=root/'scripts';scripts.mkdir();(root/'Published').mkdir()
            page=root/'Nigeria_Events_Map.html';page.write_text(html)
            (scripts/'.gdelt_state.json').write_text(json.dumps({'ingested':[yesterday.strftime('%Y%m%d')],'missing':[]}))
            with patch.object(renderer,'HTML_PATH',page),patch.object(renderer,'SCRIPT_DIR',scripts),patch.object(renderer,'load_mosaic',return_value=None),patch('urllib.request.urlopen',side_effect=AssertionError('No network allowed')):
                try:renderer.main()
                finally:renderer.plt.close('all')
            with patch.object(validator,'PROJECT',root),patch.object(validator,'ROOT',scripts):
                arrays=validator.validate()
                self.assertEqual(arrays['EVENTS'][0]['headline'],text_value)
                self.assertEqual(arrays['GDELT_EVENTS'][0]['headline'],text_value)
                with patch.object(heat,'validate',validator.validate),patch.object(heat,'__file__',str(scripts/'refresh_sentiment_heat.py')):heat.main()
            result=json.loads((root/'Published/sentiment-gdelt.json').read_text())
            self.assertEqual(result['raw'],101)
            self.assertEqual(result['from'],yesterday.isoformat())
            self.assertEqual(result['to'],yesterday.isoformat())
            builder.validate_png(root/'Nigeria_Events_Map_LastWeek_latest.png',page)

if __name__=='__main__':unittest.main()
