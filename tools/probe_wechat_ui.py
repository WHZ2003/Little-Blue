"""Read-only targeted UIA probe. Prints structure, never chat text or credentials."""
import argparse
import json
from pywinauto import Desktop
import psutil

parser=argparse.ArgumentParser()
parser.add_argument('--handle',type=int,required=True)
args=parser.parse_args()
w=Desktop(backend='uia').window(handle=args.handle)
p=psutil.Process(w.process_id())
assert p.name().lower()=='weixin.exe'
print(json.dumps({'pid':p.pid,'process_started':p.create_time(),'main_window':args.handle}))
lists=[x for x in w.descendants(control_type='List') if x.element_info.automation_id=='chat_message_list']
assert len(lists)==1, 'No unique message list'
for r in lists[0].children()[-8:]:
    info=r.element_info
    try:
        legacy=r.legacy_properties()
    except Exception:
        legacy={}
    # Only structural evidence; text lengths/mention code points can diagnose API differences.
    name=r.window_text()
    properties={}
    for label, number in [('help',30013),('item_status',30026),('aria',30102),('full_description',30159),('value',30045)]:
        try:
            properties[label]=len(str(info.element.GetCurrentPropertyValue(number) or ''))
        except Exception:
            properties[label]=None
    print(json.dumps({'type':info.control_type,'class':r.class_name(),'automation_id':info.automation_id,
        'runtime_id':info.runtime_id,'text_length':len(name),'mention':name.startswith('@'),
        'mention_prefix_codepoints':[ord(c) for c in name[:6]] if name.startswith('@') else [],
        'legacy_keys':list(legacy),'legacy_description_length':len(str(legacy.get('Description',''))), 'property_lengths':properties,
        'children':[{'type':x.element_info.control_type,'class':x.class_name(),
                     'automation_id':x.element_info.automation_id,'text_length':len(x.window_text())}
                    for x in r.descendants()[:20]]},ensure_ascii=True))
